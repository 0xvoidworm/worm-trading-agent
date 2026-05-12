#!/usr/bin/env python3
"""Validation v2 — REGIME_UP @ 72h di data 90 hari dengan 3 filter.

Filters:
  1. EXCLUDE_SYMBOLS: drop symbol yang IR-nya negatif parah di test sebelumnya
  2. FUNDING_NEGATIVE_SKIP: skip entry kalau funding < threshold (short squeeze trap)
  3. ARB_MAX_ALLOC: cap kontribusi ARB ke portfolio P&L max 20%

Output: comparison table (RAW vs FILTER_SYM vs FULL_FILTER) + per-symbol +
walk-forward 60/30 + slippage stress.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.regime import classify
from scripts.backtest import DB_PATH, WARMUP_BARS, load_ohlcv, load_funding


HORIZON = 72
COST_BASELINE = 0.0011
COST_STRESS = 0.0018
EXCLUDE_SYMBOLS = {"SOL/USDT"}                 # dropped: IR -0.45 di test 30d
FUNDING_NEG_SKIP_THR = -0.0005                 # -0.05%/8h (≈-55% APR) → skip long
ARB_SYMBOL = "ARB/USDT"
ARB_MAX_ALLOC = 0.20                           # ARB max 20% portfolio contribution


@dataclass
class RegimeUpTrade:
    symbol: str
    entry_ts: pd.Timestamp
    entry_price: float
    gross_ret: float
    funding_at_entry: float | None             # untuk confirmation filter


def last_funding_before(funding_df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Funding rate terakhir sebelum ts. Pakai searchsorted — O(log n)."""
    if funding_df is None or len(funding_df) == 0:
        return None
    arr = funding_df["ts"].values.astype("datetime64[ns]")
    target = np.datetime64(ts.to_datetime64())
    idx = int(np.searchsorted(arr, target, side="right")) - 1
    if idx < 0:
        return None
    return float(funding_df.iloc[idx]["funding"])


def replay_regime_up(
    ohlcv_dict: dict[str, pd.DataFrame],
    funding_dict: dict[str, pd.DataFrame],
) -> list[RegimeUpTrade]:
    trades = []
    for sym, df in ohlcv_dict.items():
        if len(df) < WARMUP_BARS + HORIZON:
            continue
        f_df = funding_dict.get(sym)
        prev_regime = None
        for i in range(WARMUP_BARS - 1, len(df)):
            window = df.iloc[i - WARMUP_BARS + 1 : i + 1]
            snap = classify(window)
            if prev_regime is None:
                prev_regime = snap.regime
                continue
            if snap.regime == prev_regime:
                continue
            if snap.regime == "trending_up" and i + HORIZON < len(df):
                entry = float(df.iloc[i]["close"])
                exitp = float(df.iloc[i + HORIZON]["close"])
                entry_ts = pd.Timestamp(df.iloc[i]["ts"])
                trades.append(RegimeUpTrade(
                    symbol=sym,
                    entry_ts=entry_ts,
                    entry_price=entry,
                    gross_ret=(exitp - entry) / entry,
                    funding_at_entry=last_funding_before(f_df, entry_ts),
                ))
            prev_regime = snap.regime
    return trades


def apply_filters(
    trades: list[RegimeUpTrade],
    exclude: set[str] = frozenset(),
    funding_skip: bool = False,
) -> list[RegimeUpTrade]:
    out = []
    for t in trades:
        if t.symbol in exclude:
            continue
        if funding_skip and t.funding_at_entry is not None \
                and t.funding_at_entry < FUNDING_NEG_SKIP_THR:
            continue
        out.append(t)
    return out


def metrics(rets: np.ndarray) -> dict:
    n = len(rets)
    if n == 0:
        return {"n": 0, "win_pct": 0.0, "avg": 0.0, "med": 0.0, "ir": 0.0,
                "cum": 0.0, "max_dd": 0.0}
    wins = int((rets > 0).sum())
    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    ir = float(rets.mean() / std) if std > 0 else 0.0
    equity = np.cumsum(rets)
    peak = np.maximum.accumulate(equity)
    dd = float((equity - peak).min())
    return {
        "n": n,
        "win_pct": 100 * wins / n,
        "avg": float(rets.mean()) * 100,
        "med": float(np.median(rets)) * 100,
        "ir": ir,
        "cum": float(rets.sum()) * 100,
        "max_dd": dd * 100,
    }


