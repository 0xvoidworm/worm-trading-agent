# Cointegration Audit — 2026-05-24

_Generated: 2026-05-24T10:17:06.541617+00:00_

**Method:** Engle-Granger two-step on log-prices. ADF p-value on OLS residuals.
Cointegrated (p < 0.05) = ratio spread is stationary = mean-reverts properly.

## Per-Pair Results (sorted by p-value asc)
| Pair | N bars | ADF p | Coint? | β (hedge) | Half-life | Sugg hold | Current z | Existing verdict |
|---|---:|---:|:---:|---:|---:|---:|---:|---|
| CAKE/USDT/UNI/USDT | 26280 | 0.024 | ✓ | 0.614 | 37.4d | 21.0d | +0.14 | STABLE_NEGATIVE |
| POL/USDT/ARB/USDT | 22602 | 0.033 | ✓ | 0.884 | 27.2d | 21.0d | +0.56 | STABLE_NEGATIVE |
| FET/USDT/WLD/USDT | 23132 | 0.046 | ✓ | 0.772 | 61.9d | 21.0d | +0.12 | STABLE_NEGATIVE |
| BNB/USDT/BTC/USDT | 26357 | 0.092 | ✗ | 0.963 | 67.3d | 21.0d | +0.49 | RECENTLY_BROKEN |
| WIF/USDT/BONK/USDT | 18116 | 0.128 | ✗ | 1.469 | 37.8d | 21.0d | -0.07 | STABLE_NEGATIVE |
| SOL/USDT/ETH/USDT | 26357 | 0.166 | ✗ | 1.997 | 110.3d | 21.0d | +0.47 | INCONSISTENT |
| SUI/USDT/SOL/USDT | 26280 | 0.259 | ✗ | 0.714 | 112.0d | 21.0d | -0.39 | MARGINAL_POSITIVE |
| DOGE/USDT/BTC/USDT | 26280 | 0.261 | ✗ | 0.940 | 81.7d | 21.0d | -1.55 | MARGINAL_POSITIVE |
| ARB/USDT/OP/USDT | 26357 | 0.262 | ✗ | 0.822 | 92.2d | 21.0d | +0.21 | STABLE_NEGATIVE |
| STRK/USDT/ARB/USDT | 19770 | 0.296 | ✗ | 1.401 | 55.2d | 21.0d | +0.57 | STABLE_NEGATIVE |
| ETH/USDT/BTC/USDT | 26357 | 0.382 | ✗ | 0.431 | 107.5d | 21.0d | -1.32 | STABLE_NEGATIVE |
| NEAR/USDT/SOL/USDT | 26280 | 0.398 | ✗ | 0.554 | 136.7d | 21.0d | -0.01 | STABLE_NEGATIVE |
| NEAR/USDT/AVAX/USDT | 26280 | 0.515 | ✗ | 1.045 | 90.2d | 21.0d | +2.86 | CPCV_ROBUST |
| TON/USDT/ETH/USDT | 26280 | 0.575 | ✗ | 0.969 | 219.7d | 21.0d | -0.58 | MARGINAL_POSITIVE |
| SUI/USDT/ETH/USDT | 26280 | 0.599 | ✗ | 1.441 | 211.5d | 21.0d | -0.03 | INCONSISTENT |
| ENA/USDT/ETH/USDT | 18766 | 0.680 | ✗ | 1.817 | 134.2d | 21.0d | -1.49 | STABLE_NEGATIVE |
| AVAX/USDT/SOL/USDT | 26357 | 0.747 | ✗ | 0.436 | 240.7d | 21.0d | -1.78 | MARGINAL_POSITIVE |
| FET/USDT/ETH/USDT | 23132 | 0.794 | ✗ | 1.328 | 350.4d | 21.0d | -1.12 | STABLE_NEGATIVE |
| PYTH/USDT/LINK/USDT | 21976 | 0.810 | ✗ | 1.496 | 321.4d | 21.0d | -1.21 | STABLE_NEGATIVE |
| SHIB/USDT/DOGE/USDT | 26280 | 0.815 | ✗ | 0.619 | 295.1d | 21.0d | -1.71 | STABLE_NEGATIVE |
| SEI/USDT/SOL/USDT | 24306 | 0.829 | ✗ | 0.599 | 378.9d | 21.0d | -1.84 | STABLE_NEGATIVE |
| STX/USDT/ETH/USDT | 26280 | 0.897 | ✗ | 0.989 | 809.5d | 21.0d | -1.46 | STABLE_NEGATIVE |
| TRX/USDT/ETH/USDT | 26280 | 0.902 | ✗ | 0.799 | 1265.4d | 21.0d | +1.92 | MARGINAL_POSITIVE |
| DOT/USDT/ETH/USDT | 26280 | 0.942 | ✗ | 0.443 | 1380.9d | 21.0d | -2.15 | STABLE_NEGATIVE |
| TIA/USDT/ETH/USDT | 22454 | 0.963 | ✗ | 0.793 | ∞ | 14.0d | -1.41 | STABLE_NEGATIVE |
| APT/USDT/SOL/USDT | 26280 | 0.970 | ✗ | 0.067 | 5012.9d | 21.0d | -2.27 | MARGINAL_POSITIVE |

## Insights
- **3/26 pairs formally cointegrated** (ADF p < 0.05)
- Validated pairs that ARE cointegrated → strategy foundation solid
- Validated pairs that are NOT cointegrated → ratio drift risk, demote

## Suggested PAIR_HOLD_HOURS config
```python
# Per-pair max_hold_h = 4× half-life (OU mean-revert window)
# Clamped [3d, 21d] practical bounds. Default 14d for unknown.
PAIR_HOLD_HOURS = {
    ("SUI/USDT", "SOL/USDT"): 504,  # half_life 112.0d, coint p=0.259
    ("DOGE/USDT", "BTC/USDT"): 504,  # half_life 81.7d, coint p=0.261
    ("NEAR/USDT", "AVAX/USDT"): 504,  # half_life 90.2d, coint p=0.515
    ("TON/USDT", "ETH/USDT"): 504,  # half_life 219.7d, coint p=0.575
    ("AVAX/USDT", "SOL/USDT"): 504,  # half_life 240.7d, coint p=0.747
    ("TRX/USDT", "ETH/USDT"): 504,  # half_life 1265.4d, coint p=0.902
    ("APT/USDT", "SOL/USDT"): 504,  # half_life 5012.9d, coint p=0.970
}
```
