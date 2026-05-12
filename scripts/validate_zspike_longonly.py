#!/usr/bin/env python3
"""Drill-down: Z_LONG_SPR only (skip Z_SHORT_SPR yang broken).

Observasi dari validate_zspike_90d.py:
- Z_LONG_SPR @ 72h: WIN 60%, AVG +0.38%, CUM +15.3% → hint edge
- Z_SHORT_SPR semua horizon: CUM -10 sd -55% → squeezed oleh trending assets

Hipotesis: pair trading asimetris — long-spread (fade oversold) works, short-spread
(fade overbought) gets squeezed. Classic finding di positive-drift markets.

Test ini cek: kalau kita SKIP Z_SHORT_SPR, apakah Z_LONG_SPR standalone layak?
"""
from __future__ import annotations

import sys
import time
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
    DB_PATH, HORIZONS_H, COST_PAIR, load_ohlcv, replay_zspike,
)


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
COST_STRESS = 0.0030


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
    return {"n": n, "win_pct": 100 * wins / n,
            "avg": float(rets.mean()) * 100, "med": float(np.median(rets)) * 100,
            "ir": ir, "cum": float(rets.sum()) * 100, "max_dd": dd * 100}


def color(v, good, bad, fmt="{:+.2f}"):
    s = fmt.format(v)
    if v >= good: return f"[bold green]{s}[/bold green]"
    if v < bad: return f"[red]{s}[/red]"
    return s