def portfolio_weighted_metrics(trades: list[RegimeUpTrade], cost: float,
                               arb_cap: float = ARB_MAX_ALLOC) -> dict:
    """Portfolio-level CUM dengan ARB capped at arb_cap proportional weight.

    Method: setiap trade diberi weight sebesar 1/N_non_arb untuk non-ARB trades,
    dan cap_weight untuk ARB trades, lalu di-normalize ulang agar total=1.
    CUM = sum(weight_i * (gross_i - cost)).
    """
    if not trades:
        return metrics(np.array([]))
    arb_trades = [t for t in trades if t.symbol == ARB_SYMBOL]
    other_trades = [t for t in trades if t.symbol != ARB_SYMBOL]
    if not arb_trades or not other_trades:
        # degenerate — fallback equal-weight
        rets = np.array([t.gross_ret - cost for t in trades])
        return metrics(rets)

    # Raw weights: total = 1. ARB share capped at arb_cap.
    w_arb_each = arb_cap / len(arb_trades)
    w_other_each = (1 - arb_cap) / len(other_trades)

    weighted_rets = []
    for t in trades:
        r = t.gross_ret - cost
        w = w_arb_each if t.symbol == ARB_SYMBOL else w_other_each
        # Normalize so each trade contributes with scale (N_total * weight)
        # agar scale sebanding dengan equal-weighted N-trade portfolio
        weighted_rets.append(r * w * len(trades))

    arr = np.array(weighted_rets)
    return metrics(arr)


def color(value: float, good_thr: float, bad_thr: float, fmt: str = "{:+.2f}") -> str:
    s = fmt.format(value)
    if value >= good_thr:
        return f"[bold green]{s}[/bold green]"
    if value < bad_thr:
        return f"[red]{s}[/red]"
    return s


def render_scenario_compare(scenarios: list[tuple[str, list[RegimeUpTrade]]],
                            cost: float, console: Console):
    console.rule("[bold orange1]SCENARIO COMPARISON · REGIME_UP @ 72h · 90D DATA[/bold orange1]",
                 style="orange1")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SCENARIO", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "SCENARIO" else "right")
    for label, trades in scenarios:
        rets = np.array([t.gross_ret - cost for t in trades])
        m = metrics(rets)
        table.add_row(
            label,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["med"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)


def render_per_symbol(trades: list[RegimeUpTrade], cost: float, console: Console, label: str):
    console.rule(f"[bold orange1]PER-SYMBOL · {label}[/bold orange1]", style="orange1")
    groups: dict[str, list[float]] = {}
    for t in trades:
        groups.setdefault(t.symbol, []).append(t.gross_ret - cost)
    per_symbol = {sym: metrics(np.array(rs)) for sym, rs in groups.items()}
    total_abs = sum(abs(m["cum"]) for m in per_symbol.values()) or 1.0

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SYMBOL", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%", "SHARE"):
        table.add_column(c, justify="left" if c == "SYMBOL" else "right")
    for sym in sorted(per_symbol.keys()):
        m = per_symbol[sym]
        share = 100 * abs(m["cum"]) / total_abs
        table.add_row(
            sym,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
            f"{share:.1f}%",
        )
    console.print(table)
    return per_symbol


def render_walk_forward(trades: list[RegimeUpTrade], cost: float, console: Console):
    """3-way split: 60d train / 30d test (on 90d data)."""
    console.rule("[bold orange1]WALK-FORWARD · 60D TRAIN / 30D TEST[/bold orange1]", style="orange1")
    if not trades:
        console.print("[red]no trades[/red]")
        return
    ts = sorted(trades, key=lambda t: t.entry_ts)
    t_min, t_max = ts[0].entry_ts, ts[-1].entry_ts
    span = (t_max - t_min).total_seconds()
    split = t_min + pd.Timedelta(seconds=span * 2 / 3)

    console.print(f"  Span: {t_min.strftime('%Y-%m-%d')} → {t_max.strftime('%Y-%m-%d')} · "
                  f"Split: {split.strftime('%Y-%m-%d %H:%M')}")
    train = [t.gross_ret - cost for t in ts if t.entry_ts < split]
    test = [t.gross_ret - cost for t in ts if t.entry_ts >= split]
    m_tr, m_te = metrics(np.array(train)), metrics(np.array(test))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for label, m in [("TRAIN (first 2/3 ~60d)", m_tr), ("TEST  (last 1/3 ~30d)", m_te)]:
        table.add_row(
            label,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0)
    tag = "[bold green]CONSISTENT[/bold green]" if consistent else "[bold red]REGIME BREAK[/bold red]"
    console.print(f"  → Δ AVG {m_te['avg'] - m_tr['avg']:+.2f}pp · {tag}")
    return m_tr, m_te


def render_slippage(trades: list[RegimeUpTrade], console: Console):
    console.rule(f"[bold orange1]SLIPPAGE STRESS · {COST_BASELINE*100:.2f}% → {COST_STRESS*100:.2f}%[/bold orange1]",
                 style="orange1")
    m_base = metrics(np.array([t.gross_ret - COST_BASELINE for t in trades]))
    m_str = metrics(np.array([t.gross_ret - COST_STRESS for t in trades]))
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SCENARIO", "COST", "AVG%", "CUM%", "IR"):
        table.add_column(c, justify="left" if c in ("SCENARIO", "COST") else "right")
    for label, cost, m in [("BASELINE", f"{COST_BASELINE*100:.2f}%", m_base),
                           ("STRESS  ", f"{COST_STRESS*100:.2f}%", m_str)]:
        table.add_row(
            label, cost,
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
        )
    console.print(table)
    return m_base, m_str


