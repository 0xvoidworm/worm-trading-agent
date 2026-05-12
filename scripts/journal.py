#!/usr/bin/env python3
"""Manual Trade Journal CLI.

Operasional harian lewat terminal. Integrasi via data_pipe.storage.Storage
(single-writer DuckDB, sama dengan daemon).

Usage:
    python scripts/journal.py add --pair "SOL/USDT" --dir LONG \
        --price 145.2 --size 10 --thesis "Regime shift to trending_up"

    python scripts/journal.py add --pair "SOL/USDT|ETH/USDT" --dir PAIR \
        --thesis "z=+2.58 mean reversion, hl=15h" --size 100

    python scripts/journal.py list
    python scripts/journal.py list --all        # termasuk CLOSED

    python scripts/journal.py close --id 3 --price 148.0 --notes "profit taking"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage


# Bloomberg palette — selaras dengan dashboard/tui.py
AMBER = "orange1"
AMBER_BG = "black on orange1"
DIM_AMBER = "dark_orange3"
GREEN = "green1"
RED = "bright_red"
CYAN_D = "dark_cyan"
WHITE = "grey93"
MUTED = "grey50"

_DIR_COLOR = {"LONG": GREEN, "SHORT": RED, "PAIR": CYAN_D}


def cmd_add(args) -> int:
    try:
        with Storage() as s:
            new_id = s.add_trade(
                pair=args.pair,
                direction=args.dir.upper(),
                entry_price=args.entry,
                size=args.size,
                thesis=args.thesis,
                signal_id=args.signal,
            )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    console = Console()
    console.print(f"[bold {AMBER}][journal] OPEN trade #{new_id}[/bold {AMBER}]")
    console.print(f"  [{MUTED}]pair   [/{MUTED}] {args.pair}")
    console.print(f"  [{MUTED}]dir    [/{MUTED}] {args.dir.upper()}")
    console.print(f"  [{MUTED}]entry  [/{MUTED}] {args.entry if args.entry is not None else '(none)'}")
    console.print(f"  [{MUTED}]size   [/{MUTED}] {args.size if args.size is not None else '(none)'}")
    console.print(f"  [{MUTED}]thesis [/{MUTED}] {args.thesis or '(none)'}")
    console.print(f"  [{MUTED}]signal [/{MUTED}] {f'SIG#{args.signal}' if args.signal is not None else '(none)'}")
    return 0


def cmd_list(args) -> int:
    with Storage(read_only=True) as s:
        df = s.get_trades(status=None if args.all else "OPEN")

    console = Console()
    scope_label = "ALL TRADES" if args.all else "OPEN TRADES"

    if df.empty:
        console.print(f"[{MUTED}](no {scope_label.lower()})[/{MUTED}]")
        return 0

    table = Table(
        title=f"[bold {AMBER}]TRADE JOURNAL — {scope_label}[/bold {AMBER}]",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("ID", justify="right", style=WHITE)
    table.add_column("OPEN_TS", style=MUTED)
    table.add_column("PAIR")
    table.add_column("DIR", justify="center")
    table.add_column("ENTRY", justify="right")
    table.add_column("SIZE", justify="right", style=MUTED)
    table.add_column("STATUS", justify="center")
    table.add_column("EXIT_TS", style=MUTED)
    table.add_column("EXIT", justify="right")
    table.add_column("PNL", justify="right")
    table.add_column("THESIS")

    for _, r in df.iterrows():
        ts = pd.Timestamp(r["ts"]).strftime("%m-%d %H:%M")
        exit_ts = (
            pd.Timestamp(r["exit_ts"]).strftime("%m-%d %H:%M")
            if pd.notna(r["exit_ts"]) else "-"
        )
        entry = f"{r['entry_price']:.4f}" if pd.notna(r["entry_price"]) else "-"
        size = f"{r['size']:.2f}" if pd.notna(r["size"]) else "-"
        exit_px = f"{r['exit_price']:.4f}" if pd.notna(r["exit_price"]) else "-"

        # PnL color: green if profit, red if loss, muted if none
        if pd.notna(r["pnl"]):
            pnl_val = float(r["pnl"])
            pnl_color = GREEN if pnl_val > 0 else RED if pnl_val < 0 else WHITE
            pnl_str = f"[{pnl_color}]{pnl_val:+.2f}[/{pnl_color}]"
        else:
            pnl_str = f"[{MUTED}]-[/{MUTED}]"

        direction = str(r["direction"])
        dcolor = _DIR_COLOR.get(direction, WHITE)
        status = str(r["status"])
        scolor = GREEN if status == "OPEN" else MUTED
        thesis = str(r["thesis"] or "")
        if len(thesis) > 60:
            thesis = thesis[:59] + "…"
        pair_cell = str(r["pair"])[:28]
        if pd.notna(r.get("signal_id")):
            pair_cell = f"{pair_cell} [{DIM_AMBER}][SIG#{int(r['signal_id'])}][/{DIM_AMBER}]"

        table.add_row(
            str(int(r["id"])),
            ts,
            pair_cell,
            f"[{dcolor}]{direction}[/{dcolor}]",
            entry,
            size,
            f"[{scolor}]{status}[/{scolor}]",
            exit_ts,
            exit_px,
            pnl_str,
            thesis,
        )

    console.print(table)
    return 0


def cmd_close(args) -> int:
    try:
        with Storage() as s:
            result = s.close_trade(
                trade_id=args.id,
                exit_price=args.price,
                notes=args.notes,
            )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    console = Console()
    pnl = result["pnl"]
    if pnl is not None:
        pnl_color = GREEN if pnl > 0 else RED if pnl < 0 else WHITE
        pnl_str = f"[{pnl_color}]{pnl:+.2f}[/{pnl_color}]"
    else:
        pnl_str = f"[{MUTED}](manual — PAIR or missing entry/size)[/{MUTED}]"

    console.print(f"[bold {AMBER}][journal] CLOSED trade #{result['id']}[/bold {AMBER}]")
    console.print(f"  [{MUTED}]exit_ts [/{MUTED}] {result['exit_ts']} UTC")
    console.print(f"  [{MUTED}]exit    [/{MUTED}] {args.price if args.price is not None else '(none)'}")
    console.print(f"  [{MUTED}]pnl     [/{MUTED}] {pnl_str}")
    console.print(f"  [{MUTED}]notes   [/{MUTED}] {args.notes or '(none)'}")
    return 0


def cmd_stats(args) -> int:
    with Storage(read_only=True) as s:
        st = s.journal_stats()

    console = Console()
    # Headline panel
    wr = st["win_rate"]
    total = st["total_pnl"]
    total_color = GREEN if total > 0 else RED if total < 0 else WHITE
    wr_color = GREEN if wr >= 0.5 else (CYAN_D if wr >= 0.4 else RED)

    table = Table(
        title=f"[bold {AMBER}]TRADE JOURNAL — PERFORMANCE STATS[/bold {AMBER}]",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("METRIC", style=WHITE)
    table.add_column("VALUE", justify="right")

    table.add_row("OPEN TRADES", f"[{WHITE}]{st['open']}[/{WHITE}]")
    table.add_row("CLOSED (SCORED)", f"[{WHITE}]{st['scored']}[/{WHITE}]")
    if st["closed_no_pnl"]:
        table.add_row(
            f"[{MUTED}]· closed w/o pnl (PAIR/manual)[/{MUTED}]",
            f"[{MUTED}]{st['closed_no_pnl']}[/{MUTED}]",
        )
    table.add_row("WINS / LOSSES", f"[{GREEN}]{st['wins']}[/{GREEN}] / [{RED}]{st['losses']}[/{RED}]")
    table.add_row(
        "WIN RATE",
        f"[bold {wr_color}]{wr*100:.1f}%[/bold {wr_color}]"
        if st["scored"] else f"[{MUTED}]—[/{MUTED}]",
    )
    table.add_row(
        "TOTAL PNL (NET)",
        f"[bold {total_color}]{total:+,.2f}[/bold {total_color}]"
        if st["scored"] else f"[{MUTED}]—[/{MUTED}]",
    )
    table.add_row(
        "AVG PNL / TRADE",
        f"[{total_color}]{st['avg_pnl']:+,.2f}[/{total_color}]"
        if st["scored"] else f"[{MUTED}]—[/{MUTED}]",
    )

    def _fmt_t(t):
        if t is None:
            return f"[{MUTED}]—[/{MUTED}]"
        color = GREEN if t["pnl"] > 0 else RED
        dcol = _DIR_COLOR.get(t["direction"], WHITE)
        return (
            f"#{t['id']} [{dcol}]{t['direction']}[/{dcol}] "
            f"[{WHITE}]{t['pair']}[/{WHITE}]  "
            f"[bold {color}]{t['pnl']:+,.2f}[/bold {color}]"
        )

    table.add_row("BEST TRADE", _fmt_t(st["best"]))
    table.add_row("WORST TRADE", _fmt_t(st["worst"]))

    console.print(table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manual trade journal (DuckDB-backed)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Buka trade baru (status OPEN)")
    p_add.add_argument("--pair", required=True, help="e.g. SOL/USDT atau SOL/USDT|ETH/USDT")
    p_add.add_argument("--dir", required=True, help="LONG | SHORT | PAIR")
    # --price alias untuk --entry: --price lebih natural di spec user, --entry eksplisit.
    p_add.add_argument(
        "--entry", "--price", dest="entry", type=float, default=None,
        help="entry price (optional, skip untuk PAIR)",
    )
    p_add.add_argument("--size", type=float, default=None, help="ukuran posisi (notional/unit)")
    p_add.add_argument("--thesis", type=str, default=None, help="alasan trade / ref sinyal")
    p_add.add_argument(
        "--signal", type=int, default=None,
        help="sig_num dari signal_events (lihat Panel [5] TUI atau `signals.py`). Link trade ke sinyal pemicu.",
    )
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="Tampilkan trade (Rich table)")
    p_list.add_argument("--all", action="store_true", help="termasuk yang sudah CLOSED")
    p_list.set_defaults(func=cmd_list)

    p_close = sub.add_parser("close", help="Tutup trade (status CLOSED)")
    p_close.add_argument("--id", type=int, required=True)
    p_close.add_argument("--price", type=float, default=None, help="exit price")
    p_close.add_argument("--notes", type=str, default=None, help="postmortem / catatan")
    p_close.set_defaults(func=cmd_close)

    p_stats = sub.add_parser("stats", help="Performance stats (win rate, PnL, best/worst)")
    p_stats.set_defaults(func=cmd_stats)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
