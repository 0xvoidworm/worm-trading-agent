#!/usr/bin/env python3
"""Validation gate untuk REGIME_UP @ 72h.

Tiga test:
  1. Per-symbol breakdown — cek distribusi profit antar symbol (data mining check)
  2. Walk-forward 20d train / 10d test — cek stabilitas temporal
  3. Slippage stress test di cost 0.18% (dari baseline 0.11%)

Reuses replay_regime() dari backtest.py tapi track gross return (pre-cost) agar
kita bisa apply multiple cost scenarios tanpa replay ulang.
"""
from __future__ import annotations

import sys
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
from scripts.backtest import DB_PATH, WARMUP_BARS, load_ohlcv


HORIZON = 72
COST_BASELINE = 0.0011
COST_STRESS = 0.0018


@dataclass
class RegimeUpTrade:
    symbol: str
    entry_ts: pd.Timestamp
    entry_price: float
    exit_price: float
    gross_ret: float   # pre-cost return (fractional)


def replay_regime_up_only(ohlcv_dict: dict[str, pd.DataFrame]) -> list[RegimeUpTrade]:
    """Varian replay_regime: hanya emit trade untuk REGIME_UP @ horizon 72h, simpan gross."""
    trades = []
    for sym, df in ohlcv_dict.items():
        if len(df) < WARMUP_BARS + HORIZON:
            continue
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
                gross = (exitp - entry) / entry
                trades.append(RegimeUpTrade(
                    symbol=sym,
                    entry_ts=pd.Timestamp(df.iloc[i]["ts"]),
                    entry_price=entry,
                    exit_price=exitp,
                    gross_ret=gross,
                ))
            prev_regime = snap.regime
    return trades


def metrics(rets: np.ndarray) -> dict:
    n = len(rets)
    if n == 0:
        return {"n": 0, "win_pct": 0.0, "avg": 0.0, "med": 0.0, "ir": 0.0, "cum": 0.0, "max_dd": 0.0}
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


def color(value: float, good_thr: float, bad_thr: float, fmt: str = "{:+.2f}") -> str:
    s = fmt.format(value)
    if value >= good_thr:
        return f"[bold green]{s}[/bold green]"
    if value < bad_thr:
        return f"[red]{s}[/red]"
    return s