def render_portfolio_cap(trades: list[RegimeUpTrade], console: Console):
    console.rule(f"[bold orange1]PORTFOLIO · ARB CAP {ARB_MAX_ALLOC*100:.0f}%[/bold orange1]",
                 style="orange1")
    m_equal = metrics(np.array([t.gross_ret - COST_BASELINE for t in trades]))
    m_capped = portfolio_weighted_metrics(trades, COST_BASELINE, ARB_MAX_ALLOC)
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WEIGHTING", "N", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WEIGHTING" else "right")
    for label, m in [("EQUAL-WEIGHT", m_equal), (f"ARB-CAPPED {int(ARB_MAX_ALLOC*100)}%", m_capped)]:
        table.add_row(
            label,
            str(m["n"]),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    return m_capped


def final_verdict(per_symbol: dict, m_capped: dict, m_stress: dict, m_test: dict,
                  console: Console):
    console.rule("[bold orange1]FINAL VERDICT · 90D · FILTERED[/bold orange1]", style="orange1")
    pos_count = sum(1 for m in per_symbol.values() if m["cum"] > 0)
    total_count = len(per_symbol)
    cum_all = sum(m["cum"] for m in per_symbol.values())
    top_contrib = max((m["cum"] / cum_all * 100 if cum_all > 0 else 0)
                      for m in per_symbol.values())

    gates = [
        ("Breadth (≥60% symbol profit)", pos_count >= total_count * 0.6,
         f"{pos_count}/{total_count}"),
        ("Concentration natural <50%", top_contrib < 50 and cum_all > 0,
         f"top {top_contrib:.1f}%"),
        ("Slippage stress IR>0", m_stress["ir"] > 0,
         f"stress IR {m_stress['ir']:+.2f}"),
        ("Walk-forward TEST profit", m_test["avg"] > 0,
         f"test AVG {m_test['avg']:+.2f}%"),
        ("ARB-capped portfolio AVG>0", m_capped["avg"] > 0,
         f"capped AVG {m_capped['avg']:+.2f}%"),
        ("ARB-capped MAX_DD > -30%", m_capped["max_dd"] > -30,
         f"MAX_DD {m_capped['max_dd']:.1f}%"),
    ]
    table = Table(box=box.SIMPLE_HEAVY, header_style="black on orange1")
    for c in ("GATE", "RESULT", "DETAIL"):
        table.add_column(c)
    for name, passed, detail in gates:
        mark = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)
    passed_n = sum(1 for g in gates if g[1])
    if passed_n == len(gates):
        console.print(f"\n[bold green]✔ LAYAK PAPER TRADING[/bold green] · {passed_n}/{len(gates)} gate lolos")
    elif passed_n >= len(gates) - 1:
        console.print(f"\n[yellow]◆ PAPER TRADING LIMITED OK[/yellow] · {passed_n}/{len(gates)} gate · "
                      "proceed dengan caveat, review gate yang gagal")
    else:
        console.print(f"\n[bold red]✘ BELUM LAYAK[/bold red] · cuma {passed_n}/{len(gates)} gate lolos")


def main():
    console = Console()
    console.rule("[bold orange1]VALIDATION V2 · 90D · REGIME_UP @ 72h[/bold orange1]", style="orange1")

    console.print("Loading 90d data...")
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ohlcv_dict = load_ohlcv(conn)
        funding_dict = load_funding(conn)
    finally:
        conn.close()

    # Trim ke 90d terakhir (in case data lebih panjang)
    cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=90)
    ohlcv_dict = {s: df[df["ts"] >= cutoff].reset_index(drop=True)
                  for s, df in ohlcv_dict.items()}

    total_bars = sum(len(df) for df in ohlcv_dict.values())
    console.print(f"  {len(ohlcv_dict)} symbols · {total_bars} OHLCV bars · "
                  f"{sum(len(f) for f in funding_dict.values())} funding rows")

    console.print("Replaying REGIME_UP @ 72h...")
    t0 = time.time()
    raw_trades = replay_regime_up(ohlcv_dict, funding_dict)
    console.print(f"  {len(raw_trades)} trades · {time.time() - t0:.1f}s")

    # Build 3 filter scenarios
    f_sym = apply_filters(raw_trades, exclude=EXCLUDE_SYMBOLS)
    f_full = apply_filters(raw_trades, exclude=EXCLUDE_SYMBOLS, funding_skip=True)

    render_scenario_compare(
        [
            ("RAW (all 8 symbols, no filter)", raw_trades),
            (f"EXCLUDE_SOL (7 symbols)", f_sym),
            (f"+ FUNDING>{FUNDING_NEG_SKIP_THR*100:+.2f}%", f_full),
        ],
        COST_BASELINE, console,
    )

    # Drill down ke filter final
    per_symbol = render_per_symbol(f_full, COST_BASELINE, console,
                                   "EXCLUDE_SOL + FUNDING_FILTER (90d)")
    _, m_test = render_walk_forward(f_full, COST_BASELINE, console)
    _, m_stress = render_slippage(f_full, console)
    m_capped = render_portfolio_cap(f_full, console)
    final_verdict(per_symbol, m_capped, m_stress, m_test, console)


if __name__ == "__main__":
    main()
