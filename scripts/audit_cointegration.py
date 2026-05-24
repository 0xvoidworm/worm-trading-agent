#!/usr/bin/env python3
"""Formal cointegration audit for validated pairs.

Two-step Engle-Granger on log-prices:
  1. OLS regression log(A) = alpha + beta * log(B)
  2. ADF test on residuals (spread)

Output:
  - adf_pvalue (< 0.05 = cointegrated at 95%)
  - hedge_ratio (beta)
  - half_life_bars (OU mean reversion speed)
  - is_cointegrated bool

Non-cointegrated pairs → z-score strategy invalid (mean drifts). Demote.

Plus: half-life-based max_hold suggestion (= 4 × half_life capped 7d-21d).

Output: data/backtest_results/cointegration_audit_<date>.md
        + suggested PAIR_HOLD_HINT for spot_pairs.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dashboard.cointegration import engle_granger  # noqa: E402
from data_pipe.spot_pairs import PAIR_BACKTEST_VERDICT, VALIDATED_VERDICTS  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"


def fetch_log_prices(conn, pair_a, pair_b, exchange="bybit", tf="1h"):
    """Fetch aligned log-prices for cointegration test."""
    import pandas as pd
    df_a = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pair_a, tf],
    ).fetchdf()
    df_b = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts",
        [exchange, pair_b, tf],
    ).fetchdf()
    if df_a.empty or df_b.empty:
        return None, None
    merged = pd.merge(
        df_a.rename(columns={"close": "a"}),
        df_b.rename(columns={"close": "b"}),
        on="ts", how="inner",
    )
    if merged.empty:
        return None, None
    return np.log(merged["a"]), np.log(merged["b"])


def main() -> int:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    # Audit ALL pairs in verdict map (not just validated — also see if STABLE_NEG actually negative-cointegrated)
    pairs_to_audit = [(pa, pb) for (pa, pb), v in PAIR_BACKTEST_VERDICT.items()
                      if v != "NO_SIGNALS"]
    print(f"[coint_audit] testing {len(pairs_to_audit)} pairs (Engle-Granger on log-prices)\n")

    results = []
    for i, (pa, pb) in enumerate(pairs_to_audit, 1):
        log_a, log_b = fetch_log_prices(conn, pa, pb)
        if log_a is None:
            print(f"[{i}/{len(pairs_to_audit)}] {pa}/{pb}: NO DATA")
            continue
        cr = engle_granger(log_a, log_b, pair_names=(pa, pb))
        verdict_existing = PAIR_BACKTEST_VERDICT.get((pa, pb), "?")
        # Convert half-life bars (1h) to days
        hl_bars = cr.half_life_bars
        hl_days = hl_bars / 24 if hl_bars != float("inf") else float("inf")
        # Suggested max_hold = 4× half_life, clamped 3-21 days
        if hl_days == float("inf"):
            suggested_hold_days = 14
        else:
            suggested_hold_days = max(3, min(21, hl_days * 4))
        results.append({
            "pair": f"{pa}/{pb}",
            "n_bars": cr.n_bars,
            "hedge_ratio": cr.hedge_ratio,
            "adf_pvalue": cr.adf_pvalue,
            "is_cointegrated": cr.is_cointegrated,
            "half_life_bars": hl_bars,
            "half_life_days": hl_days,
            "current_zscore": cr.current_zscore,
            "suggested_hold_days": suggested_hold_days,
            "existing_verdict": verdict_existing,
        })
        coint_tag = "✓ COINT" if cr.is_cointegrated else "✗ no coint"
        hl_str = f"{hl_days:.1f}d" if hl_days != float("inf") else "∞"
        print(f"[{i}/{len(pairs_to_audit)}] {pa}/{pb}: ADF p={cr.adf_pvalue:.3f} "
              f"hl={hl_str}  beta={cr.hedge_ratio:.3f}  z={cr.current_zscore:+.2f}  {coint_tag}")
    conn.close()

    # Render report
    lines = []
    lines.append(f"# Cointegration Audit — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).isoformat()}_\n")
    lines.append(f"**Method:** Engle-Granger two-step on log-prices. ADF p-value on OLS residuals.")
    lines.append(f"Cointegrated (p < 0.05) = ratio spread is stationary = mean-reverts properly.\n")
    lines.append("## Per-Pair Results (sorted by p-value asc)")
    lines.append("| Pair | N bars | ADF p | Coint? | β (hedge) | Half-life | Sugg hold | Current z | Existing verdict |")
    lines.append("|---|---:|---:|:---:|---:|---:|---:|---:|---|")
    sorted_results = sorted(results, key=lambda r: r["adf_pvalue"])
    for r in sorted_results:
        hl_str = f"{r['half_life_days']:.1f}d" if r['half_life_days'] != float('inf') else "∞"
        coint_str = "✓" if r["is_cointegrated"] else "✗"
        lines.append(
            f"| {r['pair']} | {r['n_bars']} | {r['adf_pvalue']:.3f} | {coint_str} | "
            f"{r['hedge_ratio']:.3f} | {hl_str} | {r['suggested_hold_days']:.1f}d | "
            f"{r['current_zscore']:+.2f} | {r['existing_verdict']} |"
        )

    lines.append("\n## Insights")
    n_coint = sum(1 for r in results if r["is_cointegrated"])
    lines.append(f"- **{n_coint}/{len(results)} pairs formally cointegrated** (ADF p < 0.05)")
    lines.append(f"- Validated pairs that ARE cointegrated → strategy foundation solid")
    lines.append(f"- Validated pairs that are NOT cointegrated → ratio drift risk, demote")
    lines.append("")

    # Specific recommendations
    lines.append("## Suggested PAIR_HOLD_HOURS config")
    lines.append("```python")
    lines.append("# Per-pair max_hold_h = 4× half-life (OU mean-revert window)")
    lines.append("# Clamped [3d, 21d] practical bounds. Default 14d for unknown.")
    lines.append("PAIR_HOLD_HOURS = {")
    for r in sorted_results:
        if r["existing_verdict"] not in VALIDATED_VERDICTS:
            continue  # Only for validated pairs
        parts = r["pair"].split("/")
        if len(parts) == 4:
            pa_str = f'"{parts[0]}/{parts[1]}"'
            pb_str = f'"{parts[2]}/{parts[3]}"'
        else:
            pa_str = f'"{parts[0]}"'
            pb_str = f'"{parts[1]}"' if len(parts) > 1 else '""'
        hold_h = int(r["suggested_hold_days"] * 24)
        hl_note = (f"half_life {r['half_life_days']:.1f}d" if r['half_life_days'] != float('inf')
                   else "no mean revert")
        lines.append(f'    ({pa_str}, {pb_str}): {hold_h},  # {hl_note}, coint p={r["adf_pvalue"]:.3f}')
    lines.append("}")
    lines.append("```")

    md = "\n".join(lines) + "\n"
    out = OUT_DIR / f"cointegration_audit_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.write_text(md)
    print(f"\n[coint_audit] Report written: {out}")

    # Print summary table to stdout
    print()
    print("=" * 110)
    print(f"{'Pair':<28s} {'p-val':>6s} {'coint':>6s} {'beta':>7s} {'hl_days':>8s} {'sug_hold':>9s} {'z':>6s} verdict")
    print("-" * 110)
    for r in sorted_results:
        hl_s = f"{r['half_life_days']:.1f}" if r['half_life_days'] != float('inf') else "inf"
        coint = "YES" if r["is_cointegrated"] else "NO"
        print(f"{r['pair']:<28s} {r['adf_pvalue']:>6.3f} {coint:>6s} {r['hedge_ratio']:>7.3f} "
              f"{hl_s:>8s} {r['suggested_hold_days']:>8.1f}d {r['current_zscore']:>+6.2f} "
              f"{r['existing_verdict']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
