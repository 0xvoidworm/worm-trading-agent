#!/usr/bin/env python3
"""Pilot Executor CLI — manual tick atau dipanggil daemon.

Usage:
    python scripts/executor.py                # satu tick, print hasil
    python scripts/executor.py --dry-run      # skip add_trade, report only
    python scripts/executor.py --disable      # pasang kill switch
    python scripts/executor.py --enable       # lepas kill switch
    python scripts/executor.py status         # tampilkan last execution

Exit codes:
    0 — SKIP (normal, no action)
    1 — OPEN (trade dibuka)
    2 — ERROR
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.executor import (
    KILL_SWITCH, read_latest_execution, run_executor,
)
from data_pipe.storage import Storage


AMBER = "orange1"
AMBER_BG = "black on orange1"
DIM_AMBER = "dark_orange3"
GREEN = "green1"
YELLOW = "yellow"
RED = "bright_red"
WHITE = "grey93"
MUTED = "grey50"

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _render_result(console: Console, result: dict) -> None:
    action = result.get("action", "?")
    color = GREEN if action == "OPEN" else MUTED
    t = Table(
        title=f"[bold {AMBER}]EXECUTOR TICK[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("FIELD", style=WHITE)
    t.add_column("VALUE")
    t.add_row("TIMESTAMP", result.get("timestamp", "?"))
    t.add_row("ACTION", f"[bold {color}]● {action}[/bold {color}]")
    t.add_row("REASON", f"[{WHITE}]{result.get('reason', '')}[/{WHITE}]")
    if result.get("trade_id") is not None:
        t.add_row("TRADE ID", f"[{YELLOW}]#{result['trade_id']}[/{YELLOW}]")
    if result.get("signal_id") is not None:
        t.add_row("SIG#", f"[{DIM_AMBER}]#{result['signal_id']}[/{DIM_AMBER}]")
    conf = result.get("confidence")
    if isinstance(conf, (int, float)):
        t.add_row("CONFIDENCE", f"[{WHITE}]{conf:.2f}[/{WHITE}]")
    console.print(t)


def cmd_tick(args) -> int:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())

    if args.dry_run:
        # Cara paling sederhana: write temporary kill switch, restore, but
        # itu mengubah state. Lebih baik: jalankan read-only dan simulate.
        # MVP: implement dry-run dengan fail-fast kalau akan OPEN.
        from data_pipe.advisor import read_latest_verdict
        from data_pipe.health import run_health_check
        with Storage(read_only=True) as s:
            payload = read_latest_verdict(_DATA_DIR)
            health = run_health_check(s, cfg)
        console = Console()
        console.print(f"[{MUTED}]DRY RUN — no state change[/{MUTED}]")
        console.print(f"health: {health['status']}  · verdict: "
                      f"{payload['verdict']['verdict'] if payload else '(none)'}")
        return 0

    with Storage() as s:
        result = run_executor(s, cfg, _DATA_DIR)

    console = Console()
    _render_result(console, result)
    return 1 if result["action"] == "OPEN" else 0


def cmd_disable(args) -> int:
    path = _DATA_DIR / KILL_SWITCH
    path.write_text(f"disabled at {datetime.now(timezone.utc).isoformat()}\n")
    print(f"kill switch ON → {path}")
    return 0


def cmd_enable(args) -> int:
    path = _DATA_DIR / KILL_SWITCH
    if path.exists():
        path.unlink()
        print(f"kill switch OFF → removed {path}")
    else:
        print("kill switch already OFF")
    return 0


def cmd_status(args) -> int:
    result = read_latest_execution(_DATA_DIR)
    console = Console()
    if result is None:
        console.print(f"[{MUTED}]no executor run yet[/{MUTED}]")
        return 0
    _render_result(console, result)
    ks = _DATA_DIR / KILL_SWITCH
    if ks.exists():
        console.print(f"[{RED}]kill switch ACTIVE ({ks})[/{RED}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pilot executor — semi-autonomous")
    sub = p.add_subparsers(dest="cmd")

    pt = sub.add_parser("tick", help="run one evaluation tick (default)")
    pt.add_argument("--dry-run", action="store_true",
                    help="jangan ubah state — inspect saja")
    pt.set_defaults(func=cmd_tick)

    pd_ = sub.add_parser("disable", help="pasang kill switch")
    pd_.set_defaults(func=cmd_disable)

    pe = sub.add_parser("enable", help="lepas kill switch")
    pe.set_defaults(func=cmd_enable)

    ps = sub.add_parser("status", help="tampilkan last executor run")
    ps.set_defaults(func=cmd_status)

    return p


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    if not argv:
        argv = ["tick"]
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
