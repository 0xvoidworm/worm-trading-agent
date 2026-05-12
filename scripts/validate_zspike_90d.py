#!/usr/bin/env python3
"""Validation Z_SPIKE pair trading di 90d data full.

Tujuan: cek apakah cointegration-based mean reversion punya edge yang stabil
di cross-regime window (bear Jan-Mar + recovery Mar-Apr).

Test:
  - Per-pair breakdown (N, WIN%, AVG%, IR)
  - Per-horizon comparison (4h/24h/72h)
  - Walk-forward 60/30 split untuk detect regime break
  - Slippage stress (0.22% → 0.30% pair cost)
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest import (
    DB_PATH, HORIZONS_H, COST_PAIR, Z_THRESHOLD, ADF_P_MAX, WARMUP_BARS,
    load_ohlcv, replay_zspike,
)


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
COST_STRESS = 0.0030   # 0.30% pair round-trip (vs 0.22% baseline)


def metrics(rets: np.ndarray) -> dict:
    n = len(rets)
    if n == 0:
        return {"n": 0, "win_pct": 0.0, "avg": 0.0, "med": 0.0, "ir": 0.0,
                "cum": 0.0, "max_dd": 0.0}
    wins = int((rets > 0).sum())
    std = float(rets.std(ddof=1)) if n > 1 else 0.0
    ir = float(rets.mean() / std) if std > 0 else 0.0
    eq = np.cumsum(rets)
    peak = np.maximum.accumulate(eq)
    dd = float((eq - peak).min())
    return {
        "n": n,
        "win_pct": 100 * wins / n,
        "avg": float(rets.mean()) * 100,
        "med": float(np.median(rets)) * 100,
        "ir": ir,
        "cum": float(rets.sum()) * 100,
        "max_dd": dd * 100,
    }


def color(v: float, good: float, bad: float, fmt: str = "{:+.2f}") -> str:
    s = fmt.format(v)
    if v >= good:
        return f"[bold green]{s}[/bold green]"
    if v < bad:
        return f"[red]{s}[/red]"
    return s


def render_by_horizon(trades, console: Console):
    console.rule("[bold orange1]Z_SPIKE · BY HORIZON (combined long/short spread)[/bold orange1]",
                 style="orange1")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SUBTYPE", "H", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c in ("SUBTYPE", "H") else "right")
    for subtype in ("Z_LONG_SPR", "Z_SHORT_SPR"):
        for h in HORIZONS_H:
            rets = np.array([t.returns[h] for t in trades
                             if t.subtype == subtype and h in t.returns])
            m = metrics(rets)
            table.add_row(
                subtype, f"{h}h",
                str(m["n"]),
                color(m["win_pct"], 55, 45, "{:.1f}"),
                color(m["avg"], 0.05, 0),
                color(m["med"], 0.05, 0),
                color(m["cum"], 5, 0, "{:+.1f}"),
                color(m["ir"], 0.2, 0),
                f"[red]{m['max_dd']:.2f}[/red]",
            )
    console.print(table)


def render_by_pair(trades, console: Console, horizon: int):
    console.rule(f"[bold orange1]Z_SPIKE · PER PAIR @ {horizon}h[/bold orange1]", style="orange1")
    groups = {}
    for t in trades:
        if horizon not in t.returns:
            continue
        groups.setdefault(t.symbol, []).append(t.returns[horizon])

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("PAIR", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "PAIR" else "right")
    for pair in sorted(groups.keys()):
        m = metrics(np.array(groups[pair]))
        table.add_row(
            pair,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.05, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.2, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)


def render_walk_forward(trades, console: Console, horizon: int):
    console.rule(f"[bold orange1]WALK-FORWARD · BEAR (Jan-Mar) vs RECOVERY (Mar-Apr) @ {horizon}h[/bold orange1]",
                 style="orange1")
    matching = sorted([t for t in trades if horizon in t.returns], key=lambda t: t.entry_ts)
    if not matching:
        console.print("[yellow]no trades[/yellow]")
        return None, None

    t_min, t_max = matching[0].entry_ts, matching[-1].entry_ts
    span = (t_max - t_min).total_seconds()
    split = t_min + pd.Timedelta(seconds=span * 2 / 3)
    console.print(f"  Span: {t_min.strftime('%Y-%m-%d')} → {t_max.strftime('%Y-%m-%d')} · "
                  f"Split: {split.strftime('%Y-%m-%d %H:%M')}")

    train = [t.returns[horizon] for t in matching if t.entry_ts < split]
    test = [t.returns[horizon] for t in matching if t.entry_ts >= split]
    m_tr = metrics(np.array(train))
    m_te = metrics(np.array(test))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for lbl, m in [("TRAIN (first 2/3)", m_tr), ("TEST  (last 1/3)", m_te)]:
        table.add_row(
            lbl,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.05, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.2, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    if m_tr["n"] > 0 and m_te["n"] > 0:
        consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0)
        tag = "[bold green]CONSISTENT[/bold green]" if consistent else "[bold red]REGIME BREAK[/bold red]"
        console.print(f"  → Δ AVG {m_te['avg'] - m_tr['avg']:+.2f}pp · {tag}")
    return m_tr, m_te


def render_slippage(trades, console: Console, horizon: int):
    """Re-apply slippage on gross return. Original trades have COST_PAIR baked in,
    so we back it out then reapply at new cost."""
    console.rule(f"[bold orange1]SLIPPAGE STRESS @ {horizon}h · {COST_PAIR*100:.2f}% → {COST_STRESS*100:.2f}%[/bold orange1]",
                 style="orange1")
    base = [t.returns[horizon] for t in trades if horizon in t.returns]
    # gross = net + COST_PAIR  (since returns[h] = gross - COST_PAIR)
    gross = [r + COST_PAIR for r in base]
    stress = [g - COST_STRESS for g in gross]
    m_b = metrics(np.array(base))
    m_s = metrics(np.array(stress))
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SCENARIO", "COST", "AVG%", "CUM%", "IR"):
        table.add_column(c, justify="left" if c in ("SCENARIO", "COST") else "right")
    for lbl, cost, m in [("BASELINE", f"{COST_PAIR*100:.2f}%", m_b),
                         ("STRESS  ", f"{COST_STRESS*100:.2f}%", m_s)]:
        table.add_row(lbl, cost,
                      color(m["avg"], 0.05, 0),
                      color(m["cum"], 5, 0, "{:+.1f}"),
                      color(m["ir"], 0.2, 0))
    console.print(table)
    return m_b, m_s


def final_verdict(trades, m_tr, m_te, m_base, m_stress, console: Console, horizon: int):
    console.rule(f"[bold orange1]FINAL VERDICT · Z_SPIKE @ {horizon}h[/bold orange1]", style="orange1")
    rets_all = np.array([t.returns[horizon] for t in trades if horizon in t.returns])
    m_all = metrics(rets_all)

    pair_metrics = {}
    for t in trades:
        if horizon not in t.returns:
            continue
        pair_metrics.setdefault(t.symbol, []).append(t.returns[horizon])
    pair_metrics = {p: metrics(np.array(rs)) for p, rs in pair_metrics.items()}
    pos_pairs = sum(1 for m in pair_metrics.values() if m["cum"] > 0)

    gates = [
        ("Sample size ≥30 trades", m_all["n"] >= 30, f"{m_all['n']} trades"),
        ("Overall AVG% > 0", m_all["avg"] > 0, f"AVG {m_all['avg']:+.2f}%"),
        ("Overall IR > 0.15", m_all["ir"] > 0.15, f"IR {m_all['ir']:+.2f}"),
        ("Walk-forward TEST profit", m_te and m_te["n"] > 0 and m_te["avg"] > 0,
         f"test AVG {m_te['avg']:+.2f}%" if m_te else "no test data"),
        ("Walk-forward CONSISTENT sign",
         m_tr and m_te and (m_tr["avg"] > 0) == (m_te["avg"] > 0),
         "same sign" if m_tr and m_te and (m_tr["avg"] > 0) == (m_te["avg"] > 0) else "regime break"),
        ("Slippage stress IR still > 0", m_stress["ir"] > 0,
         f"stress IR {m_stress['ir']:+.2f}"),
        ("Pair breadth ≥50% profitable", pos_pairs >= max(1, len(pair_metrics) // 2),
         f"{pos_pairs}/{len(pair_metrics)}"),
        ("MAX_DD > -30%", m_all["max_dd"] > -30, f"MAX_DD {m_all['max_dd']:.1f}%"),
    ]
    table = Table(box=box.SIMPLE_HEAVY, header_style="black on orange1")
    for c in ("GATE", "RESULT", "DETAIL"):
        table.add_column(c)
    for name, passed, detail in gates:
        mark = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)

    passed_n = sum(1 for g in gates if g[1])
    total = len(gates)
    if passed_n == total:
        tag = f"[bold green]✔ LAYAK PAPER TRADING[/bold green]"
    elif passed_n >= total - 2:
        tag = f"[yellow]◆ BORDERLINE[/yellow]"
    else:
        tag = f"[bold red]✘ TIDAK LAYAK[/bold red]"
    console.print(f"\n{tag} · {passed_n}/{total} gate lolos")


def main():
    console = Console()
    console.rule("[bold orange1]Z_SPIKE VALIDATION · 90D · CROSS-REGIME TEST[/bold orange1]",
                 style="orange1")

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    pairs = cfg.get("cointegration_candidates", [])
    console.print(f"Pairs configured: {pairs}")
    console.print(f"Cost baseline: {COST_PAIR*100:.2f}% pair · "
                  f"Z threshold: ±{Z_THRESHOLD} · ADF_P_MAX: {ADF_P_MAX}")

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ohlcv_dict = load_ohlcv(conn)
    finally:
        conn.close()

    cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=90)
    ohlcv_dict = {s: df[df["ts"] >= cutoff].reset_index(drop=True)
                  for s, df in ohlcv_dict.items()}
    console.print(f"Data: {len(ohlcv_dict)} symbols · "
                  f"{sum(len(df) for df in ohlcv_dict.values())} bars")

    console.print(f"\nReplaying Z_SPIKE (warmup {WARMUP_BARS} bars, this is slow)...")
    t0 = time.time()
    trades = replay_zspike(ohlcv_dict, pairs, console)
    console.print(f"  {len(trades)} trades total · {time.time() - t0:.1f}s")

    if not trades:
        console.print("[red]No trades generated — check pair cointegration in 90d window[/red]")
        return

    # By horizon (combined long+short spread)
    render_by_horizon(trades, console)

    # Find best horizon by IR on combined trades
    best_h = max(HORIZONS_H,
                 key=lambda h: metrics(np.array([t.returns[h] for t in trades
                                                  if h in t.returns]))["ir"])
    console.print(f"\n[bold]Best horizon by IR: {best_h}h[/bold] — drilling down...")

    render_by_pair(trades, console, best_h)
    m_tr, m_te = render_walk_forward(trades, console, best_h)
    m_base, m_stress = render_slippage(trades, console, best_h)
    final_verdict(trades, m_tr, m_te, m_base, m_stress, console, best_h)


if __name__ == "__main__":
    main()
