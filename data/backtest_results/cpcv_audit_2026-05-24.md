# CPCV Backtest Audit — 2026-05-24

_Generated: 2026-05-24T09:54:43.008886+00:00_

**Method:** Combinatorial Purged Cross-Validation per Lopez de Prado
- Groups: 10, Test groups: 2
- Folds per pair: C(10,2) = 45
- Embargo: 50 bars

## Global Metric
- **PBO (Probability of Backtest Overfitting): 10.5%**
  - Interpretation: **ROBUST signal**

## Per-pair Results (sorted by deflated Sharpe desc)
| Pair | Folds | Trades | Mean Ret% | Std Ret% | % Pos Folds | Mean Sharpe | p-value | Deflated SR | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| NEAR/USDT/AVAX/USDT | 45 | 496 | +6.689 | 7.770 | 91.1 | +1.47 | 0.000 | 0.801 | ROBUST |
| AVAX/USDT/SOL/USDT | 45 | 484 | +3.517 | 5.456 | 62.2 | +0.90 | 0.000 | 0.000 | MARGINAL |
| DOGE/USDT/BTC/USDT | 45 | 392 | +2.728 | 4.801 | 68.9 | +0.79 | 0.010 | 0.000 | MARGINAL |
| TRX/USDT/ETH/USDT | 45 | 458 | +2.491 | 5.506 | 57.8 | +0.72 | 0.007 | 0.000 | MARGINAL |
| APT/USDT/SOL/USDT | 45 | 412 | +2.244 | 5.500 | 57.8 | +0.51 | 0.083 | 0.000 | MARGINAL |
| TON/USDT/ETH/USDT | 45 | 405 | +0.871 | 4.647 | 62.2 | +0.40 | 0.029 | 0.000 | MARGINAL |
| SUI/USDT/SOL/USDT | 45 | 441 | +3.778 | 12.141 | 55.6 | +0.16 | 0.262 | 0.000 | MARGINAL |
| SUI/USDT/ETH/USDT | 45 | 425 | +0.962 | 5.669 | 48.9 | -0.07 | 0.596 | 0.000 | WEAK |

## Suggested PAIR_BACKTEST_VERDICT Update
```python
# Based on CPCV audit — overwrites existing verdicts
PAIR_BACKTEST_VERDICT_CPCV = {
    ("NEAR/USDT", "AVAX/USDT"): "STABLE_POSITIVE",  # CPCV mean=+6.689% DSR=0.80
    ("AVAX/USDT", "SOL/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+3.517% DSR=0.00
    ("DOGE/USDT", "BTC/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+2.728% DSR=0.00
    ("TRX/USDT", "ETH/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+2.491% DSR=0.00
    ("APT/USDT", "SOL/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+2.244% DSR=0.00
    ("TON/USDT", "ETH/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+0.871% DSR=0.00
    ("SUI/USDT", "SOL/USDT"): "MARGINAL_POSITIVE",  # CPCV mean=+3.778% DSR=0.00
    ("SUI/USDT", "ETH/USDT"): "INCONSISTENT",  # CPCV mean=+0.962% DSR=0.00
}
```

## CPCV Methodology Notes
- **Mean Sharpe**: averaged across all folds. Lopez de Prado favors >1.0 as edge.
- **% Positive Folds**: stability. >60% = consistent signal across regimes.
- **p-value**: t-test on Sharpe > 0. <0.10 = signal significantly positive.
- **Deflated Sharpe Ratio**: Bailey-LdP adjustment for multi-comparison. Probability observed Sharpe is real after testing N strategies. >0.50 = likely real.
- **PBO**: aggregate. Probability that best-in-sample strategy underperforms out-of-sample. <0.30 = strategies have signal. >0.50 = mostly noise.
