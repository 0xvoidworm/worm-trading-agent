#!/usr/bin/env python3
"""Historical backtest for spot pair z-score strategy.

For each default pair, scan 3y of 1h OHLCV. For each fresh signal
(|z| crossing threshold from inside band), simulate trade:
  - Entry: market buy "cheap" side at signal close
  - Exit conditions (whichever first):
      * z reverts to 0 (target hit)
      * stop loss -X% from entry
      * max hold N days timeout
  - Fees: 0.1%/side spot (default Binance taker)

Aggregate per-pair: n_trades, win_rate, avg_pct, total_return,
median_hold_days, max_drawdown_per_trade.

Output: data/backtest_results/spot_pairs_<date>.md

Usage:
    python scripts/backtest_spot_pairs.py
    python scripts/backtest_spot_pairs.py --z 1.5 --hold 7  # custom params
    python scripts/backtest_spot_pairs.py --pair ETH/USDT,BTC/USDT  # single pair
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

from data_pipe.spot_pairs import DEFAULT_PAIRS  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"

# Backtest defaults
DEFAULT_LOOKBACK_BARS = 720       # 30 days of 1h bars for rolling stats
DEFAULT_Z_ENTRY = 2.0             # entry when |z| crosses ±2
DEFAULT_Z_EXIT = 0.0              # exit when z reverts to 0
DEFAULT_MAX_HOLD_DAYS = 14        # time stop
DEFAULT_STOP_LOSS_PCT = 10.0      # SL pct
DEFAULT_FEE_PCT_SIDE = 0.10       # Binance spot taker


class ROStorage:
    def __init__(self, path):
        self._conn = duckdb.connect(str(path), read_only=True)
    def close(self):
        self._conn.close()


def _fetch_pair_ohlcv(conn, pair_a, pair_b, exchange="bybit", tf="1h"):
    df_a = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pair_a, tf],
    ).fetchdf()
    df_b = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pair_b, tf],
    ).fetchdf()
    if df_a.empty or df_b.empty:
        return pd.DataFrame()
    merged = pd.merge(
        df_a.rename(columns={"close": "a"}),
        df_b.rename(columns={"close": "b"}),
        on="ts", how="inner",
    )
    return merged


def _backtest_pair(
    merged: pd.DataFrame, pair_a: str, pair_b: str,
    lookback: int, z_entry: float, z_exit: float,
    max_hold_h: int, sl_pct: float, fee_pct_side: float,
) -> dict:
    """Walk forward through merged price df, simulate trades."""
    if len(merged) < lookback + 50:
        return {"pair": f"{pair_a}/{pair_b}", "skipped": "insufficient bars", "n_trades": 0}

    merged = merged.reset_index(drop=True)
    merged["ratio"] = merged["a"] / merged["b"]
    merged["mean"] = merged["ratio"].rolling(lookback).mean()
    merged["std"] = merged["ratio"].rolling(lookback).std()
    merged["z"] = (merged["ratio"] - merged["mean"]) / merged["std"]
    # Mark fresh crossings (was in band, now exceeded)
    merged["above"] = merged["z"] >= z_entry
    merged["below"] = merged["z"] <= -z_entry
    merged["fresh_above"] = merged["above"] & ~merged["above"].shift(1, fill_value=False)
    merged["fresh_below"] = merged["below"] & ~merged["below"].shift(1, fill_value=False)

    trades = []
    in_trade = False
    entry_idx = None
    entry_side = None  # "BUY_A" or "BUY_B"
    mfe_pct = 0.0      # max favorable excursion during current trade
    mae_pct = 0.0      # max adverse excursion (negative number)

    fee_round_pct = 2 * fee_pct_side

    for i in range(lookback, len(merged)):
        z = merged.at[i, "z"]
        if pd.isna(z):
            continue
        if not in_trade:
            if merged.at[i, "fresh_below"]:
                in_trade = True
                entry_idx = i
                entry_side = "BUY_A"
                mfe_pct = 0.0; mae_pct = 0.0
            elif merged.at[i, "fresh_above"]:
                in_trade = True
                entry_idx = i
                entry_side = "BUY_B"
                mfe_pct = 0.0; mae_pct = 0.0
        else:
            assert entry_idx is not None
            # Compute current %change for entered leg
            if entry_side == "BUY_A":
                entry_price = merged.at[entry_idx, "a"]
                cur_price = merged.at[i, "a"]
            else:
                entry_price = merged.at[entry_idx, "b"]
                cur_price = merged.at[i, "b"]
            pct = (cur_price - entry_price) / entry_price * 100
            # Track MFE/MAE intra-trade
            if pct > mfe_pct:
                mfe_pct = pct
            if pct < mae_pct:
                mae_pct = pct
            age_h = (i - entry_idx)  # 1h bars → hours
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
                pnl_pct_net = pct - fee_round_pct
                trades.append({
                    "entry_ts": merged.at[entry_idx, "ts"],
                    "exit_ts": merged.at[i, "ts"],
                    "side": entry_side,
                    "entry_z": float(merged.at[entry_idx, "z"]),
                    "exit_z": float(z),
                    "entry_price": float(entry_price),
                    "exit_price": float(cur_price),
                    "pnl_pct_gross": float(pct),
                    "pnl_pct_net": float(pnl_pct_net),
                    "hold_h": int(age_h),
                    "exit_reason": exit_reason,
                    "mfe_pct": float(mfe_pct),
                    "mae_pct": float(mae_pct),
                })
                in_trade = False
                entry_idx = None
                entry_side = None
                mfe_pct = 0.0; mae_pct = 0.0

    if not trades:
        return {"pair": f"{pair_a}/{pair_b}", "n_trades": 0, "notes": "no signals fired"}

    df = pd.DataFrame(trades)
    return {
        "pair": f"{pair_a}/{pair_b}",
        "n_trades": len(df),
        "n_wins": int((df["pnl_pct_net"] > 0).sum()),
        "n_losses": int((df["pnl_pct_net"] < 0).sum()),
        "win_rate_pct": float((df["pnl_pct_net"] > 0).mean() * 100),
        "avg_pct_gross": float(df["pnl_pct_gross"].mean()),
        "avg_pct_net": float(df["pnl_pct_net"].mean()),
        "median_pct_net": float(df["pnl_pct_net"].median()),
        "best_pct_net": float(df["pnl_pct_net"].max()),
        "worst_pct_net": float(df["pnl_pct_net"].min()),
        "total_pct_compound": float(((1 + df["pnl_pct_net"] / 100).prod() - 1) * 100),
        "median_hold_h": float(df["hold_h"].median()),
        # MFE/MAE excursion stats — for asymmetric TP/SL design
        "median_mfe_pct": float(df["mfe_pct"].median()),
        "median_mae_pct": float(df["mae_pct"].median()),
        "p75_mfe_pct": float(df["mfe_pct"].quantile(0.75)),
        "p75_mae_pct": float(df["mae_pct"].quantile(0.25)),  # 25% = lower tail
        "by_side": df.groupby("side")["pnl_pct_net"].agg(["count", "mean"]).to_dict(),
        "by_exit": df.groupby("exit_reason")["pnl_pct_net"].agg(["count", "mean"]).to_dict(),
        "first_signal_ts": str(df["entry_ts"].iloc[0]),
        "last_signal_ts": str(df["entry_ts"].iloc[-1]),
        "trades_sample_first_3": df.head(3).to_dict(orient="records"),
    }


def _verdict(result: dict) -> str:
    """Stable_positive / inconsistent / negative / insufficient — Step 17-style."""
    n = result.get("n_trades", 0)
    if n == 0:
        return "NO_SIGNALS"
    if n < 10:
        return "INSUFFICIENT_SAMPLE"
    wr = result.get("win_rate_pct", 0)
    avg = result.get("avg_pct_net", 0)
    total = result.get("total_pct_compound", 0)
    if wr >= 55 and avg > 0 and total > 5:
        return "STABLE_POSITIVE"
    if wr >= 50 and avg > 0:
        return "MARGINAL_POSITIVE"
    if wr < 40 and avg < 0:
        return "STABLE_NEGATIVE"
    return "INCONSISTENT"


def _render_md(results: list[dict], params: dict) -> str:
    lines = []
    lines.append(f"# Spot Pairs Backtest — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append("## Params")
    for k, v in params.items():
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("| Pair | N | Win% | Avg net% | Median net% | Total compound% | Best | Worst | Median hold (h) | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        if r.get("n_trades", 0) == 0:
            lines.append(f"| {r['pair']} | 0 | — | — | — | — | — | — | — | NO_SIGNALS |")
            continue
        v = _verdict(r)
        lines.append(
            f"| {r['pair']} | {r['n_trades']} | {r['win_rate_pct']:.1f} | "
            f"{r['avg_pct_net']:+.3f} | {r['median_pct_net']:+.3f} | "
            f"{r['total_pct_compound']:+.2f} | {r['best_pct_net']:+.2f} | "
            f"{r['worst_pct_net']:+.2f} | {r['median_hold_h']:.0f} | {v} |"
        )

    lines.append("")
    lines.append("## Per-Pair Detail")
    for r in results:
        if r.get("n_trades", 0) == 0:
            continue
        lines.append(f"\n### {r['pair']}")
        lines.append(f"- N trades: {r['n_trades']} ({r['n_wins']} wins, {r['n_losses']} losses, {r['win_rate_pct']:.1f}% wr)")
        lines.append(f"- Net avg/trade: **{r['avg_pct_net']:+.3f}%** (median {r['median_pct_net']:+.3f}%)")
        lines.append(f"- Compound total: **{r['total_pct_compound']:+.2f}%**")
        lines.append(f"- Best/worst: {r['best_pct_net']:+.2f}% / {r['worst_pct_net']:+.2f}%")
        lines.append(f"- Median hold: {r['median_hold_h']:.0f}h ({r['median_hold_h']/24:.1f} days)")
        lines.append(f"- First signal: {r['first_signal_ts']}, last: {r['last_signal_ts']}")
        by_side = r.get("by_side", {})
        if by_side and isinstance(by_side, dict):
            count = by_side.get("count", {})
            mean = by_side.get("mean", {})
            for side in count:
                lines.append(f"  - {side}: {count[side]} trades, avg {mean[side]:+.3f}%")
        by_exit = r.get("by_exit", {})
        if by_exit and isinstance(by_exit, dict):
            count = by_exit.get("count", {})
            mean = by_exit.get("mean", {})
            for reason in count:
                lines.append(f"  - exit {reason}: {count[reason]}, avg {mean[reason]:+.3f}%")
        lines.append(f"- **Verdict: {_verdict(r)}**")
    return "\n".join(lines) + "\n"


def _walk_forward_periods(merged: pd.DataFrame, n_periods: int) -> list[tuple]:
    """Split merged df into n equal time periods. Returns [(label, sub_df), ...]."""
    if n_periods <= 1:
        return [("full", merged)]
    chunk = len(merged) // n_periods
    out = []
    for i in range(n_periods):
        start = i * chunk
        end = (i + 1) * chunk if i < n_periods - 1 else len(merged)
        sub = merged.iloc[start:end]
        first_ts = sub["ts"].iloc[0] if len(sub) > 0 else "?"
        last_ts = sub["ts"].iloc[-1] if len(sub) > 0 else "?"
        label = f"P{i+1}({first_ts}→{last_ts})"
        out.append((label, sub))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_BARS)
    p.add_argument("--z", type=float, default=DEFAULT_Z_ENTRY, help="entry |z| threshold")
    p.add_argument("--z-exit", type=float, default=DEFAULT_Z_EXIT)
    p.add_argument("--hold", type=int, default=DEFAULT_MAX_HOLD_DAYS, help="max hold days")
    p.add_argument("--sl", type=float, default=DEFAULT_STOP_LOSS_PCT, help="stop loss pct")
    p.add_argument("--fee", type=float, default=DEFAULT_FEE_PCT_SIDE, help="fee pct per side")
    p.add_argument("--pair", default=None, help="single pair 'A/USDT,B/USDT'")
    p.add_argument("--out", default=None, help="output md path (default auto)")
    p.add_argument("--periods", type=int, default=1, help="walk-forward split (default 1 = full history)")
    args = p.parse_args()

    if args.pair:
        a, b = args.pair.split(",")
        pairs = [(a.strip(), b.strip())]
    else:
        pairs = DEFAULT_PAIRS

    max_hold_h = args.hold * 24

    storage = ROStorage(DB_PATH)
    results = []
    try:
        for pa, pb in pairs:
            merged = _fetch_pair_ohlcv(storage._conn, pa, pb)
            if args.periods > 1:
                # Walk-forward: split history into N periods, backtest each
                for label, sub in _walk_forward_periods(merged, args.periods):
                    r = _backtest_pair(
                        sub, pa, pb,
                        lookback=args.lookback, z_entry=args.z, z_exit=args.z_exit,
                        max_hold_h=max_hold_h, sl_pct=args.sl, fee_pct_side=args.fee,
                    )
                    r["period_label"] = label
                    r["pair"] = f"{pa}/{pb} [{label}]"
                    print(f"[backtest] {pa}/{pb} {label}: {r.get('n_trades', 0)} trades, verdict={_verdict(r)}")
                    results.append(r)
            else:
                r = _backtest_pair(
                    merged, pa, pb,
                    lookback=args.lookback, z_entry=args.z, z_exit=args.z_exit,
                    max_hold_h=max_hold_h, sl_pct=args.sl, fee_pct_side=args.fee,
                )
                print(f"[backtest] {pa} / {pb}: {r.get('n_trades', 0)} trades, verdict={_verdict(r)}")
                results.append(r)
    finally:
        storage.close()

    params = {
        "lookback_bars": args.lookback,
        "z_entry": args.z, "z_exit": args.z_exit,
        "max_hold_days": args.hold, "stop_loss_pct": args.sl,
        "fee_pct_per_side": args.fee,
        "n_pairs": len(pairs),
    }

    md = _render_md(results, params)
    out = Path(args.out) if args.out else (OUT_DIR / f"spot_pairs_{datetime.now(timezone.utc):%Y-%m-%d}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"\n[backtest] report written: {out} ({len(md)} chars)")
    print(md.split("## Per-Pair Detail")[0])  # print summary table to stdout
    return 0


if __name__ == "__main__":
    sys.exit(main())
