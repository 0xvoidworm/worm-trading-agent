#!/usr/bin/env python3
"""Health check CLI — data freshness, OHLCV gaps, RSS feed liveness.

Usage:
    python scripts/health.py                  # full check + log append
    python scripts/health.py --quiet          # status line only, no tables
    python scripts/health.py --no-log         # skip appending to health.log

Exit codes:
    0 → OK
    1 → GAP   (non-blocking warn)
    2 → STALE (data dead — agent tidak boleh take position)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.health import append_log, run_health_check
from data_pipe.storage import Storage


# Bloomberg palette — selaras dengan signals.py / journal.py
AMBER = "orange1"
AMBER_BG = "black on orange1"
DIM_AMBER = "dark_orange3"
GREEN = "green1"
YELLOW = "yellow"
RED = "bright_red"
WHITE = "grey93"
MUTED = "grey50"

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "health.log"

_STATUS_COLOR = {"OK": GREEN, "GAP": YELLOW, "STALE": RED}
_STATUS_EXIT = {"OK": 0, "GAP": 1, "STALE": 2}


def _render_header(console: Console, report: dict) -> None:
    status = report["status"]
    color = _STATUS_COLOR.get(status, WHITE)
    ts = report["generated_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
    console.print(
        f"[bold {AMBER}]HEALTH CHECK[/bold {AMBER}]  "
        f"[{MUTED}]{ts}[/{MUTED}]  "
        f"[bold {color}]● {status}[/bold {color}]  "
        f"[{WHITE}]{report['summary']}[/{WHITE}]"
    )


def _render_gaps(console: Console, gaps: list[dict]) -> None:
    if not gaps:
        return
    t = Table(
        title=f"[bold {AMBER}]OHLCV GAPS[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("SYMBOL")
    t.add_column("TF", justify="center", style=MUTED)
    t.add_column("GAPS", justify="right")
    t.add_column("MAX GAP (h)", justify="right")
    t.add_column("BARS SEEN", justify="right", style=MUTED)
    for g in gaps:
        t.add_row(
            g["symbol"], g["timeframe"],
            f"[{YELLOW}]{g['gap_count']}[/{YELLOW}]",
            f"{g['max_gap_hours']:.2f}",
            str(g["bar_intervals"]),
        )
    console.print(t)


def _render_stale(console: Console, ohlcv_stale: list[dict], funding_stale: list[dict]) -> None:
    if not ohlcv_stale and not funding_stale:
        return
    t = Table(
        title=f"[bold {AMBER}]STALE DATA[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("KIND", style=MUTED)
    t.add_column("SYMBOL")
    t.add_column("LAST SEEN")
    t.add_column("AGE (h)", justify="right")
    for s in ohlcv_stale:
        last = pd.Timestamp(s["last_ts"]).strftime("%Y-%m-%d %H:%M") if s["last_ts"] else "(none)"
        age = f"{s['age_hours']:.1f}" if s["age_hours"] is not None else "∞"
        t.add_row("ohlcv", s["symbol"], last, f"[{RED}]{age}[/{RED}]")
    for s in funding_stale:
        last = pd.Timestamp(s["last_ts"]).strftime("%Y-%m-%d %H:%M") if s["last_ts"] else "(none)"
        t.add_row("funding", s["symbol"], last, f"[{YELLOW}]{s['age_hours']:.1f}[/{YELLOW}]")
    console.print(t)


def _render_feeds(console: Console, feeds: list[dict]) -> None:
    t = Table(
        title=f"[bold {AMBER}]RSS FEEDS — last 24h[/bold {AMBER}]",
        title_justify="left", box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG, border_style=DIM_AMBER,
        show_edge=False, padding=(0, 1),
    )
    t.add_column("SOURCE")
    t.add_column("ITEMS", justify="right")
    t.add_column("STATUS", justify="center")
    for f in feeds:
        ok = f["ok"]
        color = GREEN if ok else RED
        badge = "LIVE" if ok else "DEAD"
        count_color = WHITE if ok else RED
        t.add_row(
            f["source"],
            f"[{count_color}]{f['count_24h']}[/{count_color}]",
            f"[{color}]● {badge}[/{color}]",
        )
    console.print(t)


def main() -> int:
    parser = argparse.ArgumentParser(description="Data quality health check")
    parser.add_argument("--quiet", action="store_true", help="status line only")
    parser.add_argument("--no-log", action="store_true", help="skip appending to data/health.log")
    args = parser.parse_args()

    cfg = yaml.safe_load(_CONFIG_PATH.read_text())

    with Storage(read_only=True) as s:
        report = run_health_check(s, cfg)

    if not args.no_log:
        append_log(report, _LOG_PATH)

    console = Console()
    _render_header(console, report)
    if not args.quiet:
        console.print()
        _render_gaps(console, report["ohlcv_gaps"])
        if report["ohlcv_gaps"]:
            console.print()
        _render_stale(console, report["ohlcv_stale"], report["funding_stale"])
        if report["ohlcv_stale"] or report["funding_stale"]:
            console.print()
        _render_feeds(console, report["feeds"])

    return _STATUS_EXIT.get(report["status"], 0)


if __name__ == "__main__":
    sys.exit(main())
