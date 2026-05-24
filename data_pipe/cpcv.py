"""Combinatorial Purged Cross-Validation (CPCV) per Lopez de Prado.

CPCV addresses backtest overfitting by generating MANY train/test splits
instead of single walk-forward. For each pair, we get N choose K test
configurations and aggregate metrics.

Key outputs:
1. Per-pair: mean_sharpe, std_sharpe, % positive folds, sample size
2. Multi-pair: PBO (Probability of Backtest Overfitting)
3. Deflated Sharpe Ratio (DSR) — adjusts Sharpe for # of strategies tested

PBO interpretation:
  < 30% : signal robust
  30-50%: marginal
  > 50% : likely overfit

DSR interpretation:
  > 0.95: high confidence
  0.5-0.95: moderate
  < 0.5: weak / random

Reference: Lopez de Prado, "Advances in Financial Machine Learning" (2018)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class FoldResult:
    """Single fold backtest result."""
    fold_id: str           # e.g. "P3+P5" (test on these chunks)
    n_trades: int
    win_rate_pct: float
    avg_pct_net: float
    sharpe: float          # annualized Sharpe of trade returns
    total_compound_pct: float


@dataclass
class CPCVResult:
    """Aggregated CPCV stats for one strategy/pair."""
    strategy_label: str
    n_folds: int
    n_total_trades: int
    fold_returns: list[float]      # mean return per fold (for aggregation)
    fold_sharpes: list[float]
    mean_return: float
    std_return: float
    median_return: float
    pct_positive_folds: float
    mean_sharpe: float
    std_sharpe: float
    sharpe_t_stat: float           # t-test: is mean_sharpe > 0?
    sharpe_p_value: float
    deflated_sharpe: float = 0.0   # adjusted for multiple testing (set later)
    verdict: str = "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_label,
            "n_folds": self.n_folds,
            "n_total_trades": self.n_total_trades,
            "mean_return_pct": round(self.mean_return, 3),
            "std_return_pct": round(self.std_return, 3),
            "median_return_pct": round(self.median_return, 3),
            "pct_positive_folds": round(self.pct_positive_folds, 1),
            "mean_sharpe": round(self.mean_sharpe, 3),
            "std_sharpe": round(self.std_sharpe, 3),
            "sharpe_t_stat": round(self.sharpe_t_stat, 3),
            "sharpe_p_value": round(self.sharpe_p_value, 4),
            "deflated_sharpe": round(self.deflated_sharpe, 3),
            "verdict": self.verdict,
        }


# ─── core CPCV splitter ────────────────────────────────────────────────────

def _build_test_splits(
    n_bars: int,
    n_groups: int = 10,
    test_groups: int = 2,
) -> list[tuple[str, list[int]]]:
    """Generate all (n_groups choose test_groups) test bar-index sets.

    Each split: contiguous chunks combined → test indices.
    """
    group_size = n_bars // n_groups
    splits = []
    for combo in combinations(range(n_groups), test_groups):
        # Build mask + label
        test_indices = []
        for g in combo:
            start = g * group_size
            end = (g + 1) * group_size if g < n_groups - 1 else n_bars
            test_indices.extend(range(start, end))
        label = "+".join(f"G{g}" for g in combo)
        splits.append((label, test_indices))
    return splits


def _purge_embargo(test_indices: list[int], n_bars: int, embargo: int = 50) -> set[int]:
    """Return purged bar set: test indices + embargo around them.

    These bars are EXCLUDED from training. We don't need train data here since
    backtest is self-contained, but we use embargo to ensure test data itself
    doesn't include leaking from adjacent chunks.

    For our purpose: shrink test set by `embargo` bars at each chunk boundary
    to avoid leak from rolling z-score lookback.
    """
    test_set = set(test_indices)
    # Identify chunk boundaries
    sorted_test = sorted(test_set)
    if not sorted_test:
        return set()
    purged = set()
    prev = -2
    for idx in sorted_test:
        # If gap > 1 (chunk boundary), purge first `embargo` bars of new chunk
        if idx > prev + 1:
            for i in range(idx, min(idx + embargo, n_bars)):
                purged.add(i)
        prev = idx
    return purged


# ─── CPCV per pair ─────────────────────────────────────────────────────────

def run_cpcv_on_pair(
    merged: pd.DataFrame,
    backtest_fn: Callable[[pd.DataFrame], dict],
    strategy_label: str,
    n_groups: int = 10,
    test_groups: int = 2,
    embargo: int = 50,
    min_test_bars: int = 200,
    bars_per_year: int = 8760,  # 1h bars × 24 × 365
) -> CPCVResult:
    """Run CPCV on one pair. backtest_fn takes test-data df and returns
    dict with at least keys: n_trades, win_rate_pct, avg_pct_net, total_pct_compound.

    Sharpe estimated from trade-level pnl_pct_net mean/std × sqrt(trades/year).
    """
    n_bars = len(merged)
    if n_bars < min_test_bars * 2:
        return CPCVResult(
            strategy_label=strategy_label, n_folds=0, n_total_trades=0,
            fold_returns=[], fold_sharpes=[], mean_return=0, std_return=0,
            median_return=0, pct_positive_folds=0, mean_sharpe=0,
            std_sharpe=0, sharpe_t_stat=0, sharpe_p_value=1.0,
            verdict="INSUFFICIENT_BARS",
        )

    splits = _build_test_splits(n_bars, n_groups, test_groups)
    fold_returns: list[float] = []
    fold_sharpes: list[float] = []
    fold_n_trades: list[int] = []
    fold_results: list[FoldResult] = []

    for label, test_indices in splits:
        purged = _purge_embargo(test_indices, n_bars, embargo)
        # Effective test bars = test_indices minus purged
        effective = [i for i in test_indices if i not in purged]
        if len(effective) < min_test_bars:
            continue
        test_df = merged.iloc[effective].copy().reset_index(drop=True)

        # Reset ts index to ensure rolling z-score on continuous data is OK
        # (warning: chunked test data has gaps; backtest_fn must use index-only)
        try:
            res = backtest_fn(test_df)
        except Exception:
            continue
        if res.get("n_trades", 0) == 0:
            continue

        n_trades = int(res["n_trades"])
        avg_ret = float(res.get("avg_pct_net", 0))
        # Estimate Sharpe from std + mean of returns
        # Rough: Sharpe ≈ mean / std × sqrt(trades_per_year)
        # Without per-trade list: use proxy
        compound = float(res.get("total_pct_compound", 0))
        # Sharpe proxy from win_rate + avg
        wr = float(res.get("win_rate_pct", 50)) / 100
        # Sharpe rough: (mean / approx_std) × sqrt(trades/year ann factor)
        if n_trades > 1:
            # Need std proxy; use (best-worst)/4 as approx
            best = float(res.get("best_pct_net", avg_ret + 1))
            worst = float(res.get("worst_pct_net", avg_ret - 1))
            std_proxy = max(0.5, (best - worst) / 4)
            # Annualization: # trades / years (rough estimate from test bars)
            years_in_test = len(effective) / bars_per_year
            trades_per_year = n_trades / max(years_in_test, 0.1)
            sharpe = (avg_ret / std_proxy) * math.sqrt(max(trades_per_year, 1))
        else:
            sharpe = 0.0

        fold_returns.append(avg_ret)
        fold_sharpes.append(sharpe)
        fold_n_trades.append(n_trades)
        fold_results.append(FoldResult(
            fold_id=label, n_trades=n_trades, win_rate_pct=wr * 100,
            avg_pct_net=avg_ret, sharpe=sharpe, total_compound_pct=compound,
        ))

    if not fold_returns:
        return CPCVResult(
            strategy_label=strategy_label, n_folds=0, n_total_trades=0,
            fold_returns=[], fold_sharpes=[], mean_return=0, std_return=0,
            median_return=0, pct_positive_folds=0, mean_sharpe=0,
            std_sharpe=0, sharpe_t_stat=0, sharpe_p_value=1.0,
            verdict="NO_TRADES",
        )

    arr_ret = np.array(fold_returns)
    arr_sharpe = np.array(fold_sharpes)
    mean_ret = float(arr_ret.mean())
    std_ret = float(arr_ret.std())
    median_ret = float(np.median(arr_ret))
    pct_pos = float((arr_ret > 0).mean() * 100)
    mean_sh = float(arr_sharpe.mean())
    std_sh = float(arr_sharpe.std())
    # t-test: mean Sharpe > 0?
    if len(arr_sharpe) > 1 and std_sh > 0:
        t_stat = mean_sh / (std_sh / math.sqrt(len(arr_sharpe)))
        p_val = float(1 - stats.t.cdf(t_stat, df=len(arr_sharpe) - 1))
    else:
        t_stat = 0.0
        p_val = 1.0

    return CPCVResult(
        strategy_label=strategy_label,
        n_folds=len(fold_returns),
        n_total_trades=int(sum(fold_n_trades)),
        fold_returns=fold_returns,
        fold_sharpes=fold_sharpes,
        mean_return=mean_ret,
        std_return=std_ret,
        median_return=median_ret,
        pct_positive_folds=pct_pos,
        mean_sharpe=mean_sh,
        std_sharpe=std_sh,
        sharpe_t_stat=t_stat,
        sharpe_p_value=p_val,
    )


# ─── PBO across multiple strategies ────────────────────────────────────────

def compute_pbo(strategy_results: dict[str, CPCVResult]) -> float:
    """Probability of Backtest Overfitting via Bailey-Lopez de Prado method.

    For each fold, rank strategies by performance. Compute median rank of
    "best in-sample" strategy across folds. PBO = % chance best IS strategy
    underperforms OOS median.

    Simplified version: use fold returns matrix.
    """
    pairs = list(strategy_results.keys())
    if len(pairs) < 2:
        return 0.0

    # Build matrix [strategy, fold] of returns
    # All strategies must have same fold structure for valid comparison
    matrix = []
    valid_pairs = []
    n_folds = None
    for k, r in strategy_results.items():
        if r.n_folds < 5:
            continue
        if n_folds is None:
            n_folds = r.n_folds
        if r.n_folds == n_folds:
            matrix.append(r.fold_returns)
            valid_pairs.append(k)
    if len(valid_pairs) < 2:
        return 0.0
    M = np.array(matrix)  # [n_strategies, n_folds]

    # Random splits of folds into IS / OOS halves — DO NOT materialize all combos
    # (C(45, 22) ≈ 3.5e12 → OOM). Sample directly via random subsets.
    rng = np.random.default_rng(42)
    n_splits = 200  # reasonable sample
    fold_combos = []
    half = n_folds // 2
    fold_indices = np.arange(n_folds)
    seen = set()
    while len(fold_combos) < n_splits and len(seen) < 5000:
        sample = tuple(sorted(rng.choice(fold_indices, size=half, replace=False).tolist()))
        if sample not in seen:
            seen.add(sample)
            fold_combos.append(sample)

    overfit_count = 0
    for is_folds in fold_combos:
        oos_folds = [f for f in range(n_folds) if f not in is_folds]
        if not oos_folds:
            continue
        is_means = M[:, list(is_folds)].mean(axis=1)
        oos_means = M[:, oos_folds].mean(axis=1)
        # Best in-sample strategy
        best_is_idx = int(np.argmax(is_means))
        # OOS rank of that strategy (0 = best, len-1 = worst)
        oos_rank = int(np.sum(oos_means > oos_means[best_is_idx]))
        # If oos_rank > median, strategy underperformed OOS
        median_rank = (len(valid_pairs) - 1) / 2
        if oos_rank > median_rank:
            overfit_count += 1

    return overfit_count / len(fold_combos)


# ─── Deflated Sharpe Ratio ─────────────────────────────────────────────────

def compute_deflated_sharpe(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Bailey-Lopez de Prado Deflated Sharpe Ratio.

    Probability that observed Sharpe is statistically significant after
    adjusting for multiple-testing across n_trials strategies.

    Args:
        observed_sharpe: pair's annualized Sharpe
        n_trials: # of strategies (pairs) tested
        n_observations: # of trades in the strategy
        skewness, kurtosis: of returns (approx)

    Returns: probability in [0, 1] that this Sharpe is real (not noise).
    """
    if n_observations < 2:
        return 0.0
    # Expected max Sharpe under null (random strategies)
    # E[max SR] approx = sqrt(2 ln(N)) - (gamma + ln(2)) / sqrt(2 ln(N))
    # where gamma is Euler-Mascheroni ≈ 0.5772
    if n_trials < 2:
        n_trials = 2
    gamma = 0.5772156649
    z_inv_n = math.sqrt(2 * math.log(n_trials))
    expected_max_sr = z_inv_n - (gamma + math.log(2)) / z_inv_n
    # Variance of estimated Sharpe (adjusted for skew/kurt)
    sr_var_factor = 1 - skewness * observed_sharpe + (kurtosis - 1) / 4 * observed_sharpe ** 2
    sr_var_factor = max(0.01, sr_var_factor)
    sr_var = sr_var_factor / (n_observations - 1)
    sr_std = math.sqrt(sr_var)
    # Deflated: probability observed > expected_max under null
    z = (observed_sharpe - expected_max_sr) / sr_std
    return float(stats.norm.cdf(z))


