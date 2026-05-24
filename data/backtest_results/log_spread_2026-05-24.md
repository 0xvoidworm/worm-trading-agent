# Log-Spread Backtest — 2026-05-24

_Generated: 2026-05-24T10:24:16.101175+00:00_

**Method:** spread = log(A) - β·log(B), β from OLS on first 50% of data (train), backtest on remaining (out-of-sample). Z-score on rolling spread.

Params: z_entry=±2.0, hold=14d, SL 10%, fee 0.1%/side.

## Summary Matrix

| Pair | β | N | Win% | Avg Net% | Median Net% | Compound% | Best | Worst | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| NEAR/USDT/AVAX/USDT | 1.122 | 37 | 51.4 | -0.796 | +0.295 | -35.30 | +13.38 | -13.73 | INCONSISTENT |
| AVAX/USDT/SOL/USDT | 0.524 | 38 | 47.4 | -0.426 | -1.566 | -31.59 | +27.06 | -15.86 | INCONSISTENT |
| DOGE/USDT/BTC/USDT | 0.965 | 28 | 57.1 | +0.880 | +2.418 | +12.94 | +15.92 | -16.73 | STABLE_POSITIVE |
| TRX/USDT/ETH/USDT | 0.667 | 30 | 60.0 | +0.823 | +1.449 | +14.78 | +15.58 | -13.64 | STABLE_POSITIVE |
| APT/USDT/SOL/USDT | 0.173 | 29 | 41.4 | -2.864 | -10.305 | -62.42 | +16.54 | -12.19 | INCONSISTENT |
| TON/USDT/ETH/USDT | 1.759 | 34 | 52.9 | -0.222 | +0.840 | -20.62 | +21.94 | -13.66 | INCONSISTENT |
| SUI/USDT/SOL/USDT | 0.415 | 34 | 32.4 | -1.545 | -6.152 | -53.86 | +23.90 | -19.87 | STABLE_NEGATIVE |
| SUI/USDT/ETH/USDT | 1.253 | 29 | 37.9 | -2.910 | -3.234 | -62.30 | +17.93 | -18.71 | STABLE_NEGATIVE |
| CAKE/USDT/UNI/USDT | 0.916 | 21 | 33.3 | -0.717 | -6.503 | -26.97 | +25.66 | -11.35 | STABLE_NEGATIVE |
| POL/USDT/ARB/USDT | 0.744 | 21 | 57.1 | +1.351 | +2.080 | +18.07 | +16.63 | -13.09 | STABLE_POSITIVE |
| FET/USDT/WLD/USDT | 0.718 | 26 | 42.3 | +0.023 | -8.572 | -21.97 | +49.94 | -14.54 | INCONSISTENT |
| BNB/USDT/BTC/USDT | 1.061 | 27 | 40.7 | -0.680 | -2.456 | -22.46 | +17.51 | -11.25 | INCONSISTENT |
| ETH/USDT/BTC/USDT | 0.663 | 27 | 33.3 | -2.014 | -3.660 | -47.47 | +12.29 | -14.59 | STABLE_NEGATIVE |
| ARB/USDT/OP/USDT | 0.780 | 29 | 34.5 | -4.605 | -10.338 | -78.42 | +18.90 | -23.43 | STABLE_NEGATIVE |
| SOL/USDT/ETH/USDT | 2.885 | 26 | 38.5 | -1.144 | -4.781 | -35.97 | +19.39 | -15.26 | STABLE_NEGATIVE |
