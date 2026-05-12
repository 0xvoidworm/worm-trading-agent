#!/usr/bin/env python3
"""Risk Guard CLI — daily-loss killswitch operator console.

Usage:
    python scripts/risk.py status              # render current daily PnL state
    python scripts/risk.py reset               # remove killswitch (requires confirm)
    python scripts/risk.py history --days 7    # past RISK_* events
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.risk_guard import (
    KILLSWITCH_FILE,
    get_daily_pnl_state,
    is_killswitch_active,
    read_killswitch_reason,
    remove_killswitch,
)
from data_pipe.storage import Storage


# Palette (selaras dashboard/tui.py)
AMBER = "orange1"
AMBER_BG = "black on orange1"
GREEN = "green1"
RED = "bright_red"
YELLOW = "yellow"
WHITE = "grey93"
MUTED = "grey50"

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "pairs.yaml"
DATA_DIR = ROOT / "data"
RISK_LOG = DATA_DIR / "risk.log"


_STATUS_COLOR = {"OK": GREEN, "WARN": YELLOW, "TRIGGER": RED, "ERR": RED}


def _load_cfg() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _append_log(msg: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with RISK_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def cmd_status(args) -> int:
    cfg = _load_cfg()
    console = Console()
    with Storage(read_only=True) as s:
        st = get_daily_pnl_state(s, cfg)

    color = _STATUS_COLOR.get(st["status"], WHITE)
    table = Table(
        title=f"[bold {AMBER}]RISK GUARD · DAILY PNL[/bold {AMBER}]",
        box=box.HEAVY, header_style=AMBER_BG, border_style=AMBER,
        show_edge=True, padding=(0, 1),
    )
    table.add_column("FIELD", style=WHITE, width=22)
    table.add_column("VALUE", style=WHITE, justify="right")

    def add(k, v):
        table.add_row(k, str(v))

    add("day_start_utc", st["day_start_utc"].strftime("%Y-%m-%d %H:%M UTC"))
    add("equity_start", f"{st['equity_start']:,.2f}")
    add("realized_today", f"{st['realized_today']:+,.2f}")
    add("unrealized_now", f"{st['unrealized_now']:+,.2f}")
    add("daily_pnl", f"{st['daily_pnl']:+,.2f}")
    add("daily_pnl_pct", f"[bold {color}]{st['daily_pnl_pct']:+.4f}%[/bold {color}]")
    add("warn_threshold", f"-{st['warn_pct']:.2f}%")
    add("kill_threshold", f"-{st['threshold_pct']:.2f}%")
    add("status", f"[bold {color}]{st['status']}[/bold {color}]")
    add(
        "killswitch_file",
        f"[bold {RED}]{KILLSWITCH_FILE} ENGAGED[/bold {RED}]"
        if is_killswitch_active(DATA_DIR)
        else f"[{MUTED}]not present[/{MUTED}]",
    )
    console.print(table)

    if is_killswitch_active(DATA_DIR):
        reason = read_killswitch_reason(DATA_DIR) or {}
        console.print(
            f"\n[{RED}]Engaged at:[/{RED}] {reason.get('engaged_at', '?')}\n"
            f"[{RED}]Reason:[/{RED}] {reason.get('reason', '?')} "
            f"(daily_pnl_pct={reason.get('daily_pnl_pct')})\n"
            f"[{MUTED}]Run:[/{MUTED}] python scripts/risk.py reset"
        )
    return 0


def cmd_reset(args) -> int:
    console = Console()
    if not is_killswitch_active(DATA_DIR):
        console.print(f"[{MUTED}]No killswitch file present — nothing to reset.[/{MUTED}]")
        return 0

    reason = read_killswitch_reason(DATA_DIR) or {}
    console.print(
        f"[bold {YELLOW}]About to reset risk killswitch.[/bold {YELLOW}]\n"
        f"  engaged_at  : {reason.get('engaged_at')}\n"
        f"  daily_pnl_pct: {reason.get('daily_pnl_pct')}%\n"
        f"  threshold   : -{reason.get('threshold_pct')}%\n"
    )
    if args.yes:
        confirmed = True
    else:
        try:
            entered = input("Type 'RESET' to confirm: ").strip()
        except EOFError:
            entered = ""
        confirmed = entered == "RESET"

    if not confirmed:
        console.print(f"[{RED}]aborted — exact 'RESET' string required.[/{RED}]")
        return 1

    prev = remove_killswitch(DATA_DIR)
    _append_log(
        f"RESET prev_pnl_pct={prev.get('daily_pnl_pct') if prev else '?'} "
        f"prev_engaged_at={prev.get('engaged_at') if prev else '?'}"
    )

    # Emit RISK_RESET signal_event (deterministic id per UTC day)
    try:
        with Storage() as s:
            day_iso = datetime.now(timezone.utc).date().isoformat()
            payload = (
                f"reset by operator; prev_pnl_pct="
                f"{prev.get('daily_pnl_pct') if prev else '?'}"
            )
            seed = f"RISK_RESET|{day_iso}|{payload}"
            sig_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
            ts = datetime.now(timezone.utc).replace(tzinfo=None)
            s.upsert_signals([{
                "id": sig_id, "ts": ts, "event_type": "RISK_RESET",
                "symbol": None, "severity": "info", "payload": payload,
            }])
    except Exception as e:
        console.print(f"[{YELLOW}]warning: signal emit failed: {e}[/{YELLOW}]")

    console.print(f"[bold {GREEN}]killswitch reset OK.[/bold {GREEN}]")
    return 0


def cmd_history(args) -> int:
    console = Console()
    days = max(1, int(args.days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    with Storage(read_only=True) as s:
        rows = s._conn.execute(
            "SELECT sig_num, ts, event_type, severity, payload FROM signal_events "
            "WHERE event_type IN ('RISK_KILLSWITCH','RISK_WARN','RISK_RESET') "
            "  AND ts >= ? "
            "ORDER BY ts DESC",
            [cutoff],
        ).fetchall()

    if not rows:
        console.print(f"[{MUTED}]no risk events in last {days} day(s).[/{MUTED}]")
        return 0

    table = Table(
        title=f"[bold {AMBER}]RISK EVENTS · last {days}d[/bold {AMBER}]",
        box=box.HEAVY, header_style=AMBER_BG, border_style=AMBER,
        padding=(0, 1),
    )
    table.add_column("SIG#", justify="right", style=AMBER, width=6)
    table.add_column("TIME (UTC)", style=MUTED, width=19)
    table.add_column("TYPE", width=18)
    table.add_column("SEV", width=8)
    table.add_column("PAYLOAD", style=WHITE)
    type_color = {
        "RISK_KILLSWITCH": RED,
        "RISK_WARN": YELLOW,
        "RISK_RESET": GREEN,
    }
    sev_color = {"critical": RED, "warn": YELLOW, "info": GREEN}
    for sig_num, ts, et, sev, payload in rows:
        tc = type_color.get(et, WHITE)
        sc = sev_color.get(sev, WHITE)
        table.add_row(
            str(sig_num), str(ts)[:19],
            f"[bold {tc}]{et}[/bold {tc}]",
            f"[{sc}]{sev}[/{sc}]",
            str(payload)[:120],
        )
    console.print(table)
    return 0


def main():
    p = argparse.ArgumentParser(description="Risk guard operator console")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="show current daily PnL state")
    p_status.set_defaults(func=cmd_status)

    p_reset = sub.add_parser("reset", help="remove risk.killswitch (requires confirm)")
    p_reset.add_argument("--yes", action="store_true",
                         help="skip 'RESET' prompt (for automation)")
    p_reset.set_defaults(func=cmd_reset)

    p_hist = sub.add_parser("history", help="past RISK_* signal events")
    p_hist.add_argument("--days", type=int, default=7)
    p_hist.set_defaults(func=cmd_history)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
