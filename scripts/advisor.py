#!/usr/bin/env python3
"""Advisor Layer CLI — Phase 1.

Alur standar:
    1. python scripts/advisor.py brief        # health gate + dump konteks
       → hasil di stdout (Bloomberg TUI) + data/advisor.context.json
    2. [Claude/LLM] baca konteks, hasilkan verdict JSON
    3. python scripts/advisor.py decide --verdict verdict.json
       → render verdict + persist data/advisor.latest.json + append advisor.log

Advisor refuse untuk decide kalau health.status != OK. Sesuai prinsip:
  "Data basi, gwe nggak mau ngomong."

Verdict JSON schema (minimal):
    {"summary": "Bullish|Bearish|Chaos",
     "verdict": "WAIT|ENTRY|EXIT",
     "thesis": "<=200 chars",
     "trade_id": 7,          # optional, wajib untuk EXIT
     "proposed": {...}       # optional, untuk ENTRY
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.advisor import (
    apply_verdict,
    build_context,
    validate_verdict,
)
from data_pipe.storage import Storage


# Bloomberg palette — selaras scripts/{journal,signals,health}.py
AMBER = "orange1"
AMBER_BG = "black on orange1"
DIM_AMBER = "dark_orange3"
GREEN = "green1"
YELLOW = "yellow"
RED = "bright_red"
CYAN_D = "dark_cyan"
WHITE = "grey93"
MUTED = "grey50"

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_VERDICT_COLOR = {"WAIT": MUTED, "ENTRY": GREEN, "EXIT": YELLOW}
_SUMMARY_COLOR = {"Bullish": GREEN, "Bearish": RED, "Chaos": YELLOW}
_DIR_COLOR = {"LONG": GREEN, "SHORT": RED, "PAIR": CYAN_D}
_HEALTH_COLOR = {"OK": GREEN, "GAP": YELLOW, "STALE": RED}


# ─── RENDER ────────────────────────────────────────────────────────────────

def _render_health_line(console: Console, health: dict) -> None:
    status = health.get("status", "?")
    color = _HEALTH_COLOR.get(status, MUTED)
    console.print(
        f"[bold {AMBER}]HEALTH GATE[/bold {AMBER}]  "
        f"[bold {color}]● {status}[/bold {color}]  "
        f"[{WHITE}]{health.get('summary', '')}[/{WHITE}]"
    )


def _render_signals(console: Console, signals: list[dict]) -> None:
    if not signals:
        console.print(f"[{MUTED}](no recent signals)[/{MUTED}]")
        return
    t = Table(
        title=f"[bold {AMBER}]SIGNALS — 3 TERBARU[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("SIG#", justify="right", style=WHITE)
    t.add_column("TS", style=MUTED)
    t.add_column("SEV", justify="center")
    t.add_column("TYPE")
    t.add_column("SYMBOL")
    t.add_column("PAYLOAD")
    for s in signals:
        sn = f"#{int(s['sig_num'])}" if s.get("sig_num") is not None else "-"
        ts = pd.Timestamp(s["ts"]).strftime("%m-%d %H:%M")
        sev = str(s.get("severity") or "info")
        sev_color = RED if sev == "warn" else YELLOW if sev == "alert" else WHITE
        payload = str(s.get("payload") or "")
        if len(payload) > 60:
            payload = payload[:59] + "…"
        t.add_row(
            sn, ts,
            f"[{sev_color}]{sev.upper()}[/{sev_color}]",
            f"[{AMBER}]{s.get('event_type', '-')}[/{AMBER}]",
            str(s.get("symbol") or "-"),
            payload,
        )
    console.print(t)


def _render_open_trades(console: Console, trades: list[dict]) -> None:
    if not trades:
        console.print(f"[{MUTED}](no open trades)[/{MUTED}]")
        return
    t = Table(
        title=f"[bold {AMBER}]OPEN TRADES[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("ID", justify="right", style=WHITE)
    t.add_column("PAIR")
    t.add_column("DIR", justify="center")
    t.add_column("ENTRY", justify="right")
    t.add_column("SIZE", justify="right", style=MUTED)
    t.add_column("SIG#", justify="right", style=DIM_AMBER)
    t.add_column("THESIS")
    for tr in trades:
        entry = f"{tr['entry_price']:.4f}" if tr.get("entry_price") is not None else "-"
        size = f"{tr['size']:.2f}" if tr.get("size") is not None else "-"
        direction = str(tr.get("direction") or "")
        dcol = _DIR_COLOR.get(direction, WHITE)
        sig_id = f"#{int(tr['signal_id'])}" if tr.get("signal_id") is not None else "-"
        thesis = str(tr.get("thesis") or "")
        if len(thesis) > 50:
            thesis = thesis[:49] + "…"
        t.add_row(
            str(int(tr["id"])), str(tr.get("pair", "-"))[:24],
            f"[{dcol}]{direction}[/{dcol}]",
            entry, size, sig_id, thesis,
        )
    console.print(t)


def _render_news(console: Console, news: list[dict], symbols_of_interest: list[str]) -> None:
    if not news:
        console.print(f"[{MUTED}](no relevant news)[/{MUTED}]")
        return
    hint = f" — filter: {', '.join(symbols_of_interest)}" if symbols_of_interest else " — fallback: recent"
    t = Table(
        title=f"[bold {AMBER}]NEWS (5 RELEVAN){hint}[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("PUB", style=MUTED)
    t.add_column("SRC", style=DIM_AMBER)
    t.add_column("SYM", style=CYAN_D)
    t.add_column("TITLE")
    for n in news:
        pub = pd.Timestamp(n["published_at"]).strftime("%m-%d %H:%M")
        title = str(n.get("title") or "")
        if len(title) > 72:
            title = title[:71] + "…"
        t.add_row(
            pub, str(n.get("source") or "-")[:14],
            str(n.get("symbols") or "-")[:14], title,
        )
    console.print(t)


def _render_verdict_panel(console: Console, verdict: dict) -> None:
    summary = verdict.get("summary", "?")
    v = verdict.get("verdict", "?")
    scol = _SUMMARY_COLOR.get(summary, WHITE)
    vcol = _VERDICT_COLOR.get(v, WHITE)
    t = Table(
        title=f"[bold {AMBER}]ADVISOR VERDICT[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("FIELD", style=WHITE)
    t.add_column("VALUE")
    t.add_row("SUMMARY", f"[bold {scol}]{summary.upper()}[/bold {scol}]")
    t.add_row("VERDICT", f"[bold {vcol}]● {v}[/bold {vcol}]")
    t.add_row("THESIS", f"[{WHITE}]{verdict.get('thesis', '')}[/{WHITE}]")
    if "trade_id" in verdict:
        t.add_row("TRADE ID", f"[{YELLOW}]#{verdict['trade_id']}[/{YELLOW}]")
    if "proposed" in verdict and verdict["proposed"]:
        p = verdict["proposed"]
        d = str(p.get("direction", ""))
        dcol = _DIR_COLOR.get(d, WHITE)
        entry = p.get("entry_price")
        entry_s = f"@ {entry}" if entry is not None else ""
        size = p.get("size_hint")
        size_s = f"  size≈{size}" if size is not None else ""
        sig = p.get("signal_id")
        sig_s = f"  SIG#{sig}" if sig is not None else ""
        t.add_row(
            "PROPOSED",
            f"[{dcol}]{d}[/{dcol}] [{WHITE}]{p.get('pair', '-')}[/{WHITE}] "
            f"{entry_s}{size_s}{sig_s}",
        )
    console.print(t)


# ─── SUBCOMMANDS ───────────────────────────────────────────────────────────

def cmd_brief(args) -> int:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    with Storage(read_only=True) as s:
        ctx = build_context(s, cfg)

    console = Console()
    _render_health_line(console, ctx["health"])

    if ctx["health"]["status"] != "OK" and not args.force:
        console.print(
            f"[bold {RED}]STOP.[/bold {RED}] "
            f"[{WHITE}]Data basi, gwe nggak mau ngomong.[/{WHITE}]  "
            f"[{MUTED}](jalankan ulang saat daemon sehat, atau --force untuk bypass)[/{MUTED}]"
        )
        _persist_context_dump(ctx)
        return 2

    console.print()
    _render_signals(console, ctx["signals"])
    console.print()
    _render_open_trades(console, ctx["open_trades"])
    console.print()
    _render_news(console, ctx["news"], ctx["symbols_of_interest"])
    console.print()

    dump = _persist_context_dump(ctx)
    console.print(
        f"[{MUTED}]context dumped → {dump}  "
        f"(feed ke LLM, lalu: advisor.py decide --verdict <file>.json)[/{MUTED}]"
    )
    return 0


def cmd_decide(args) -> int:
    verdict = json.loads(Path(args.verdict).read_text())
    try:
        validate_verdict(verdict)
    except ValueError as e:
        print(f"ERROR: invalid verdict — {e}", file=sys.stderr)
        return 2

    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    with Storage(read_only=True) as s:
        ctx = build_context(s, cfg)

    console = Console()
    _render_health_line(console, ctx["health"])

    if ctx["health"]["status"] != "OK" and not args.force:
        console.print(
            f"[bold {RED}]STOP.[/bold {RED}] "
            f"[{WHITE}]Data basi, verdict tidak diapply.[/{WHITE}]  "
            f"[{MUTED}](gunakan --force untuk bypass)[/{MUTED}]"
        )
        return 2

    console.print()
    _render_verdict_panel(console, verdict)
    console.print()

    latest = apply_verdict(verdict, ctx, _DATA_DIR)
    console.print(f"[{MUTED}]persisted → {latest}[/{MUTED}]")
    return 0


def cmd_show(args) -> int:
    """Tampilkan verdict terakhir yang tersimpan."""
    p = _DATA_DIR / "advisor.latest.json"
    if not p.exists():
        print("no verdict yet — run `advisor.py brief` then `decide`")
        return 1
    payload = json.loads(p.read_text())
    console = Console()
    ts = payload.get("generated_at", "?")
    console.print(f"[{MUTED}]generated_at: {ts}[/{MUTED}]")
    _render_verdict_panel(console, payload["verdict"])
    return 0


# ─── helpers ──────────────────────────────────────────────────────────────

def _persist_context_dump(ctx: dict) -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = _DATA_DIR / "advisor.context.json"
    out.write_text(json.dumps(ctx, indent=2, default=str))
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trading advisor CLI (Phase 1 — LLM in the loop)")
    sub = p.add_subparsers(dest="cmd")

    pb = sub.add_parser("brief", help="Health gate + context briefing for LLM")
    pb.add_argument("--force", action="store_true",
                    help="lanjut meski health bukan OK (dev only)")
    pb.set_defaults(func=cmd_brief)

    pd_ = sub.add_parser("decide", help="Ingest verdict JSON dari LLM, render + persist")
    pd_.add_argument("--verdict", required=True, help="path ke verdict JSON")
    pd_.add_argument("--force", action="store_true", help="bypass health gate")
    pd_.set_defaults(func=cmd_decide)

    ps = sub.add_parser("show", help="Tampilkan verdict terakhir yang tersimpan")
    ps.set_defaults(func=cmd_show)

    return p


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    if not argv:
        argv = ["brief"]
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