def render_per_symbol(trades: list[RegimeUpTrade], cost: float, console: Console):
    console.rule("[bold orange1]1. PER-SYMBOL BREAKDOWN · REGIME_UP @ 72h[/bold orange1]", style="orange1")
    groups: dict[str, list[float]] = {}
    for t in trades:
        groups.setdefault(t.symbol, []).append(t.gross_ret - cost)

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SYMBOL", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR", "MAX_DD%", "SHARE_OF_CUM"):
        table.add_column(c, justify="left" if c == "SYMBOL" else "right")

    per_symbol_metrics = {sym: metrics(np.array(rs)) for sym, rs in groups.items()}
    total_cum_abs = sum(abs(m["cum"]) for m in per_symbol_metrics.values()) or 1.0

    for sym in sorted(per_symbol_metrics.keys()):
        m = per_symbol_metrics[sym]
        share = 100 * abs(m["cum"]) / total_cum_abs
        table.add_row(
            sym,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["med"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
            f"{share:.1f}%",
        )
    console.print(table)

    # Concentration analysis
    sorted_by_cum = sorted(per_symbol_metrics.items(), key=lambda x: x[1]["cum"], reverse=True)
    top1_sym, top1_m = sorted_by_cum[0]
    cum_all = sum(m["cum"] for m in per_symbol_metrics.values())
    top1_contrib = top1_m["cum"] / cum_all * 100 if cum_all else 0

    positive_syms = [s for s, m in per_symbol_metrics.items() if m["cum"] > 0]
    console.print(
        f"  → Symbol profitable: {len(positive_syms)}/{len(per_symbol_metrics)} · "
        f"Top contributor: {top1_sym} ({top1_contrib:.1f}% dari total CUM)"
    )
    return per_symbol_metrics


def render_walk_forward(trades: list[RegimeUpTrade], cost: float, console: Console):
    console.rule("[bold orange1]2. WALK-FORWARD · 20D TRAIN / 10D TEST[/bold orange1]", style="orange1")
    if not trades:
        console.print("[red]no trades[/red]")
        return
    trades_sorted = sorted(trades, key=lambda t: t.entry_ts)
    t_min = trades_sorted[0].entry_ts
    t_max = trades_sorted[-1].entry_ts
    total_span = (t_max - t_min).total_seconds()
    split_ts = t_min + pd.Timedelta(seconds=total_span * 2 / 3)

    console.print(f"  Data span: {t_min.strftime('%Y-%m-%d %H:%M')} → {t_max.strftime('%Y-%m-%d %H:%M')}")
    console.print(f"  Split at : {split_ts.strftime('%Y-%m-%d %H:%M')} (train=first 2/3, test=last 1/3)")

    train = [t.gross_ret - cost for t in trades_sorted if t.entry_ts < split_ts]
    test = [t.gross_ret - cost for t in trades_sorted if t.entry_ts >= split_ts]

    m_train = metrics(np.array(train))
    m_test = metrics(np.array(test))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for label, m in [("TRAIN (first 2/3)", m_train), ("TEST  (last 1/3)", m_test)]:
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

    # Consistency verdict
    if m_train["n"] > 0 and m_test["n"] > 0:
        delta_win = m_test["win_pct"] - m_train["win_pct"]
        delta_avg = m_test["avg"] - m_train["avg"]
        train_sign = "profit" if m_train["avg"] > 0 else "loss"
        test_sign = "profit" if m_test["avg"] > 0 else "loss"
        consistent = (m_train["avg"] > 0) == (m_test["avg"] > 0)
        tag = "[bold green]CONSISTENT[/bold green]" if consistent else "[bold red]REGIME BREAK[/bold red]"
        console.print(
            f"  → Train {train_sign} / Test {test_sign} · "
            f"Δ WIN% = {delta_win:+.1f}pp · Δ AVG% = {delta_avg:+.2f}pp · {tag}"
        )


def render_slippage_stress(trades: list[RegimeUpTrade], console: Console):
    console.rule(
        f"[bold orange1]3. SLIPPAGE STRESS · COST {COST_BASELINE*100:.2f}% → {COST_STRESS*100:.2f}%[/bold orange1]",
        style="orange1",
    )
    rets_base = np.array([t.gross_ret - COST_BASELINE for t in trades])
    rets_stress = np.array([t.gross_ret - COST_STRESS for t in trades])
    m_base = metrics(rets_base)
    m_stress = metrics(rets_stress)

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SCENARIO", "COST", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR"):
        table.add_column(c, justify="left" if c in ("SCENARIO", "COST") else "right")
    for label, cost, m in [
        ("BASELINE", f"{COST_BASELINE*100:.2f}%", m_base),
        ("STRESS  ", f"{COST_STRESS*100:.2f}%", m_stress),
    ]:
        table.add_row(
            label,
            cost,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["med"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
        )
    console.print(table)
    survived = m_stress["avg"] > 0 and m_stress["ir"] > 0
    tag = "[bold green]EDGE SURVIVES[/bold green]" if survived else "[bold red]EDGE COLLAPSES[/bold red]"
    console.print(
        f"  → Baseline AVG {m_base['avg']:+.2f}% · Stress AVG {m_stress['avg']:+.2f}% "
        f"(Δ {m_stress['avg'] - m_base['avg']:+.2f}pp) · {tag}"
    )
    return m_base, m_stress


def final_verdict(
    per_symbol: dict, m_base: dict, m_stress: dict, trades: list[RegimeUpTrade], console: Console
):
    console.rule("[bold orange1]FINAL VERDICT[/bold orange1]", style="orange1")

    # Gate 1: breadth (≥5 of 8 symbol profitable)
    pos_count = sum(1 for m in per_symbol.values() if m["cum"] > 0)
    total_count = len(per_symbol)
    gate1_pass = pos_count >= max(5, total_count * 0.6)

    # Gate 2: top symbol contributes < 60% of total CUM
    cum_all = sum(m["cum"] for m in per_symbol.values())
    top_contrib = max((m["cum"] / cum_all * 100 if cum_all else 0) for m in per_symbol.values())
    gate2_pass = top_contrib < 60 and cum_all > 0

    # Gate 3: slippage stress still profitable
    gate3_pass = m_stress["avg"] > 0 and m_stress["ir"] > 0

    # Gate 4: walk-forward consistency (test window still profitable)
    trades_sorted = sorted(trades, key=lambda t: t.entry_ts)
    if trades_sorted:
        span = (trades_sorted[-1].entry_ts - trades_sorted[0].entry_ts).total_seconds()
        split = trades_sorted[0].entry_ts + pd.Timedelta(seconds=span * 2 / 3)
        test = [t.gross_ret - COST_BASELINE for t in trades_sorted if t.entry_ts >= split]
        gate4_pass = len(test) > 0 and np.array(test).mean() > 0
    else:
        gate4_pass = False

    gates = [
        ("Breadth (≥5/8 symbol profit)", gate1_pass, f"{pos_count}/{total_count}"),
        ("Concentration (top < 60%)", gate2_pass, f"{top_contrib:.1f}%"),
        ("Slippage stress survives", gate3_pass, f"stress AVG {m_stress['avg']:+.2f}%"),
        ("Walk-forward TEST profit", gate4_pass, "test avg profitable" if gate4_pass else "test avg loss"),
    ]

    table = Table(box=box.SIMPLE_HEAVY, header_style="black on orange1")
    for c in ("GATE", "RESULT", "DETAIL"):
        table.add_column(c)
    for name, passed, detail in gates:
        mark = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)

    all_pass = all(g[1] for g in gates)
    if all_pass:
        console.print("\n[bold green]✔ LAYAK PAPER TRADING[/bold green] — strategi lolos semua validation gate.")
    else:
        fail_count = sum(1 for g in gates if not g[1])
        console.print(
            f"\n[bold red]✘ BELUM LAYAK PAPER TRADING[/bold red] — {fail_count}/4 gate gagal. "
            "Strategi butuh investigasi lanjut, bukan eksekusi."
        )


def main():
    console = Console()
    console.rule("[bold orange1]VALIDATION GATE — REGIME_UP @ 72h[/bold orange1]", style="orange1")

    console.print("Loading OHLCV dari DuckDB...")
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ohlcv_dict = load_ohlcv(conn)
    finally:
        conn.close()
    console.print(f"  {len(ohlcv_dict)} symbols loaded")

    console.print("Replaying regime (REGIME_UP only, 72h horizon)...")
    import time
    t0 = time.time()
    trades = replay_regime_up_only(ohlcv_dict)
    console.print(f"  {len(trades)} trades · {time.time() - t0:.1f}s")

    if not trades:
        console.print("[red]no trades generated — abort[/red]")
        return

    per_symbol = render_per_symbol(trades, COST_BASELINE, console)
    render_walk_forward(trades, COST_BASELINE, console)
    m_base, m_stress = render_slippage_stress(trades, console)
    final_verdict(per_symbol, m_base, m_stress, trades, console)


if __name__ == "__main__":
    main()
