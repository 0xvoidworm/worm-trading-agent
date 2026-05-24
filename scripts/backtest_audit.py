#!/usr/bin/env python3
"""CPCV backtest audit — Lopez de Prado-style rigor.

Runs Combinatorial Purged Cross-Validation on validated spot pairs.
For each pair: N=10 chunks, K=2 test-chunks per fold → C(10,2)=45 folds.

Outputs per pair: mean Sharpe, std Sharpe, % positive folds, p-value,
deflated Sharpe (adjusted for multi-comparison across N pairs tested).

Plus global PBO (Probability of Backtest Overfitting).

Usage:
    python scripts/backtest_audit.py
    python scripts/backtest_audit.py --groups 8 --test-groups 2

Output: data/backtest_results/cpcv_audit_<date>.md
        Plus updated verdict suggestions printed for spot_pairs.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.cpcv import (  # noqa: E402
    assign_verdicts, compute_deflated_sharpe, compute_pbo, run_cpcv_on_pair,
)
from data_pipe.spot_pairs import PAIR_BACKTEST_VERDICT  # noqa: E402
from backtest_spot_pairs import _backtest_pair, _fetch_pair_ohlcv  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"


def _backtest_fn_factory(pair_a, pair_b, lookback=720, z_entry=2.0,
                          z_exit=0.0, max_hold_h=14*24, sl_pct=10.0,
                          fee_pct_side=0.10):
    """Build a backtest function bound to specific pair + params."""
    def fn(test_df):
        return _backtest_pair(
            test_df, pair_a, pair_b,
            lookback=lookback, z_entry=z_entry, z_exit=z_exit,
            max_hold_h=max_hold_h, sl_pct=sl_pct, fee_pct_side=fee_pct_side,
        )
    return fn


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--groups", type=int, default=10)
    p.add_argument("--test-groups", type=int, default=2)
    p.add_argument("--embargo", type=int, default=50)
    p.add_argument("--validated-only", action="store_true",
                   help="Audit only currently validated pairs (default: all in verdict map)")
    args = p.parse_args()

    if args.validated_only:
        from data_pipe.spot_pairs import VALIDATED_VERDICTS
        pairs = [(pa, pb) for (pa, pb), v in PAIR_BACKTEST_VERDICT.items()
                 if v in VALIDATED_VERDICTS]
    else:
        # Audit all pairs we have verdicts for (excluding NO_SIGNALS)
        pairs = [(pa, pb) for (pa, pb), v in PAIR_BACKTEST_VERDICT.items()
                 if v != "NO_SIGNALS"]

    print(f"[audit] CPCV on {len(pairs)} pairs, "
          f"groups={args.groups}, test_groups={args.test_groups}, "
          f"embargo={args.embargo}")
    print(f"[audit] Total folds per pair: C({args.groups},{args.test_groups})")
    print()

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    results = {}
    try:
        for i, (pa, pb) in enumerate(pairs, 1):
            merged = _fetch_pair_ohlcv(conn, pa, pb)
            if merged.empty:
                print(f"[{i}/{len(pairs)}] {pa}/{pb}: NO DATA")
                continue
            label = f"{pa}/{pb}"
            print(f"[{i}/{len(pairs)}] {label}: running CPCV ({len(merged)} bars)...", end=" ", flush=True)
            bt_fn = _backtest_fn_factory(pa, pb)
            cpcv = run_cpcv_on_pair(
                merged=merged, backtest_fn=bt_fn,
                strategy_label=label,
                n_groups=args.groups, test_groups=args.test_groups,
                embargo=args.embargo,
            )
            print(f"folds={cpcv.n_folds} mean_sh={cpcv.mean_sharpe:.2f} "
                  f"mean_ret={cpcv.mean_return:+.3f}%")
            results[label] = cpcv
    finally:
        conn.close()

    # PBO across all pairs
    print()
    pbo = compute_pbo(results)
    print(f"[audit] PBO (Probability of Backtest Overfitting): {pbo:.1%}")

    # Deflated Sharpe per pair (adjusted for n_trials)
    n_trials = len(results)
    for r in results.values():
        r.deflated_sharpe = compute_deflated_sharpe(
            observed_sharpe=r.mean_sharpe,
            n_trials=n_trials,
            n_observations=r.n_total_trades,
        )

    # Assign verdicts
    assign_verdicts(results, pbo)

    # Build markdown report
    lines = []
    lines.append(f"# CPCV Backtest Audit — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"**Method:** Combinatorial Purged Cross-Validation per Lopez de Prado")
    lines.append(f"- Groups: {args.groups}, Test groups: {args.test_groups}")
    lines.append(f"- Folds per pair: C({args.groups},{args.test_groups}) = "
                 f"{len(list(__import__('itertools').combinations(range(args.groups), args.test_groups)))}")
    lines.append(f"- Embargo: {args.embargo} bars\n")

    lines.append(f"## Global Metric")
    lines.append(f"- **PBO (Probability of Backtest Overfitting): {pbo:.1%}**")
    pbo_verdict = ("ROBUST signal" if pbo < 0.3 else
                   "MARGINAL" if pbo < 0.5 else
                   "LIKELY OVERFIT — strategies are mostly noise")
    lines.append(f"  - Interpretation: **{pbo_verdict}**\n")

    lines.append("## Per-pair Results (sorted by deflated Sharpe desc)")
    lines.append("| Pair | Folds | Trades | Mean Ret% | Std Ret% | % Pos Folds | Mean Sharpe | p-value | Deflated SR | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    sorted_results = sorted(results.values(), key=lambda r: -r.deflated_sharpe)
    for r in sorted_results:
        d = r.to_dict()
        lines.append(
            f"| {d['strategy']} | {d['n_folds']} | {d['n_total_trades']} | "
            f"{d['mean_return_pct']:+.3f} | {d['std_return_pct']:.3f} | "
            f"{d['pct_positive_folds']:.1f} | {d['mean_sharpe']:+.2f} | "
            f"{d['sharpe_p_value']:.3f} | {d['deflated_sharpe']:.3f} | {d['verdict']} |"
        )

    lines.append("\n## Suggested PAIR_BACKTEST_VERDICT Update")
    lines.append("```python")
    lines.append("# Based on CPCV audit — overwrites existing verdicts")
    lines.append("PAIR_BACKTEST_VERDICT_CPCV = {")
    for r in sorted_results:
        # Map CPCV verdict to existing schema
        if r.verdict == "ROBUST":
            new_v = "STABLE_POSITIVE"
        elif r.verdict == "MARGINAL":
            new_v = "MARGINAL_POSITIVE"
        elif r.verdict == "OVERFIT":
            new_v = "OVERFIT_REJECTED"
        elif r.verdict == "NEGATIVE":
            new_v = "STABLE_NEGATIVE"
        elif r.verdict == "WEAK":
            new_v = "INCONSISTENT"
        else:
            new_v = "NO_SIGNALS"
        # Label format: "A/USDT/B/USDT"
        parts = r.strategy_label.split("/")
        if len(parts) == 4:
            pair_a = f"{parts[0]}/{parts[1]}"
            pair_b = f"{parts[2]}/{parts[3]}"
        elif len(parts) == 2:
            pair_a, pair_b = parts[0], parts[1]
        else:
            pair_a, pair_b = r.strategy_label, "?"
        lines.append(f'    ("{pair_a}", "{pair_b}"): "{new_v}",  # CPCV mean={r.mean_return:+.3f}% DSR={r.deflated_sharpe:.2f}')
    lines.append("}")
    lines.append("```\n")

    lines.append("## CPCV Methodology Notes")
    lines.append(
        "- **Mean Sharpe**: averaged across all folds. Lopez de Prado favors >1.0 as edge.\n"
        "- **% Positive Folds**: stability. >60% = consistent signal across regimes.\n"
        "- **p-value**: t-test on Sharpe > 0. <0.10 = signal significantly positive.\n"
        "- **Deflated Sharpe Ratio**: Bailey-LdP adjustment for multi-comparison. "
        "Probability observed Sharpe is real after testing N strategies. >0.50 = likely real.\n"
        "- **PBO**: aggregate. Probability that best-in-sample strategy underperforms "
        "out-of-sample. <0.30 = strategies have signal. >0.50 = mostly noise.\n"
    )

    md = "\n".join(lines)
    out = OUT_DIR / f"cpcv_audit_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"\n[audit] Report written: {out}")

    # Print summary table to stdout
    print()
    print("=" * 100)
    print(f"{'Pair':<28} {'Folds':>5} {'Trades':>6} {'MeanRet%':>9} "
          f"{'%Pos':>5} {'Sharpe':>7} {'p-val':>6} {'DSR':>5} {'Verdict':<14}")
    print("-" * 100)
    for r in sorted_results:
        d = r.to_dict()
        print(f"{d['strategy']:<28} {d['n_folds']:>5} {d['n_total_trades']:>6} "
              f"{d['mean_return_pct']:>+9.3f} {d['pct_positive_folds']:>5.1f} "
              f"{d['mean_sharpe']:>+7.2f} {d['sharpe_p_value']:>6.3f} "
              f"{d['deflated_sharpe']:>5.2f} {d['verdict']:<14}")
    print(f"\nGlobal PBO: {pbo:.1%} ({pbo_verdict})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
