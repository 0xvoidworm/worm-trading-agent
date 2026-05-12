#!/usr/bin/env python3
"""Validation INVERTED — test hipotesis contrarian signal.

Hipotesis: regime classifier kita lagging → saat bilang "trending_up" sebenarnya
udah overbought, saat bilang "trending_down" udah oversold. Jadi fade direction
yang dikasih classifier = edge.

Test:
  - REGIME_UP direction: +1 (ORIGINAL long) vs -1 (INVERTED short)
  - REGIME_DOWN direction: -1 (ORIGINAL short) vs +1 (INVERTED long)
  - Horizons: 4h, 24h, 72h (cari mana yang paling payoff untuk contrarian)
  - 90 hari data full, all 8 symbols

Output: matrix comparison original vs inverted per (signal, horizon).
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
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


HORIZONS = [4, 24, 72]
COST = 0.0011


@dataclass
class RegSignal:
    symbol: str
    regime: str                          # 'trending_up' or 'trending_down'
    entry_ts: pd.Timestamp
    entry_price: float
    gross_rets: dict = field(default_factory=dict)  # horizon → gross (pre-direction)


def replay_all_regime(ohlcv_dict: dict[str, pd.DataFrame]) -> list[RegSignal]:
    """Emit SEMUA regime flip ke trending_up/down. Simpan gross return
    (post-cost, pre-direction) per horizon. Direction dikenakan saat aggregate."""
    sigs = []
    for sym, df in ohlcv_dict.items():
        if len(df) < WARMUP_BARS + max(HORIZONS):
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
            if snap.regime in ("trending_up", "trending_down"):
                entry = float(df.iloc[i]["close"])
                entry_ts = pd.Timestamp(df.iloc[i]["ts"])
                sig = RegSignal(sym, snap.regime, entry_ts, entry)
                for h in HORIZONS:
                    if i + h >= len(df):
                        continue
                    exitp = float(df.iloc[i + h]["close"])
                    # Gross price change (long-side, fractional). Direction applied later.
                    sig.gross_rets[h] = (exitp - entry) / entry
                sigs.append(sig)
            prev_regime = snap.regime
    return sigs


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


def compute(signals: list[RegSignal], regime: str, direction: int, h: int) -> dict:
    rets = []
    for s in signals:
        if s.regime != regime:
            continue
        if h not in s.gross_rets:
            continue
        rets.append(direction * s.gross_rets[h] - COST)
    return metrics(np.array(rets))


def color(v: float, good: float, bad: float, fmt: str = "{:+.2f}") -> str:
    s = fmt.format(v)
    if v >= good:
        return f"[bold green]{s}[/bold green]"
    if v < bad:
        return f"[red]{s}[/red]"
    return s


def render_matrix(signals: list[RegSignal], console: Console):
    console.rule("[bold orange1]MATRIX · ORIGINAL vs INVERTED · 90D · ALL 8 SYMBOLS[/bold orange1]",
                 style="orange1")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SIGNAL", "VERSION", "DIR", "H", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c in ("SIGNAL", "VERSION", "DIR", "H") else "right")

    # (regime, label, direction)
    variants = [
        ("trending_up",   "REGIME_UP  · ORIGINAL", "LONG  (+1)", +1),
        ("trending_up",   "REGIME_UP  · INVERTED", "SHORT (-1)", -1),
        ("trending_down", "REGIME_DN  · ORIGINAL", "SHORT (-1)", -1),
        ("trending_down", "REGIME_DN  · INVERTED", "LONG  (+1)", +1),
    ]
    last_signal = None
    for regime, label, dir_label, d in variants:
        signal_base = label.split("·")[0].strip()
        if last_signal and last_signal != signal_base:
            table.add_section()
        last_signal = signal_base
        for h in HORIZONS:
            m = compute(signals, regime, d, h)
            table.add_row(
                label.split("·")[0].strip() + (" " + label.split("·")[1].strip() if "INVERTED" in label else " ORIGINAL"),
                "INV" if "INVERTED" in label else "ORIG",
                dir_label,
                f"{h}h",
                str(m["n"]),
                color(m["win_pct"], 55, 45, "{:.1f}"),
                color(m["avg"], 0.1, 0),
                color(m["cum"], 5, 0, "{:+.1f}"),
                color(m["ir"], 0.3, 0),
                f"[red]{m['max_dd']:.2f}[/red]",
            )
    console.print(table)


def render_delta(signals: list[RegSignal], console: Console):
    """Side-by-side: CUM original vs inverted per horizon. Visualisasikan flip sign."""
    console.rule("[bold orange1]CUM% DELTA · ORIGINAL → INVERTED[/bold orange1]", style="orange1")
    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("SIGNAL", "HORIZON", "ORIG CUM%", "INV CUM%", "Δ", "FLIP SIGN?"):
        table.add_column(c, justify="left" if c in ("SIGNAL", "HORIZON") else "right")

    pairs = [
        ("REGIME_UP", "trending_up",   +1, -1),
        ("REGIME_DN", "trending_down", -1, +1),
    ]
    for label, regime, d_orig, d_inv in pairs:
        for h in HORIZONS:
            m_o = compute(signals, regime, d_orig, h)
            m_i = compute(signals, regime, d_inv, h)
            delta = m_i["cum"] - m_o["cum"]
            flipped = (m_o["cum"] < 0 and m_i["cum"] > 0) or (m_o["cum"] > 0 and m_i["cum"] < 0)
            flip_tag = "[bold green]YES[/bold green]" if flipped else "[dim]no[/dim]"
            table.add_row(
                label,
                f"{h}h",
                color(m_o["cum"], 5, 0, "{:+.1f}"),
                color(m_i["cum"], 5, 0, "{:+.1f}"),
                color(delta, 5, -5, "{:+.1f}"),
                flip_tag,
            )
    console.print(table)


def render_walk_forward_best(signals: list[RegSignal], console: Console):
    """Ambil varian terbaik (by IR), test walk-forward 60/30."""
    console.rule("[bold orange1]WALK-FORWARD VALIDATION · BEST VARIANT[/bold orange1]",
                 style="orange1")
    best = None
    for regime in ("trending_up", "trending_down"):
        for d in (+1, -1):
            for h in HORIZONS:
                m = compute(signals, regime, d, h)
                if m["n"] < 30:
                    continue
                if best is None or m["ir"] > best["ir"]:
                    best = {"regime": regime, "dir": d, "h": h, **m}

    if best is None:
        console.print("[yellow]no variant with enough trades[/yellow]")
        return

    label = f"{best['regime']} · dir={'+1' if best['dir'] > 0 else '-1'} · {best['h']}h"
    console.print(f"  Best by IR: [bold]{label}[/bold] · "
                  f"N={best['n']} · CUM={best['cum']:+.1f}% · IR={best['ir']:+.2f}")

    matching = sorted([s for s in signals
                       if s.regime == best["regime"] and best["h"] in s.gross_rets],
                      key=lambda s: s.entry_ts)
    if not matching:
        return
    t_min, t_max = matching[0].entry_ts, matching[-1].entry_ts
    span = (t_max - t_min).total_seconds()
    split = t_min + pd.Timedelta(seconds=span * 2 / 3)

    train_rets = [best["dir"] * s.gross_rets[best["h"]] - COST
                  for s in matching if s.entry_ts < split]
    test_rets = [best["dir"] * s.gross_rets[best["h"]] - COST
                 for s in matching if s.entry_ts >= split]
    m_tr = metrics(np.array(train_rets))
    m_te = metrics(np.array(test_rets))

    table = Table(box=box.HEAVY, header_style="black on orange1")
    for c in ("WINDOW", "N", "WIN%", "AVG%", "CUM%", "IR", "MAX_DD%"):
        table.add_column(c, justify="left" if c == "WINDOW" else "right")
    for lbl, m in [(f"TRAIN ~60d", m_tr), (f"TEST  ~30d", m_te)]:
        table.add_row(
            lbl,
            str(m["n"]),
            color(m["win_pct"], 55, 45, "{:.1f}"),
            color(m["avg"], 0.1, 0),
            color(m["cum"], 5, 0, "{:+.1f}"),
            color(m["ir"], 0.3, 0),
            f"[red]{m['max_dd']:.2f}[/red]",
        )
    console.print(table)
    if m_tr["n"] > 0 and m_te["n"] > 0:
        consistent = (m_tr["avg"] > 0) == (m_te["avg"] > 0)
        tag = "[bold green]CONSISTENT[/bold green]" if consistent else "[bold red]REGIME BREAK[/bold red]"
        console.print(f"  → Δ AVG {m_te['avg'] - m_tr['avg']:+.2f}pp · {tag}")


def main():
    console = Console()
    console.rule("[bold orange1]INVERSION TEST · CONTRARIAN HYPOTHESIS · 90D[/bold orange1]",
                 style="orange1")

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        ohlcv_dict = load_ohlcv(conn)
    finally:
        conn.close()

    cutoff = pd.Timestamp.now(tz=None) - pd.Timedelta(days=90)
    ohlcv_dict = {s: df[df["ts"] >= cutoff].reset_index(drop=True)
                  for s, df in ohlcv_dict.items()}
    total = sum(len(df) for df in ohlcv_dict.values())
    console.print(f"Loaded {len(ohlcv_dict)} symbols · {total} bars")

    console.print("Replaying regime (both directions, all horizons)...")
    t0 = time.time()
    signals = replay_all_regime(ohlcv_dict)
    up = sum(1 for s in signals if s.regime == "trending_up")
    dn = sum(1 for s in signals if s.regime == "trending_down")
    console.print(f"  {len(signals)} signals ({up} UP + {dn} DOWN) · {time.time()-t0:.1f}s")

    render_matrix(signals, console)
    render_delta(signals, console)
    render_walk_forward_best(signals, console)


if __name__ == "__main__":
    main()
