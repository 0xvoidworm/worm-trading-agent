#!/usr/bin/env python3
"""Backtest market-neutral pairs long/short (futures expr) + walk-forward OOS GATE.

Focused test pada pair VALIDATED (spot_pairs.PAIR_BACKTEST_VERDICT) di ekspresi
futures-native (true short, fee perp 0.16% round-trip). Uji walk-forward OOS yg
bunuh funding-carry. Pakai rolling_cointegration_panel buat spread z-score + β.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/backtest_pairs_neutral.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import duckdb

from data_pipe.cointegration import rolling_cointegration_panel
from data_pipe.pairs_neutral import simulate_pairs

DB = Path.home() / "trading_agent" / "data" / "market.duckdb"
EXCHANGE, TF = "bybit", "1h"

# Pair VALIDATED dari kerja spot sebelumnya (conviction order)
CANDIDATES = [
    ("NEAR/USDT", "AVAX/USDT"),  # CPCV_ROBUST (juara — Sharpe 1.47 di spot)
    ("AVAX/USDT", "SOL/USDT"),   # MARGINAL
    ("DOGE/USDT", "BTC/USDT"),   # MARGINAL (dual-validated)
    ("TRX/USDT", "ETH/USDT"),    # MARGINAL (dual-validated)
    ("APT/USDT", "SOL/USDT"),    # MARGINAL
    ("TON/USDT", "ETH/USDT"),    # MARGINAL
    ("SUI/USDT", "SOL/USDT"),    # MARGINAL
]


def load_close(con, sym):
    return con.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [EXCHANGE, sym, TF],
    ).df()


def build_panel(con, a, b):
    da, db = load_close(con, a), load_close(con, b)
    if da.empty or db.empty:
        return None
    panel = rolling_cointegration_panel(
        da, db, (a, b), lookback_bars=720, refit_freq_bars=24, z_window=50,
    )
    if panel is None or panel.empty:
        return None
    # gabung harga leg beneran (buat PnL price-based, β dikunci saat entry)
    panel = panel.merge(da.rename(columns={"close": "close_a"}), on="ts", how="left")
    panel = panel.merge(db.rename(columns={"close": "close_b"}), on="ts", how="left")
    return panel


def main() -> int:
    tmp = Path(tempfile.gettempdir()) / "pairs_bt_snap.duckdb"
    shutil.copy(DB, tmp)
    con = duckdb.connect(str(tmp), read_only=True)

    print("=== PAIRS LONG/SHORT BACKTEST (futures expr, bybit 3yr) ===")
    print("entry z=2.0 exit z=0.5 kill z=4.0 fee 0.16% rt | walk-forward IS70/OOS30\n")
    hdr = (f"{'pair':20s} {'trd':>4s} {'ann%':>7s} {'win%':>6s} {'shrpe':>6s} "
           f"{'maxDD':>6s} {'coint%':>6s} {'IS_ann':>7s} {'OOS_ann':>8s} persist")
    print(hdr)
    print("-" * len(hdr))

    oos_pos, total = 0, 0
    for a, b in CANDIDATES:
        panel = build_panel(con, a, b)
        label = f"{a.split('/')[0]}|{b.split('/')[0]}"
        if panel is None or panel.empty:
            print(f"{label:20s}  no data / insufficient")
            continue
        full = simulate_pairs(panel, pair=label)
        cut = int(len(panel) * 0.70)
        is_s = simulate_pairs(panel.iloc[:cut], pair=label)
        oos_s = simulate_pairs(panel.iloc[cut:], pair=label)
        ok = oos_s.ann_return_pct > 0
        oos_pos += ok
        total += 1
        print(f"{label:20s} {full.n_trades:4d} {full.ann_return_pct:7.2f} "
              f"{full.win_rate_pct:6.1f} {full.sharpe_trades:6.2f} "
              f"{full.max_drawdown_pct:6.2f} {full.pct_cointegrated_bars:6.0f} "
              f"{is_s.ann_return_pct:7.2f} {oos_s.ann_return_pct:8.2f}  {'OK' if ok else 'x'}")

    print(f"\n--- GATE ASSESSMENT ---")
    print(f"OOS-positive: {oos_pos}/{total} pairs")
    passed = total > 0 and oos_pos >= total * 0.6
    print(f"VERDICT: {'lanjut refine + Binance funding' if passed else 'pairs gak survive OOS — thesis exhausted, rethink total'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
