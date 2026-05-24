#!/usr/bin/env python3
"""Log-spread backtest — proper β-weighted spread instead of raw ratio.

Standard pair-trading rigor (Chan, "Algorithmic Trading" Ch 2):
  spread_t = log(A_t) - β · log(B_t)
where β = OLS hedge ratio from historical regression.

This is what Engle-Granger cointegration TESTS on. If pair is cointegrated,
spread is stationary → mean reversion expected.

vs. Raw ratio A/B (current strategy):
  - Implicit β = 1
  - Geometric mean-revert assumption
  - Drift-prone if true β ≠ 1

Output: data/backtest_results/log_spread_<date>.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"

DEFAULT_LOOKBACK = 720          # 30d of 1h for rolling z
DEFAULT_Z_ENTRY = 2.0
DEFAULT_Z_EXIT = 0.0
DEFAULT_MAX_HOLD_DAYS = 14
DEFAULT_STOP_LOSS_PCT = 10.0
DEFAULT_FEE_PCT_SIDE = 0.10


def _fetch_pair_log_prices(conn, pa, pb, exchange="bybit", tf="1h"):
    df_a = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pa, tf],
    ).fetchdf()
    df_b = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pb, tf],
    ).fetchdf()
    if df_a.empty or df_b.empty:
        return pd.DataFrame()
    merged = pd.merge(
        df_a.rename(columns={"close": "a"}),
        df_b.rename(columns={"close": "b"}),
        on="ts", how="inner",
    )
    if merged.empty:
        return pd.DataFrame()
    merged["log_a"] = np.log(merged["a"])
    merged["log_b"] = np.log(merged["b"])
    return merged


def _estimate_hedge_beta(merged: pd.DataFrame, train_frac: float = 0.5) -> float:
    """OLS β from first `train_frac` of data (avoid look-ahead bias)."""
    if merged.empty:
        return 1.0
    n_train = int(len(merged) * train_frac)
    train = merged.iloc[:n_train]
    if len(train) < 50:
        return 1.0
    # log_a = α + β * log_b
    beta = np.polyfit(train["log_b"].values, train["log_a"].values, 1)[0]
    return float(beta)


def _backtest_log_spread(
    merged: pd.DataFrame, pa: str, pb: str,
    lookback: int = DEFAULT_LOOKBACK,
    z_entry: float = DEFAULT_Z_ENTRY,
    z_exit: float = DEFAULT_Z_EXIT,
    max_hold_h: int = DEFAULT_MAX_HOLD_DAYS * 24,
    sl_pct: float = DEFAULT_STOP_LOSS_PCT,
    fee_pct_side: float = DEFAULT_FEE_PCT_SIDE,
    train_frac: float = 0.5,
) -> dict:
    """Backtest log-spread strategy. Uses train_frac for β estimation,
    runs strategy on remaining 1-train_frac period (avoid look-ahead).
    """
    if merged.empty:
        return {"pair": f"{pa}/{pb}", "n_trades": 0, "skipped": "no data"}

    beta = _estimate_hedge_beta(merged, train_frac=train_frac)
    # Restrict to OUT-OF-SAMPLE: after train period
    n_train = int(len(merged) * train_frac)
    test_merged = merged.iloc[n_train:].copy().reset_index(drop=True)
    if len(test_merged) < lookback + 50:
        return {"pair": f"{pa}/{pb}", "n_trades": 0, "skipped": "insufficient OOS bars"}

    # Spread: log_a - β * log_b
    test_merged["spread"] = test_merged["log_a"] - beta * test_merged["log_b"]
    test_merged["mean"] = test_merged["spread"].rolling(lookback).mean()
    test_merged["std"] = test_merged["spread"].rolling(lookback).std()
    test_merged["z"] = (test_merged["spread"] - test_merged["mean"]) / test_merged["std"]
    test_merged["above"] = test_merged["z"] >= z_entry
    test_merged["below"] = test_merged["z"] <= -z_entry
    test_merged["fresh_above"] = test_merged["above"] & ~test_merged["above"].shift(1, fill_value=False)
    test_merged["fresh_below"] = test_merged["below"] & ~test_merged["below"].shift(1, fill_value=False)

    trades = []
    in_trade = False
    entry_idx = None
    entry_side = None
    mfe_pct = 0.0
    mae_pct = 0.0
    fee_round = 2 * fee_pct_side

    for i in range(lookback, len(test_merged)):
        z = test_merged.at[i, "z"]
        if pd.isna(z):
            continue
        if not in_trade:
            if test_merged.at[i, "fresh_below"]:
                # z < -2 → spread is low → log_a relatively cheap → BUY A
                in_trade = True; entry_idx = i; entry_side = "BUY_A"
                mfe_pct = mae_pct = 0.0
            elif test_merged.at[i, "fresh_above"]:
                # z > +2 → spread is high → log_a relatively expensive → BUY B (it's cheap relative)
                in_trade = True; entry_idx = i; entry_side = "BUY_B"
                mfe_pct = mae_pct = 0.0
        else:
            if entry_side == "BUY_A":
                ep = test_merged.at[entry_idx, "a"]; cp = test_merged.at[i, "a"]
            else:
                ep = test_merged.at[entry_idx, "b"]; cp = test_merged.at[i, "b"]
            pct = (cp - ep) / ep * 100
            if pct > mfe_pct: mfe_pct = pct
            if pct < mae_pct: mae_pct = pct
            age_h = (i - entry_idx)
            exit_reason = None
            if entry_side == "BUY_A" and z >= z_exit:
                exit_reason = "Z_REVERT"
            elif entry_side == "BUY_B" and z <= z_exit:
                exit_reason = "Z_REVERT"
            elif pct <= -sl_pct:
                exit_reason = "STOP_LOSS"
            elif age_h >= max_hold_h:
                exit_reason = "TIME_STOP"
            if exit_reason:
                trades.append({
                    "entry_ts": test_merged.at[entry_idx, "ts"],
                    "exit_ts": test_merged.at[i, "ts"],
                    "side": entry_side,
                    "entry_z": float(test_merged.at[entry_idx, "z"]),
                    "exit_z": float(z),
                    "entry_price": float(ep),
                    "exit_price": float(cp),
                    "pnl_pct_net": float(pct - fee_round),
                    "hold_h": int(age_h),
                    "exit_reason": exit_reason,
                    "mfe_pct": float(mfe_pct),
                    "mae_pct": float(mae_pct),
                })
                in_trade = False; entry_idx = None; entry_side = None
                mfe_pct = mae_pct = 0.0

    if not trades:
        return {
            "pair": f"{pa}/{pb}", "beta": beta, "n_trades": 0,
            "notes": "no signals fired in OOS period",
        }
    df = pd.DataFrame(trades)
    return {
        "pair": f"{pa}/{pb}",
        "beta": beta,
        "n_trades": len(df),
        "n_wins": int((df["pnl_pct_net"] > 0).sum()),
        "win_rate_pct": float((df["pnl_pct_net"] > 0).mean() * 100),
        "avg_pct_net": float(df["pnl_pct_net"].mean()),
        "median_pct_net": float(df["pnl_pct_net"].median()),
        "total_compound_pct": float(((1 + df["pnl_pct_net"] / 100).prod() - 1) * 100),
        "best_pct_net": float(df["pnl_pct_net"].max()),
        "worst_pct_net": float(df["pnl_pct_net"].min()),
        "median_hold_h": float(df["hold_h"].median()),
        "median_mfe_pct": float(df["mfe_pct"].median()),
        "median_mae_pct": float(df["mae_pct"].median()),
        "by_exit": df.groupby("exit_reason")["pnl_pct_net"].agg(["count", "mean"]).to_dict(),
    }


def _verdict(r: dict) -> str:
    n = r.get("n_trades", 0)
    if n == 0:
        return "NO_SIGNALS"
    if n < 10:
        return "INSUFFICIENT_SAMPLE"
    wr = r.get("win_rate_pct", 0)
    avg = r.get("avg_pct_net", 0)
    total = r.get("total_compound_pct", 0)
    if wr >= 55 and avg > 0 and total > 5:
        return "STABLE_POSITIVE"
    if wr >= 50 and avg > 0:
        return "MARGINAL_POSITIVE"
    if wr < 40 and avg < 0:
        return "STABLE_NEGATIVE"
    return "INCONSISTENT"


# All pairs we currently track + the 3 cointegrated STABLE_NEGATIVEs
PAIRS_TO_TEST = [
    # Currently validated
    ("NEAR/USDT", "AVAX/USDT"),
    ("AVAX/USDT", "SOL/USDT"),
    ("DOGE/USDT", "BTC/USDT"),
    ("TRX/USDT", "ETH/USDT"),
    ("APT/USDT", "SOL/USDT"),
    ("TON/USDT", "ETH/USDT"),
    ("SUI/USDT", "SOL/USDT"),
    ("SUI/USDT", "ETH/USDT"),
    # Cointegrated but labeled NEG — see if log-spread reveals edge
    ("CAKE/USDT", "UNI/USDT"),
    ("POL/USDT", "ARB/USDT"),
    ("FET/USDT", "WLD/USDT"),
    # Other notables for comparison
    ("BNB/USDT", "BTC/USDT"),
    ("ETH/USDT", "BTC/USDT"),
    ("ARB/USDT", "OP/USDT"),
    ("SOL/USDT", "ETH/USDT"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--train-frac", type=float, default=0.5)
    p.add_argument("--z", type=float, default=DEFAULT_Z_ENTRY)
    p.add_argument("--hold", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    args = p.parse_args()

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    max_hold_h = args.hold * 24
    print(f"[log_spread] testing {len(PAIRS_TO_TEST)} pairs (train_frac={args.train_frac}, z={args.z}, hold={args.hold}d)\n")
    results = []
    try:
        for i, (pa, pb) in enumerate(PAIRS_TO_TEST, 1):
            merged = _fetch_pair_log_prices(conn, pa, pb)
            r = _backtest_log_spread(
                merged, pa, pb,
                lookback=DEFAULT_LOOKBACK, z_entry=args.z, z_exit=DEFAULT_Z_EXIT,
                max_hold_h=max_hold_h, sl_pct=DEFAULT_STOP_LOSS_PCT,
                fee_pct_side=DEFAULT_FEE_PCT_SIDE,
                train_frac=args.train_frac,
            )
            verdict = _verdict(r)
            beta_s = f"β={r.get('beta', 0):.3f}" if r.get('beta') else ""
            n = r.get("n_trades", 0)
            avg = r.get("avg_pct_net", 0)
            wr = r.get("win_rate_pct", 0)
            comp = r.get("total_compound_pct", 0)
            print(f"[{i}/{len(PAIRS_TO_TEST)}] {pa}/{pb}: {beta_s} n={n} wr={wr:.1f}% avg={avg:+.3f}% comp={comp:+.2f}% → {verdict}")
            results.append({**r, "verdict": verdict})
    finally:
        conn.close()

    # Render report
    lines = []
    lines.append(f"# Log-Spread Backtest — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"**Method:** spread = log(A) - β·log(B), β from OLS on first "
                 f"{int(args.train_frac*100)}% of data (train), backtest on remaining "
                 f"(out-of-sample). Z-score on rolling spread.")
    lines.append(f"\nParams: z_entry=±{args.z}, hold={args.hold}d, SL 10%, fee 0.1%/side.\n")
    lines.append("## Summary Matrix\n")
    lines.append("| Pair | β | N | Win% | Avg Net% | Median Net% | Compound% | Best | Worst | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        n = r.get("n_trades", 0)
        if n == 0:
            lines.append(f"| {r['pair']} | {r.get('beta', 0):.3f} | 0 | — | — | — | — | — | — | NO_SIGNALS |")
            continue
        lines.append(
            f"| {r['pair']} | {r['beta']:.3f} | {n} | "
            f"{r['win_rate_pct']:.1f} | {r['avg_pct_net']:+.3f} | "
            f"{r['median_pct_net']:+.3f} | {r['total_compound_pct']:+.2f} | "
            f"{r['best_pct_net']:+.2f} | {r['worst_pct_net']:+.2f} | {r['verdict']} |"
        )

    md = "\n".join(lines) + "\n"
    out = OUT_DIR / f"log_spread_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.write_text(md)
    print(f"\n[log_spread] Report written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