def render_horizon_compare(long_trades, console: Console):
    console.rule("[bold orange1]Z_LONG_SPR ONLY · BY HORIZON[/bold orange1]", style="orange1")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("H", "N", "WIN%", "AVG%", "MED%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "H" else "right")
    for h in HORIZONS_H:
        rets = np.array([t.returns[h] for t in long_trades if h in t.returns])
        m = metrics(rets)
        table.add_row(
            f"{h}h", str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["med"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.15, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)


def render_per_pair(long_trades, console: Console, h: int):
    console.rule(f"[bold orange1]Z_LONG_SPR · PER PAIR @ {h}h[/bold orange1]", style="orange1")
    groups = {}
    for t in long_trades:
        if h not in t.returns: continue
        groups.setdefault(t.symbol, []).append(t.returns[h])
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("PAIR", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "PAIR" else "right")
    for pair in sorted(groups.keys()):
        m = metrics(np.array(groups[pair]))
        table.add_row(
            pair, str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.15, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    return groups


def render_walk_forward(long_trades, console: Console, h: int):
    console.rule(f"[bold orange1]WALK-FORWARD · Z_LONG_SPR @ {h}h[/bold orange1]", style="orange1")
    matching = sorted([t for t in long_trades if h in t.returns], key=lambda t: t.entry_ts)
    if not matching:
        console.print("[yellow]no trades[/yellow]"); return None, None
    t_min, t_max = matching[0].entry_ts, matching[-1].entry_ts
    span = (t_max - t_min).total_seconds()
    split = t_min + pd.Timedelta(seconds=span * 2 / 3)
    console.print(f"  Span: {t_min.strftime('%Y-%m-%d')} → {t_max.strftime('%Y-%m-%d')} · "
                  f"Split: {split.strftime('%Y-%m-%d %H:%M')}")
    train = [t.returns[h] for t in matching if t.entry_ts < split]
    test = [t.returns[h] for t in matching if t.entry_ts >= split]
    m_tr, m_te = metrics(np.array(train)), metrics(np.array(test))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for lbl, m in [("TRAIN (Jan-Mar bear/sideways)", m_tr),
                   ("TEST  (Mar-Apr recovery)", m_te)]:
        table.add_row(lbl, str(m["n"]),
                      color(m["win_pct"], 55, 45, "{:.1f}"),
                      color(m["avg"], 0.1, 0),
                      color(m["cum"], 5, 0, "{:+.1f}"),
                      color(m["ir"], 0.15, 0),
                      f"[red]{m['max_dd']:.2f}[/red]")
    console.print(table)
    if m_tr["n"] > 0 and m_te["n"] > 0:
        both_profit = m_tr["avg"] > 0 and m_te["avg"] > 0
        consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0)
        tag = ("[bold green]BOTH PROFITABLE[/bold green]" if both_profit else
               "[bold green]CONSISTENT[/bold green]" if consistent else
               "[bold red]REGIME BREAK[/bold red]")
        console.print(f"  → Train AVG {m_tr['avg']:+.2f}% / Test AVG {m_te['avg']:+.2f}% · {tag}")
    return m_tr, m_te


def render_slippage(long_trades, console: Console, h: int):
    console.rule(f"[bold orange1]SLIPPAGE STRESS · Z_LONG_SPR @ {h}h[/bold orange1]", style="orange1")
    base = [t.returns[h] for t in long_trades if h in t.returns]
    gross = [r + COST_PAIR for r in base]
    stress = [g - COST_STRESS for g in gross]
    m_b, m_s = metrics(np.array(base)), metrics(np.array(stress))
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SCENARIO", "COST", "N", "AVG%", "CUM%", "IR"):
        table.add_column(c, justify="left" if c in ("SCENARIO", "COST") else "right")
    for lbl, cost, m in [("BASELINE", f"{COST_PAIR*100:.2f}%", m_b),
                         ("STRESS  ", f"{COST_STRESS*100:.2f}%", m_s)]:
        table.add_row(lbl, cost, str(m["n"]),
                      color(m["avg"], 0.1, 0),
                      color(m["cum"], 5, 0, "{:+.1f}"),
                      color(m["ir"], 0.15, 0))
    console.print(table)
    return m_b, m_s


def final_verdict(long_trades, m_tr, m_te, m_stress, groups, console: Console, h: int):
    console.rule(f"[bold orange1]FINAL VERDICT · Z_LONG_SPR @ {h}h[/bold orange1]", style="orange1")
    rets_all = np.array([t.returns[h] for t in long_trades if h in t.returns])
    m_all = metrics(rets_all)
    pos_pairs = sum(1 for rs in groups.values() if np.array(rs).sum() > 0)

    gates = [
        ("Sample ≥30 trades", m_all["n"] >= 30, f"{m_all['n']} trades"),
        ("Overall AVG% > 0", m_all["avg"] > 0, f"{m_all['avg']:+.2f}%"),
        ("Overall IR > 0.15", m_all["ir"] > 0.15, f"IR {m_all['ir']:+.2f}"),
        ("WIN% > 55", m_all["win_pct"] > 55, f"WIN {m_all['win_pct']:.1f}%"),
        ("WF TRAIN profit", m_tr and m_tr["avg"] > 0,
         f"train {m_tr['avg']:+.2f}%" if m_tr else "n/a"),
        ("WF TEST profit", m_te and m_te["avg"] > 0,
         f"test {m_te['avg']:+.2f}%" if m_te else "n/a"),
        ("Slippage IR>0", m_stress["ir"] > 0, f"stress IR {m_stress['ir']:+.2f}"),
        ("Pair breadth ≥50% profit", pos_pairs >= max(1, len(groups) // 2),
         f"{pos_pairs}/{len(groups)}"),
        ("MAX_DD > -30%", m_all["max_dd"] > -30, f"MAX_DD {m_all['max_dd']:.1f}%"),
    ]
    table = Table(box=box.SIMPLE_HEAVY, header_style="black on orange1")
    for c in ("GATE", "RESULT", "DETAIL"): table.add_column(c)
    for name, passed, detail in gates:
        mark = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)
    passed_n = sum(1 for g in gates if g[1])
    total = len(gates)
    if passed_n >= total - 1:
        tag = "[bold green]✔ LAYAK PAPER TRADING[/bold green]"
    elif passed_n >= total - 3:
        tag = "[yellow]◆ BORDERLINE — paper trade defanged[/yellow]"
    else:
        tag = "[bold red]✘ TIDAK LAYAK[/bold red]"
    console.print(f"\n{tag} · {passed_n}/{total} gate lolos")


def main():
    console = Console()
    console.rule("[bold orange1]Z_LONG_SPR ONLY · 90D DEEP DIVE[/bold orange1]", style="orange1")

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    pairs = cfg.get("cointegration_candidates", [])

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ohlcv_dict = load_ohlcv(conn)
    finally:
        conn.close()
    cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=90)
    ohlcv_dict = {s: df[df["ts"] >= cutoff].reset_index(drop=True)
                  for s, df in ohlcv_dict.items()}

    console.print("Replaying Z_SPIKE (reuse replay_zspike)...")
    t0 = time.time()
    all_trades = replay_zspike(ohlcv_dict, pairs, console)
    console.print(f"  {len(all_trades)} total trades · {time.time()-t0:.1f}s")

    long_trades = [t for t in all_trades if t.subtype == "Z_LONG_SPR"]
    short_trades = [t for t in all_trades if t.subtype == "Z_SHORT_SPR"]
    console.print(f"  Split: {len(long_trades)} LONG_SPR · {len(short_trades)} SHORT_SPR (dropped)")

    if not long_trades:
        console.print("[red]no long trades — nothing to validate[/red]")
        return

    render_horizon_compare(long_trades, console)

    # Best horizon by IR
    best_h = max(HORIZONS_H,
                 key=lambda h: metrics(np.array([t.returns[h] for t in long_trades
                                                  if h in t.returns]))["ir"])
    console.print(f"\n[bold]Best horizon by IR: {best_h}h[/bold] — drill-down...")

    groups = render_per_pair(long_trades, console, best_h)
    m_tr, m_te = render_walk_forward(long_trades, console, best_h)
    _, m_stress = render_slippage(long_trades, console, best_h)
    final_verdict(long_trades, m_tr, m_te, m_stress, groups, console, best_h)


if __name__ == "__main__":
    main()
