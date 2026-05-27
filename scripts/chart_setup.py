#!/usr/bin/env python3
"""Render annotated candlestick chart (live OHLCV via ccxt) + entry/SL/TP + R:R zones.

Argv-driven & reusable buat setup apapun. Tema gelap.

Usage:
  python -m scripts.chart_setup --symbol WIF/USDT:USDT --entry 0.1835 \\
      --sl 0.1765 --tp 0.2040 --side LONG --out wif_setup2.png \\
      --title "WIF LONG — Setup #2"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_pipe.futures_broker import make_futures_broker


def fmt(p: float) -> str:
    return f"{p:,.0f}" if abs(p) >= 100 else f"{p:.4f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--nbars", type=int, default=240)
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--sl", type=float, required=True)
    ap.add_argument("--tp", type=float, required=True)
    ap.add_argument("--side", default="LONG")
    ap.add_argument("--title", default="")
    ap.add_argument("--out", default="setup.png")
    a = ap.parse_args()

    br = make_futures_broker(live=False)
    ex = br._ex
    ex.load_markets()
    oh = ex.fetch_ohlcv(a.symbol, a.tf, limit=a.nbars)
    o = np.array([r[1] for r in oh]); h = np.array([r[2] for r in oh])
    l = np.array([r[3] for r in oh]); c = np.array([r[4] for r in oh])
    x = np.arange(len(c))

    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor("#0a0a0a")
    ax.set_facecolor("#0a0a0a")
    for i in x:
        col = "#26a69a" if c[i] >= o[i] else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color=col, linewidth=0.7, zorder=1)
        ax.plot([i, i], [o[i], c[i]], color=col, linewidth=2.4, zorder=2)

    # R:R zones (side-agnostic: axhspan shades between the two values)
    ax.axhspan(a.entry, a.sl, color="#ef5350", alpha=0.08, zorder=0)   # risk
    ax.axhspan(a.entry, a.tp, color="#26a69a", alpha=0.08, zorder=0)   # reward
    risk_pct = abs(a.sl - a.entry) / a.entry * 100
    rew_pct = abs(a.tp - a.entry) / a.entry * 100
    for price, color, label in [
        (a.entry, "#e0e0e0", f"ENTRY {fmt(a.entry)}"),
        (a.sl, "#ef5350", f"SL {fmt(a.sl)}  (-{risk_pct:.1f}%)"),
        (a.tp, "#26a69a", f"TP {fmt(a.tp)}  (+{rew_pct:.1f}%)"),
    ]:
        ax.axhline(price, color=color, ls="--", lw=1.3, zorder=3)
        ax.text(1, price, f"  {label}", color=color, va="bottom", fontsize=9, zorder=4)

    last = float(c[-1])
    ax.scatter([len(x) - 1], [last], color="#ffd54f", s=30, zorder=5)
    ax.text(len(x) - 1, last, f"  now {fmt(last)}", color="#ffd54f", va="center", fontsize=9)

    rr = abs(a.tp - a.entry) / max(abs(a.sl - a.entry), 1e-12)
    title = a.title or f"{a.symbol} {a.side}"
    ax.set_title(f"{title}   |   R:R {rr:.2f}:1", color="#e0e0e0", fontsize=12)
    ax.tick_params(colors="#888")
    for s in ax.spines.values():
        s.set_color("#333")
    ax.grid(True, color="#1a1a1a", lw=0.5)
    ax.set_xlim(-2, len(x) + 14)

    out = Path.home() / "trading_agent" / "data" / "charts" / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(str(out), dpi=110, facecolor=fig.get_facecolor())
    print(f"saved: {out} | {a.symbol} {a.side} | bars={len(c)} | now={fmt(last)} | R:R={rr:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
