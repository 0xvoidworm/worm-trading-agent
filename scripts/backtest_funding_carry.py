#!/usr/bin/env python3
"""Backtest funding-carry lintas simbol + validasi CPCV-style (GATE).

Load 3thn funding dari market.duckdb (snapshot copy biar gak ganggu daemon),
simulasi carry per simbol, fold-analysis (10 chunk) → PBO + Deflated Sharpe.

Output: ranking kandidat + tabel verdict per simbol + GATE assessment.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/backtest_funding_carry.py
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb
import numpy as np
from scipy import stats

from data_pipe.cpcv import (
    CPCVResult, assign_verdicts, compute_deflated_sharpe, compute_pbo,
)
from data_pipe.funding_carry import (
    infer_interval_hours, rank_carry_candidates, simulate_carry,
)

DB = Path.home() / "trading_agent" / "data" / "market.duckdb"
N_FOLDS = 10
# Sticky/smoothed config: putuskan di rolling funding (~3 hari), tahan transient
# dip, keluar cuma kalau funding sustained negatif → amortize fee over long hold.
ENTRY_BPS = 0.1
EXIT_BPS = -0.5
SMOOTH = 9


def load_funding(con) -> dict:
    syms = [r[0] for r in con.execute(
        "SELECT DISTINCT symbol FROM funding_rate ORDER BY symbol").fetchall()]
    out = {}
    for s in syms:
        df = con.execute(
            "SELECT ts, funding, mark_price FROM funding_rate "
            "WHERE symbol=? ORDER BY ts", [s],
        ).df()
        df = df.drop_duplicates(subset=["ts"]).reset_index(drop=True)
        if len(df) > 50:
            out[s] = df
    return out


def fold_analysis(df, symbol, interval_hours, n_folds=N_FOLDS):
    """Split kontigu jadi n_folds chunk, simulasi tiap chunk.
    Selalu return n_folds nilai (0.0 kalau chunk gak ada trade) → struktur
    konsisten buat PBO.
    """
    fold_returns, fold_sharpes = [], []
    n_trades_total = 0
    for chunk in np.array_split(np.arange(len(df)), n_folds):
        if len(chunk) < 10:
            fold_returns.append(0.0)
            fold_sharpes.append(0.0)
            continue
        sub = df.iloc[chunk[0]:chunk[-1] + 1]
        summ = simulate_carry(
            sub, symbol=symbol, entry_bps=ENTRY_BPS, exit_bps=EXIT_BPS,
            smooth_intervals=SMOOTH, interval_hours=interval_hours)
        fold_returns.append(summ.total_net_pct)
        fold_sharpes.append(summ.sharpe_trades)
        n_trades_total += summ.n_trades
    return fold_returns, fold_sharpes, n_trades_total


def walk_forward(df, symbol, interval_hours, is_frac=0.70):
    """IS/OOS temporal split — uji edge persist di data belakangan (unseen)."""
    cut = int(len(df) * is_frac)
    kw = dict(entry_bps=ENTRY_BPS, exit_bps=EXIT_BPS,
              smooth_intervals=SMOOTH, interval_hours=interval_hours)
    is_s = simulate_carry(df.iloc[:cut], symbol=symbol, **kw)
    oos_s = simulate_carry(df.iloc[cut:], symbol=symbol, **kw)
    return is_s.ann_return_pct, oos_s.ann_return_pct


def main() -> int:
    if not DB.exists():
        print(f"[FAIL] db not found: {DB}")
        return 1
    tmp = Path(tempfile.gettempdir()) / "carry_bt_snap.duckdb"
    shutil.copy(DB, tmp)
    con = duckdb.connect(str(tmp), read_only=True)

    funding = load_funding(con)
    n_syms = len(funding)
    print(f"=== FUNDING-CARRY BACKTEST ({n_syms} symbols, source=bybit 3yr) ===")
    print(f"config: entry>={ENTRY_BPS}bp exit<{EXIT_BPS}bp smooth={SMOOTH} (sticky, anti-churn)\n")

    # candidate ranking
    rank = rank_carry_candidates(funding)
    print("--- Candidate ranking (by avg funding) ---")
    print(rank.to_string(index=False))
    print()

    # full-period sim + fold analysis
    summaries, cpcv_results = {}, {}
    for s, df in funding.items():
        ih = infer_interval_hours(df["ts"])
        full = simulate_carry(
            df, symbol=s, entry_bps=ENTRY_BPS, exit_bps=EXIT_BPS,
            smooth_intervals=SMOOTH, interval_hours=ih)
        summaries[s] = full
        fr, fs, nt = fold_analysis(df, s, ih)
        arr, arrs = np.array(fr), np.array(fs)
        mean_sh, std_sh = float(arrs.mean()), float(arrs.std())
        if len(arrs) > 1 and std_sh > 1e-9:
            t_stat = mean_sh / (std_sh / math.sqrt(len(arrs)))
            p_val = float(1 - stats.t.cdf(t_stat, df=len(arrs) - 1))
        else:
            t_stat, p_val = 0.0, 1.0
        cpcv_results[s] = CPCVResult(
            strategy_label=s, n_folds=len(fr), n_total_trades=nt,
            fold_returns=fr, fold_sharpes=fs,
            mean_return=float(arr.mean()), std_return=float(arr.std()),
            median_return=float(np.median(arr)),
            pct_positive_folds=float((arr > 0).mean() * 100),
            mean_sharpe=mean_sh, std_sharpe=std_sh,
            sharpe_t_stat=t_stat, sharpe_p_value=p_val,
        )

    pbo = compute_pbo(cpcv_results) if len(cpcv_results) >= 2 else 0.0
    for s, r in cpcv_results.items():
        r.deflated_sharpe = compute_deflated_sharpe(
            summaries[s].sharpe_trades, n_trials=max(n_syms, 2),
            n_observations=max(summaries[s].n_trades, 2),
        )
    assign_verdicts(cpcv_results, pbo)

    # verdict table
    print("--- Per-symbol backtest + validation ---")
    hdr = f"{'sym':10s} {'trades':>6s} {'ann%':>7s} {'win%':>6s} {'shrpe':>6s} {'DSR':>5s} {'%pos':>5s} {'inMkt%':>6s} {'maxDD':>6s}  verdict"
    print(hdr)
    print("-" * len(hdr))
    for s in sorted(summaries, key=lambda x: -summaries[x].ann_return_pct):
        su, cv = summaries[s], cpcv_results[s]
        print(f"{s:10s} {su.n_trades:6d} {su.ann_return_pct:7.2f} {su.win_rate_pct:6.1f} "
              f"{su.sharpe_trades:6.2f} {cv.deflated_sharpe:5.2f} {cv.pct_positive_folds:5.0f} "
              f"{su.pct_time_in_market:6.1f} {su.max_drawdown_pct:6.2f}  {cv.verdict}")

    # ── walk-forward IS/OOS — overfit test yang TEPAT buat carry ──
    # (PBO-antar-symbol bising: symbol berkorelasi, strategi sama. trade-level
    #  DSR underpowered: sticky = sedikit trade. Uji bener: persist di OOS?)
    print("\n--- Walk-forward IS(70%)/OOS(30%) ann% ---")
    print(f"{'sym':10s} {'IS_ann%':>8s} {'OOS_ann%':>9s}  persist")
    oos_vals, oos_pos = [], 0
    for s in sorted(summaries, key=lambda x: -summaries[x].ann_return_pct):
        ih = infer_interval_hours(funding[s]["ts"])
        is_a, oos_a = walk_forward(funding[s], s, ih)
        oos_vals.append(oos_a)
        oos_pos += oos_a > 0
        print(f"{s:10s} {is_a:8.2f} {oos_a:9.2f}  {'OK' if oos_a > 0 else 'x'}")
    oos_mean = float(np.mean(oos_vals))

    print(f"\n--- GATE ASSESSMENT (honest, multi-factor) ---")
    pos_syms = [s for s in summaries if summaries[s].ann_return_pct > 0]
    avg_dd = float(np.mean([summaries[s].max_drawdown_pct for s in summaries]))
    print(f"PBO across-symbols : {pbo*100:.1f}%  (CAVEAT: bising — symbol berkorelasi, strategi sama)")
    print(f"Net-positive (full): {len(pos_syms)}/{len(summaries)} symbols")
    print(f"OOS-positive (WF)  : {oos_pos}/{len(summaries)} symbols, avg OOS {oos_mean:+.2f}%/yr")
    print(f"Avg max drawdown   : {avg_dd:.2f}%  (market-neutral)")
    passed = oos_pos >= len(summaries) * 0.6 and oos_mean > 3.0
    print(f"\nVERDICT: {'REAL-BUT-MODEST (edge OOS-persist, low DD). Layak live KECIL sbg proving run + refine Binance funding.' if passed else 'Edge gak persist OOS - pivot ke thesis pairs.'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