def assign_verdicts(
    results: dict[str, CPCVResult],
    pbo: float,
) -> None:
    """Mutate results: assign verdict per Lopez de Prado-style criteria.

    ROBUST: mean_sharpe > 0.5 AND pct_positive_folds > 60 AND
            sharpe_p_value < 0.10 AND deflated_sharpe > 0.5
    MARGINAL: mean > 0 AND pct_positive > 50
    OVERFIT: mean > 0 BUT high PBO + low DSR + low pct_positive
    NEGATIVE: mean < 0
    INSUFFICIENT: < 5 folds
    """
    for r in results.values():
        if r.n_folds < 5:
            r.verdict = "INSUFFICIENT_FOLDS"
            continue
        if r.mean_return < 0:
            r.verdict = "NEGATIVE"
            continue
        if (r.mean_sharpe > 0.5
            and r.pct_positive_folds > 60
            and r.sharpe_p_value < 0.10
            and r.deflated_sharpe > 0.5):
            r.verdict = "ROBUST"
        elif r.mean_return > 0 and r.pct_positive_folds > 50:
            r.verdict = "MARGINAL"
        elif pbo > 0.5 and r.deflated_sharpe < 0.3:
            r.verdict = "OVERFIT"
        else:
            r.verdict = "WEAK"
