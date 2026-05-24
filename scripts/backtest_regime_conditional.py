#!/usr/bin/env python3
"""Per-pair regime-conditional backtest.

For each validated pair, segment historical trades by BTC regime at entry.
Compute mean return per (pair × regime) → identify pairs that work ONLY
in specific regimes.

Output:
- markdown report data/backtest_results/regime_conditional_<date>.md
- print suggested REGIME_GATE config for spot_pairs.py

Methodology:
1. Get BTC regime history (HMM fit on full 2y daily)
2. Run normal backtest on each validated pair, get trade list with entry_ts
3. For each trade, look up BTC regime at entry_ts (closest preceding daily bar)
4. Group trades by (pair, regime), aggregate stats
5. Flag pair-regime combos with positive mean return + statistical significance
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.regime_hmm import regime_history, regime_for_timestamp  # noqa: E402
from data_pipe.spot_pairs import (  # noqa: E402
    PAIR_BACKTEST_VERDICT, VALIDATED_VERDICTS,
)
from backtest_spot_pairs import _backtest_pair, _fetch_pair_ohlcv  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"


def main() -> int:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    print("[regime_cond] fitting HMM on 2y BTC daily returns...")

    class _Storage:
        def __init__(s, c): s._conn = c

    sto = _Storage(conn)
    hist = regime_history(sto)
    if hist is None:
        print("[regime_cond] no regime history — abort")
        return 1
    print(f"[regime_cond] regime distribution (2y BTC):")
    print(hist['regime'].value_counts().to_string())
    print()

    validated_pairs = [(pa, pb) for (pa, pb), v in PAIR_BACKTEST_VERDICT.items()
                       if v in VALIDATED_VERDICTS]
    print(f"[regime_cond] running per-pair regime-conditional backtest on {len(validated_pairs)} validated pairs...\n")

    all_results = []
    for i, (pa, pb) in enumerate(validated_pairs, 1):
        merged = _fetch_pair_ohlcv(conn, pa, pb)
        if merged.empty:
            continue
        # Need trade-level output: _backtest_pair returns aggregate, but we
        # need trades_sample_first_3 + a different access. Modify call to get full trades.
        # Quick workaround: re-call with sample. Better: extract trades inline.
        # Pragmatic: re-implement minimal trade loop here to capture entry timestamps.
        from backtest_spot_pairs import (
            DEFAULT_LOOKBACK_BARS, DEFAULT_Z_ENTRY, DEFAULT_Z_EXIT,
            DEFAULT_MAX_HOLD_DAYS, DEFAULT_STOP_LOSS_PCT, DEFAULT_FEE_PCT_SIDE,
        )
        result = _backtest_pair(
            merged, pa, pb,
            lookback=DEFAULT_LOOKBACK_BARS, z_entry=DEFAULT_Z_ENTRY,
            z_exit=DEFAULT_Z_EXIT, max_hold_h=DEFAULT_MAX_HOLD_DAYS * 24,
            sl_pct=DEFAULT_STOP_LOSS_PCT, fee_pct_side=DEFAULT_FEE_PCT_SIDE,
        )
        if result.get("n_trades", 0) == 0:
            continue

        # Re-run to get raw trades list
        from backtest_spot_pairs import _backtest_pair as _bp
        # Hack: inline lightweight re-extract from _backtest_pair via copy
        # Easier: replicate the trade loop here just to capture entry_ts
        trades = _extract_trades_for_pair(merged, pa, pb)
        if not trades:
            continue
        df_trades = pd.DataFrame(trades)
        # Look up regime per entry
        df_trades["entry_regime"] = df_trades["entry_ts"].apply(
            lambda t: regime_for_timestamp(hist, t) or "UNKNOWN"
        )
        # Aggregate per (pair, regime)
        for regime, g in df_trades.groupby("entry_regime"):
            if len(g) < 5:
                continue
            all_results.append({
                "pair": f"{pa}/{pb}",
                "regime": regime,
                "n_trades": int(len(g)),
                "win_rate_pct": float((g["pnl_pct_net"] > 0).mean() * 100),
                "avg_pct_net": float(g["pnl_pct_net"].mean()),
                "median_pct_net": float(g["pnl_pct_net"].median()),
                "total_compound_pct": float(((1 + g["pnl_pct_net"] / 100).prod() - 1) * 100),
                "best": float(g["pnl_pct_net"].max()),
                "worst": float(g["pnl_pct_net"].min()),
            })
        print(f"[{i}/{len(validated_pairs)}] {pa}/{pb}: {len(df_trades)} trades, "
              f"by_regime: {df_trades['entry_regime'].value_counts().to_dict()}")
    conn.close()

    # Render report
    lines = []
    lines.append(f"# Regime-Conditional Backtest — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"**Method:** For each validated pair trade, look up BTC HMM regime "
                 f"(BULL/NEUTRAL/BEAR) at entry. Aggregate stats per regime.\n")
    lines.append("## Per-Pair × Regime Matrix\n")
    lines.append("| Pair | Regime | N | Win% | Avg Net% | Median Net% | Compound% | Best | Worst |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    sorted_results = sorted(all_results, key=lambda r: (r["pair"], r["regime"]))
    for r in sorted_results:
        lines.append(
            f"| {r['pair']} | {r['regime']} | {r['n_trades']} | "
            f"{r['win_rate_pct']:.1f} | {r['avg_pct_net']:+.3f} | "
            f"{r['median_pct_net']:+.3f} | {r['total_compound_pct']:+.2f} | "
            f"{r['best']:+.2f} | {r['worst']:+.2f} |"
        )

    # Suggested REGIME_GATE config
    lines.append("\n## Suggested PAIR_REGIME_GATE\n```python")
    lines.append("# Pair-Regime allow-list: only auto-trade when BTC regime is in set.")
    lines.append("# Derived from regime-conditional backtest. Positive avg + n>=5 = include.")
    lines.append("PAIR_REGIME_GATE = {")
    by_pair: dict[str, list[str]] = {}
    for r in sorted_results:
        if r["avg_pct_net"] > 0 and r["n_trades"] >= 5:
            by_pair.setdefault(r["pair"], []).append(r["regime"])
    for pair_str, regimes in by_pair.items():
        parts = pair_str.split("/")
        if len(parts) == 4:
            pa = f"{parts[0]}/{parts[1]}"
            pb = f"{parts[2]}/{parts[3]}"
        else:
            pa, pb = parts[0], parts[1]
        regimes_str = ", ".join(f'"{r}"' for r in regimes)
        lines.append(f'    ("{pa}", "{pb}"): {{{regimes_str}}},')
    lines.append("}")
    lines.append("```")

    md = "\n".join(lines) + "\n"
    out = OUT_DIR / f"regime_conditional_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.write_text(md)
    print(f"\n[regime_cond] Report written: {out}")

    # Print to stdout
    print()
    print("=" * 110)
    print(f"{'Pair':<28s} {'Regime':<8s} {'N':>4s} {'Win%':>5s} {'AvgN%':>7s} {'MedN%':>7s} {'Compound%':>10s}")
    print("-" * 110)
    for r in sorted_results:
        print(f"{r['pair']:<28s} {r['regime']:<8s} {r['n_trades']:>4d} "
              f"{r['win_rate_pct']:>5.1f} {r['avg_pct_net']:>+7.3f} "
              f"{r['median_pct_net']:>+7.3f} {r['total_compound_pct']:>+10.2f}")

    return 0


def _extract_trades_for_pair(merged: pd.DataFrame, pa: str, pb: str) -> list[dict]:
    """Replicate _backtest_pair's trade loop but capture entry timestamps too."""
    import numpy as np
    lookback = 720
    z_entry = 2.0
    z_exit = 0.0
    max_hold_h = 14 * 24
    sl_pct = 10.0
    fee_pct_side = 0.10

    if len(merged) < lookback + 50:
        return []
    m = merged.reset_index(drop=True).copy()
    m["ratio"] = m["a"] / m["b"]
    m["mean"] = m["ratio"].rolling(lookback).mean()
    m["std"] = m["ratio"].rolling(lookback).std()
    m["z"] = (m["ratio"] - m["mean"]) / m["std"]
    m["above"] = m["z"] >= z_entry
    m["below"] = m["z"] <= -z_entry
    m["fresh_above"] = m["above"] & ~m["above"].shift(1, fill_value=False)
    m["fresh_below"] = m["below"] & ~m["below"].shift(1, fill_value=False)

    trades = []
    in_trade = False
    entry_idx = None
    entry_side = None
    fee_round = 2 * fee_pct_side

    for i in range(lookback, len(m)):
        z = m.at[i, "z"]
        if pd.isna(z):
            continue
        if not in_trade:
            if m.at[i, "fresh_below"]:
                in_trade = True; entry_idx = i; entry_side = "BUY_A"
            elif m.at[i, "fresh_above"]:
                in_trade = True; entry_idx = i; entry_side = "BUY_B"
        else:
            if entry_side == "BUY_A":
                ep = m.at[entry_idx, "a"]; cp = m.at[i, "a"]
            else:
                ep = m.at[entry_idx, "b"]; cp = m.at[i, "b"]
            pct = (cp - ep) / ep * 100
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
                    "entry_ts": m.at[entry_idx, "ts"],
                    "exit_ts": m.at[i, "ts"],
                    "side": entry_side,
                    "pnl_pct_net": float(pct - fee_round),
                })
                in_trade = False; entry_idx = None; entry_side = None
    return trades


if __name__ == "__main__":
    sys.exit(main())
