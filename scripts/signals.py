#!/usr/bin/env python3
"""Signal events CLI.

Subcommands:
    list        tampilkan daftar sinyal (default jika tanpa subcommand)
    show        detail satu sinyal: price context, linked trades, news

Usage:
    python scripts/signals.py                       # last 50 events (list)
    python scripts/signals.py list --limit 200
    python scripts/signals.py list --type REGIME_CHANGE
    python scripts/signals.py list --symbol BTC/USDT
    python scripts/signals.py list --since 24       # last 24 hours

    python scripts/signals.py show 385              # retrospective audit
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

from dashboard.sparkline import sparkline
from data_pipe.storage import Storage


# Bloomberg palette — selaras dengan scripts/journal.py + dashboard/tui.py
AMBER = "orange1"
AMBER_BG = "black on orange1"
DIM_AMBER = "dark_orange3"
GREEN = "green1"
RED = "bright_red"
CYAN_D = "dark_cyan"
WHITE = "grey93"
MUTED = "grey50"

_DIR_COLOR = {"LONG": GREEN, "SHORT": RED, "PAIR": CYAN_D}

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"


def _load_defaults() -> tuple[str, str]:
    """Ambil default exchange + timeframe dari config/pairs.yaml."""
    try:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text())
        return cfg.get("exchange", "bybit"), cfg.get("timeframe", "1h")
    except Exception:
        return "bybit", "1h"


def cmd_list(args) -> int:
    with Storage(read_only=True) as s:
        df = s.get_signals(
            limit=args.limit,
            event_type=args.event_type,
            symbol=args.symbol,
            since_hours=args.since,
        )

    if df.empty:
        print("no events matching filter")
        return 0

    # Sort oldest-first untuk readability (paling baru di bawah)
    df = df.iloc[::-1].reset_index(drop=True)
    for _, row in df.iterrows():
        ts = pd.Timestamp(row["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        sig_n = f"#{int(row['sig_num'])}" if pd.notna(row.get("sig_num")) else "#-"
        print(
            f"{sig_n:>5}  {ts}  {row['severity']:8}  {row['event_type']:16}  "
            f"{str(row['symbol'] or '-'):14}  {row['payload']}"
        )
    print(f"\n{len(df)} events")
    return 0


def _first_leg(symbol: str | None) -> tuple[str | None, bool]:
    """Untuk Z_SPIKE pair 'SOL/USDT|ETH/USDT' → ('SOL/USDT', True).
    Return (leg_symbol, is_pair)."""
    if not symbol:
        return None, False
    if "|" in symbol:
        return symbol.split("|", 1)[0].strip(), True
    return symbol, False


def _render_signal_header(console: Console, sig: dict) -> None:
    ts = pd.Timestamp(sig["ts"]).strftime("%Y-%m-%d %H:%M:%S UTC")
    sev = str(sig["severity"] or "info")
    sev_color = RED if sev == "warn" else (AMBER if sev == "alert" else WHITE)

    t = Table(
        title=f"[bold {AMBER}]SIGNAL #{int(sig['sig_num'])} — DETAIL[/bold {AMBER}]",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column("FIELD", style=WHITE)
    t.add_column("VALUE")
    t.add_row("TYPE", f"[{AMBER}]{sig['event_type']}[/{AMBER}]")
    t.add_row("TIMESTAMP", f"[{WHITE}]{ts}[/{WHITE}]")
    t.add_row("SYMBOL", f"[{WHITE}]{sig['symbol'] or '-'}[/{WHITE}]")
    t.add_row("SEVERITY", f"[{sev_color}]{sev.upper()}[/{sev_color}]")
    t.add_row("PAYLOAD", f"[{WHITE}]{sig['payload'] or ''}[/{WHITE}]")
    console.print(t)


def _render_price_context(
    console: Console,
    storage: Storage,
    symbol: str | None,
    center_ts,
    exchange: str,
    timeframe: str,
    pair_note: bool,
) -> None:
    if not symbol:
        console.print(f"[{MUTED}](price context: symbol tidak tersedia di signal)[/{MUTED}]")
        return

    ctx = storage.get_ohlcv_around(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        center_ts=center_ts,
        before=12,
        after=12,
    )
    if ctx["signal"] is None:
        console.print(f"[{MUTED}](price context: no OHLCV for {symbol} {timeframe} @ {exchange})[/{MUTED}]")
        return

    # Gabung series closes utk sparkline
    before_df = ctx["before"]
    after_df = ctx["after"]
    sig_bar = ctx["signal"]
    closes = (
        before_df["close"].tolist()
        + [sig_bar["close"]]
        + after_df["close"].tolist()
    )
    spark = sparkline(closes) if closes else ""

    sig_close = float(sig_bar["close"])
    first_close = float(closes[0])
    last_close = float(closes[-1])
    pct_before = (sig_close - first_close) / first_close * 100 if first_close else 0.0
    pct_after = (last_close - sig_close) / sig_close * 100 if sig_close else 0.0

    pct_b_color = GREEN if pct_before > 0 else RED if pct_before < 0 else WHITE
    pct_a_color = GREEN if pct_after > 0 else RED if pct_after < 0 else WHITE

    title = f"[bold {AMBER}]PRICE CONTEXT — {symbol} {timeframe} @ {exchange}[/bold {AMBER}]"
    if pair_note:
        title += f" [{MUTED}](pair signal — showing first leg only)[/{MUTED}]"

    t = Table(
        title=title,
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column("FIELD", style=WHITE)
    t.add_column("VALUE", justify="right")
    t.add_row("BARS", f"{len(before_df)} before + 1 signal + {len(after_df)} after")
    t.add_row("SIGNAL BAR TS", pd.Timestamp(sig_bar["ts"]).strftime("%Y-%m-%d %H:%M UTC"))
    t.add_row("SIGNAL CLOSE", f"[{AMBER}]{sig_close:,.4f}[/{AMBER}]")
    t.add_row(
        "PRE-MOVE  (12→signal)",
        f"[{pct_b_color}]{pct_before:+.2f}%[/{pct_b_color}]  "
        f"({first_close:,.4f} → {sig_close:,.4f})",
    )
    t.add_row(
        "POST-MOVE (signal→12)",
        f"[{pct_a_color}]{pct_after:+.2f}%[/{pct_a_color}]  "
        f"({sig_close:,.4f} → {last_close:,.4f})",
    )
    t.add_row("SPARKLINE", f"[{AMBER}]{spark}[/{AMBER}]")
    console.print(t)


def _render_linked_trades(console: Console, storage: Storage, sig_num: int) -> None:
    df = storage.get_trades_by_signal(sig_num)
    if df.empty:
        console.print(f"[{MUTED}](no trades linked to SIG#{sig_num})[/{MUTED}]")
        return

    t = Table(
        title=f"[bold {AMBER}]LINKED TRADES — SIG#{sig_num}[/bold {AMBER}]",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column("ID", justify="right", style=WHITE)
    t.add_column("OPEN_TS", style=MUTED)
    t.add_column("PAIR")
    t.add_column("DIR", justify="center")
    t.add_column("ENTRY", justify="right")
    t.add_column("SIZE", justify="right", style=MUTED)
    t.add_column("STATUS", justify="center")
    t.add_column("EXIT", justify="right")
    t.add_column("PNL", justify="right")
    t.add_column("THESIS")

    for _, r in df.iterrows():
        ts = pd.Timestamp(r["ts"]).strftime("%m-%d %H:%M")
        entry = f"{r['entry_price']:.4f}" if pd.notna(r["entry_price"]) else "-"
        size = f"{r['size']:.2f}" if pd.notna(r["size"]) else "-"
        exit_px = f"{r['exit_price']:.4f}" if pd.notna(r["exit_price"]) else "-"

        if pd.notna(r["pnl"]):
            pnl_val = float(r["pnl"])
            pc = GREEN if pnl_val > 0 else RED if pnl_val < 0 else WHITE
            pnl_str = f"[{pc}]{pnl_val:+.2f}[/{pc}]"
        else:
            pnl_str = f"[{MUTED}]-[/{MUTED}]"

        direction = str(r["direction"])
        dcol = _DIR_COLOR.get(direction, WHITE)
        status = str(r["status"])
        scol = GREEN if status == "OPEN" else MUTED
        thesis = str(r["thesis"] or "")
        if len(thesis) > 50:
            thesis = thesis[:49] + "…"

        t.add_row(
            str(int(r["id"])),
            ts,
            str(r["pair"])[:28],
            f"[{dcol}]{direction}[/{dcol}]",
            entry,
            size,
            f"[{scol}]{status}[/{scol}]",
            exit_px,
            pnl_str,
            thesis,
        )
    console.print(t)


def _render_news(console: Console, storage: Storage, center_ts) -> None:
    df = storage.get_news_in_window(center_ts, before_hours=3, after_hours=3)
    if df.empty:
        console.print(f"[{MUTED}](no news in ±3h window — signal kemungkinan murni teknikal)[/{MUTED}]")
        return

    t = Table(
        title=f"[bold {AMBER}]NEWS CONTEXT — ±3h WINDOW[/bold {AMBER}]",
        title_justify="left",
        box=box.SIMPLE_HEAVY,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column("PUBLISHED", style=MUTED)
    t.add_column("Δh", justify="right", style=MUTED)
    t.add_column("SOURCE", style=DIM_AMBER)
    t.add_column("SYMBOLS", style=CYAN_D)
    t.add_column("TITLE")

    center = pd.Timestamp(center_ts)
    for _, r in df.iterrows():
        pub = pd.Timestamp(r["published_at"])
        delta_h = (pub - center).total_seconds() / 3600
        delta_str = f"{delta_h:+.1f}"
        title = str(r["title"] or "")
        if len(title) > 72:
            title = title[:71] + "…"
        t.add_row(
            pub.strftime("%m-%d %H:%M"),
            delta_str,
            str(r["source"] or "-")[:14],
            str(r["symbols"] or "-")[:20],
            title,
        )
    console.print(t)


def cmd_show(args) -> int:
    exchange, timeframe = _load_defaults()
    console = Console()

    with Storage(read_only=True) as s:
        sig = s.get_signal_by_num(args.sig_num)
        if sig is None:
            console.print(f"[{RED}]SIG#{args.sig_num} not found[/{RED}]")
            return 1

        _render_signal_header(console, sig)
        console.print()

        leg, is_pair = _first_leg(sig["symbol"])
        _render_price_context(
            console, s, leg, sig["ts"], exchange, timeframe, pair_note=is_pair,
        )
        console.print()

        _render_linked_trades(console, s, int(sig["sig_num"]))
        console.print()

        _render_news(console, s, sig["ts"])

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Signal events CLI")
    sub = p.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List signal events (default)")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument(
        "--type", dest="event_type", default=None,
        help="REGIME_CHANGE | FUNDING_EXTREME | FUNDING_FLIP | Z_SPIKE",
    )
    p_list.add_argument("--symbol", default=None)
    p_list.add_argument("--since", type=int, default=None, help="hours back from now")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Retrospective audit satu signal (by sig_num)")
    p_show.add_argument("sig_num", type=int, help="surrogate signal number (lihat list output)")
    p_show.set_defaults(func=cmd_show)

    return p


def main() -> int:
    parser = build_parser()
    argv = sys.argv[1:]
    # Backward compat: jika tanpa subcommand, anggap 'list' (dan teruskan semua flag).
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["list"] + argv
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
