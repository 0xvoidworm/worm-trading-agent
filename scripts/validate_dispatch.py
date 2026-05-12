#!/usr/bin/env python3
"""Validation DISPATCH — meta-layer regime overlay.

Test hipotesis: strategi yang "sampah rata-rata" mungkin profitable kalau
dieksekusi hanya di regime market yang cocok.

Dispatch rules:
  - REGIME_UP 72h (LONG): eksekusi hanya saat regime == low_vol_up
  - Z_LONG_SPR 72h:       eksekusi hanya saat regime == high_vol_sideways

Output:
  - Distribusi regime di 90d window (visibility)
  - Per-regime performance untuk setiap strategy
  - Dispatched strategy metrics (only trigger-matching regimes)
  - Walk-forward split untuk cek robustness
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

from dashboard.regime import classify
from dashboard.market_regime import (
    build_regime_timeline, regime_at, regime_summary,
)
from scripts.backtest import (
    DB_PATH, WARMUP_BARS, HORIZONS_H, COST_SINGLE, COST_PAIR,
    load_ohlcv, replay_zspike,
)


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
HORIZON_REGIME = 72
HORIZON_ZSPIKE = 72
DISPATCH_REGIME_UP = {"low_vol_up"}            # REGIME_UP executed only here
DISPATCH_ZSPIKE = {"high_vol_sideways"}        # Z_SPIKE executed only here


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


# ─── REGIME_UP replay (reuse logic, but tag with regime at entry) ───────────

def replay_regime_up_tagged(ohlcv_dict: dict[str, pd.DataFrame],
                             timeline: pd.DataFrame) -> list[dict]:
    """Same logic as replay_regime_up tapi return list of dict dengan
    entry_ts, symbol, gross_ret_72h, regime_label."""
    trades = []
    for sym, df in ohlcv_dict.items():
        if len(df) < WARMUP_BARS + HORIZON_REGIME:
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
            if snap.regime == "trending_up" and i + HORIZON_REGIME < len(df):
                entry = float(df.iloc[i]["close"])
                exitp = float(df.iloc[i + HORIZON_REGIME]["close"])
                entry_ts = pd.Timestamp(df.iloc[i]["ts"])
                trades.append({
                    "symbol": sym,
                    "entry_ts": entry_ts,
                    "gross_ret": (exitp - entry) / entry,
                    "regime": regime_at(timeline, entry_ts),
                })
            prev_regime = snap.regime
    return trades


def render_regime_distribution(timeline, console: Console):
    console.rule("[bold orange1]REGIME DISTRIBUTION · 90D BTC[/bold orange1]", style="orange1")
    summary = regime_summary(timeline)
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("REGIME", "BARS", "%"):
        table.add_column(c, justify="left" if c == "REGIME" else "right")
    for k in sorted(summary["counts"].keys()):
        table.add_row(k, str(summary["counts"][k]), f"{summary['pct'][k]:.1f}%")
    console.print(table)


def render_regime_up_per_regime(trades: list[dict], console: Console):
    console.rule("[bold orange1]REGIME_UP (LONG 72h) · PER MARKET REGIME[/bold orange1]",
                 style="orange1")
    groups = {}
    for t in trades:
        reg = t["regime"]
        if reg is None or pd.isna(reg): continue
        groups.setdefault(reg, []).append(t["gross_ret"] - COST_SINGLE)

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("MARKET_REGIME", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "MARKET_REGIME" else "right")
    for regime in sorted(groups.keys()):
        rets = np.array(groups[regime])
        m = metrics(rets)
        highlight = "[bold]" if regime in DISPATCH_REGIME_UP else ""
        closehl = "[/bold]" if regime in DISPATCH_REGIME_UP else ""
        marker = " ◀ DISPATCHED" if regime in DISPATCH_REGIME_UP else ""
        table.add_row(
            f"{highlight}{regime}{closehl}{marker}",
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.2, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    return groups


def render_zspike_per_regime(trades, timeline, console: Console):
    """For each Z_LONG_SPR trade, tag with regime. Group by regime, show metrics."""
    console.rule("[bold orange1]Z_LONG_SPR 72h · PER MARKET REGIME[/bold orange1]", style="orange1")
    groups = {}
    for t in trades:
        if HORIZON_ZSPIKE not in t.returns: continue
        reg = regime_at(timeline, t.entry_ts)
        if reg is None or pd.isna(reg): continue
        groups.setdefault(reg, []).append(t.returns[HORIZON_ZSPIKE])

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("MARKET_REGIME", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "MARKET_REGIME" else "right")
    for regime in sorted(groups.keys()):
        rets = np.array(groups[regime])
        m = metrics(rets)
        marker = " ◀ DISPATCHED" if regime in DISPATCH_ZSPIKE else ""
        highlight = "[bold]" if regime in DISPATCH_ZSPIKE else ""
        closehl = "[/bold]" if regime in DISPATCH_ZSPIKE else ""
        table.add_row(
            f"{highlight}{regime}{closehl}{marker}",
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.05, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.2, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    return groups


def render_dispatch_summary(r_groups, z_groups, console: Console):
    console.rule("[bold orange1]DISPATCHED STRATEGIES · FINAL METRICS[/bold orange1]",
                 style="orange1")
    # Regime_UP dispatched: only low_vol_up bucket
    r_rets = []
    for reg in DISPATCH_REGIME_UP:
        r_rets.extend(r_groups.get(reg, []))
    # Z_spike dispatched: only high_vol_sideways
    z_rets = []
    for reg in DISPATCH_ZSPIKE:
        z_rets.extend(z_groups.get(reg, []))

    m_r = metrics(np.array(r_rets))
    m_z = metrics(np.array(z_rets))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("STRATEGY", "DISPATCH_REGIME", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c in ("STRATEGY", "DISPATCH_REGIME") else "right")

    for label, dispatch, m in [
        ("REGIME_UP (long 72h)", ",".join(DISPATCH_REGIME_UP), m_r),
        ("Z_LONG_SPR (72h)", ",".join(DISPATCH_ZSPIKE), m_z),
    ]:
        table.add_row(
            label, dispatch,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.2, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    return m_r, m_z


def render_walk_forward(trades_tagged, strat: str, console: Console,
                        dispatch_set: set, get_return):
    """Walk-forward on dispatched strategy only."""
    console.rule(f"[bold orange1]WALK-FORWARD DISPATCHED · {strat}[/bold orange1]", style="orange1")
    # Filter trades that match dispatch regime
    matching = []
    for t in trades_tagged:
        r = get_return(t)
        if r is None: continue
        reg = t.get("regime")
        if reg is None or pd.isna(reg): continue
        if reg not in dispatch_set: continue
        matching.append(t)
    if not matching:
        console.print("[yellow]no dispatched trades[/yellow]"); return None, None

    matching.sort(key=lambda t: t["entry_ts"])
    t_min, t_max = matching[0]["entry_ts"], matching[-1]["entry_ts"]
    span = (t_max - t_min).total_seconds()
    split = t_min + pd.Timedelta(seconds=span * 2 / 3)

    train = [get_return(t) for t in matching if t["entry_ts"] < split]
    test = [get_return(t) for t in matching if t["entry_ts"] >= split]
    m_tr, m_te = metrics(np.array(train)), metrics(np.array(test))

    console.print(f"  Span: {t_min.strftime('%Y-%m-%d')} → {t_max.strftime('%Y-%m-%d')} · "
                  f"Split: {split.strftime('%Y-%m-%d %H:%M')}")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for lbl, m in [("TRAIN (first 2/3)", m_tr), ("TEST  (last 1/3)", m_te)]:
        table.add_row(lbl, str(m["n"]),
                      color(m["win_pct"], 55, 45, "{:.1f}"),
                      color(m["avg"], 0.1, 0),
                      color(m["cum"], 5, 0, "{:+.1f}"),
                      color(m["ir"], 0.2, 0),
                      f"[red]{m['max_dd']:.2f}[/red]")
    console.print(table)
    if m_tr["n"] > 0 and m_te["n"] > 0:
        consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0)
        tag = "[bold green]CONSISTENT[/bold green]" if consistent else "[bold red]REGIME BREAK[/bold red]"
        console.print(f"  → Train {m_tr['avg']:+.2f}% / Test {m_te['avg']:+.2f}% · {tag}")
    return m_tr, m_te


def final_verdict(m_r, m_z, wf_r, wf_z, console: Console):
    console.rule("[bold orange1]FINAL VERDICT · META-LAYER DISPATCH[/bold orange1]", style="orange1")
    gates = []
    for strat, m, wf, min_n in [
        ("REGIME_UP dispatched", m_r, wf_r, 10),
        ("Z_LONG_SPR dispatched", m_z, wf_z, 10),
    ]:
        gates.append((f"{strat} — sample ≥{min_n}", m["n"] >= min_n, f"N={m['n']}"))
        gates.append((f"{strat} — AVG% > 0", m["avg"] > 0, f"{m['avg']:+.2f}%"))
        gates.append((f"{strat} — IR > 0.2", m["ir"] > 0.2, f"IR {m['ir']:+.2f}"))
        if wf and wf[0] and wf[1]:
            m_tr, m_te = wf
            consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0) and m_te["avg"] > 0
            gates.append((f"{strat} — WF consistent+test>0", consistent,
                          f"tr {m_tr['avg']:+.2f}% / te {m_te['avg']:+.2f}%"))
        else:
            gates.append((f"{strat} — WF consistent+test>0", False, "insufficient data"))

    table = Table(box=box.SIMPLE_HEAVY, header_style="black on orange1")
    for c in ("GATE", "RESULT", "DETAIL"): table.add_column(c)
    for name, passed, detail in gates:
        mark = "[bold green]PASS[/bold green]" if passed else "[bold red]FAIL[/bold red]"
        table.add_row(name, mark, detail)
    console.print(table)
    passed_n = sum(1 for g in gates if g[1])
    total = len(gates)
    if passed_n == total:
        tag = "[bold green]✔ META-LAYER BEKERJA — layak paper trade[/bold green]"
    elif passed_n >= total - 2:
        tag = "[yellow]◆ BORDERLINE — edge lemah tapi ada[/yellow]"
    else:
        tag = "[bold red]✘ META-LAYER GAGAL — move to on-chain[/bold red]"
    console.print(f"\n{tag} · {passed_n}/{total} gate lolos")


def main():
    console = Console()
    console.rule("[bold orange1]META-LAYER DISPATCH · REGIME OVERLAY · 90D[/bold orange1]",
                 style="orange1")

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

    # Build regime timeline dari BTC
    btc = ohlcv_dict.get("BTC/USDT")
    if btc is None:
        console.print("[red]BTC/USDT data missing[/red]"); return
    console.print(f"Building regime timeline dari BTC ({len(btc)} bars)...")
    timeline = build_regime_timeline(btc)
    valid = timeline.dropna(subset=["label"])
    console.print(f"  {len(valid)} bars tagged · "
                  f"{timeline['vol_hourly'].dropna().median():.5f} median vol (hourly)")

    render_regime_distribution(valid, console)

    # Replay REGIME_UP with regime tag
    console.print("\nReplaying REGIME_UP 72h + tagging regime...")
    t0 = time.time()
    r_trades = replay_regime_up_tagged(ohlcv_dict, timeline)
    console.print(f"  {len(r_trades)} trades · {time.time()-t0:.1f}s")

    # Replay Z_SPIKE (use existing)
    console.print("Replaying Z_SPIKE...")
    t1 = time.time()
    all_z = replay_zspike(ohlcv_dict, pairs, console)
    long_z = [t for t in all_z if t.subtype == "Z_LONG_SPR"]
    console.print(f"  {len(all_z)} Z_SPIKE ({len(long_z)} LONG_SPR) · {time.time()-t1:.1f}s")

    # Per-market-regime breakdowns
    r_groups = render_regime_up_per_regime(r_trades, console)
    z_groups = render_zspike_per_regime(long_z, timeline, console)

    # Dispatched metrics
    m_r, m_z = render_dispatch_summary(r_groups, z_groups, console)

    # Walk-forward on dispatched
    wf_r = render_walk_forward(
        r_trades, "REGIME_UP @ low_vol_up", console,
        DISPATCH_REGIME_UP, lambda t: t["gross_ret"] - COST_SINGLE,
    )
    # For Z_SPIKE, convert Trade objects to tagged dicts
    z_tagged = [{"entry_ts": t.entry_ts, "gross_ret": t.returns.get(HORIZON_ZSPIKE),
                 "regime": regime_at(timeline, t.entry_ts)}
                for t in long_z if HORIZON_ZSPIKE in t.returns]
    wf_z = render_walk_forward(
        z_tagged, "Z_LONG_SPR @ high_vol_sideways", console,
        DISPATCH_ZSPIKE, lambda t: t["gross_ret"],  # already cost-adjusted
    )

    final_verdict(m_r, m_z, wf_r, wf_z, console)


if __name__ == "__main__":
    main()
