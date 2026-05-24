# Regime-Conditional Backtest — 2026-05-24

_Generated: 2026-05-24T10:07:52.942883+00:00_

**Method:** For each validated pair trade, look up BTC HMM regime (BULL/NEUTRAL/BEAR) at entry. Aggregate stats per regime.

## Per-Pair × Regime Matrix

| Pair | Regime | N | Win% | Avg Net% | Median Net% | Compound% | Best | Worst |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| APT/USDT/SOL/USDT | BULL | 10 | 50.0 | -1.715 | -3.873 | -20.16 | +11.40 | -14.68 |
| APT/USDT/SOL/USDT | NEUTRAL | 29 | 41.4 | +1.449 | -3.562 | +10.39 | +63.64 | -12.45 |
| APT/USDT/SOL/USDT | UNKNOWN | 20 | 70.0 | +5.889 | +6.109 | +169.30 | +28.67 | -15.80 |
| AVAX/USDT/SOL/USDT | BEAR | 5 | 60.0 | +4.751 | +4.863 | +20.11 | +25.97 | -12.18 |
| AVAX/USDT/SOL/USDT | BULL | 13 | 61.5 | +3.945 | +4.851 | +49.34 | +28.04 | -10.74 |
| AVAX/USDT/SOL/USDT | NEUTRAL | 26 | 38.5 | -1.554 | -3.382 | -41.81 | +20.94 | -12.92 |
| AVAX/USDT/SOL/USDT | UNKNOWN | 19 | 63.2 | +9.009 | +6.952 | +294.39 | +70.48 | -12.16 |
| DOGE/USDT/BTC/USDT | BEAR | 10 | 40.0 | -1.759 | -10.602 | -23.77 | +33.49 | -12.50 |
| DOGE/USDT/BTC/USDT | BULL | 9 | 55.6 | +0.609 | +2.440 | +3.51 | +12.13 | -10.38 |
| DOGE/USDT/BTC/USDT | NEUTRAL | 21 | 42.9 | -0.251 | -0.392 | -11.04 | +21.62 | -11.54 |
| DOGE/USDT/BTC/USDT | UNKNOWN | 12 | 58.3 | +2.023 | +0.223 | +20.98 | +23.34 | -10.79 |
| NEAR/USDT/AVAX/USDT | BEAR | 5 | 40.0 | -3.451 | -10.631 | -18.08 | +13.17 | -10.91 |
| NEAR/USDT/AVAX/USDT | BULL | 18 | 66.7 | +5.434 | +8.622 | +131.75 | +30.28 | -15.05 |
| NEAR/USDT/AVAX/USDT | NEUTRAL | 29 | 55.2 | +3.065 | +5.098 | +87.76 | +51.12 | -14.21 |
| NEAR/USDT/AVAX/USDT | UNKNOWN | 19 | 52.6 | +2.782 | +1.874 | +47.24 | +30.83 | -17.05 |
| SUI/USDT/SOL/USDT | BULL | 10 | 60.0 | +5.559 | +4.848 | +51.52 | +31.12 | -17.29 |
| SUI/USDT/SOL/USDT | NEUTRAL | 27 | 48.1 | -0.241 | -0.158 | -19.49 | +21.88 | -12.02 |
| SUI/USDT/SOL/USDT | UNKNOWN | 20 | 30.0 | +0.101 | -10.354 | -19.48 | +38.13 | -15.38 |
| TON/USDT/ETH/USDT | BULL | 17 | 47.1 | +0.562 | -0.165 | +5.67 | +14.62 | -10.47 |
| TON/USDT/ETH/USDT | NEUTRAL | 16 | 43.8 | -0.106 | -0.782 | -8.67 | +19.24 | -12.13 |
| TON/USDT/ETH/USDT | UNKNOWN | 21 | 47.6 | +0.712 | -0.253 | +3.17 | +35.85 | -15.71 |
| TRX/USDT/ETH/USDT | BEAR | 6 | 33.3 | -3.324 | -10.510 | -21.77 | +15.84 | -12.72 |
| TRX/USDT/ETH/USDT | BULL | 13 | 46.2 | +0.263 | -0.723 | -1.11 | +11.24 | -13.38 |
| TRX/USDT/ETH/USDT | NEUTRAL | 20 | 70.0 | +1.939 | +3.666 | +37.32 | +13.18 | -13.59 |
| TRX/USDT/ETH/USDT | UNKNOWN | 20 | 55.0 | +2.000 | +1.217 | +42.72 | +11.31 | -11.38 |

## Suggested PAIR_REGIME_GATE
```python
# Pair-Regime allow-list: only auto-trade when BTC regime is in set.
# Derived from regime-conditional backtest. Positive avg + n>=5 = include.
PAIR_REGIME_GATE = {
    ("APT/USDT", "SOL/USDT"): {"NEUTRAL", "UNKNOWN"},
    ("AVAX/USDT", "SOL/USDT"): {"BEAR", "BULL", "UNKNOWN"},
    ("DOGE/USDT", "BTC/USDT"): {"BULL", "UNKNOWN"},
    ("NEAR/USDT", "AVAX/USDT"): {"BULL", "NEUTRAL", "UNKNOWN"},
    ("SUI/USDT", "SOL/USDT"): {"BULL", "UNKNOWN"},
    ("TON/USDT", "ETH/USDT"): {"BULL", "UNKNOWN"},
    ("TRX/USDT", "ETH/USDT"): {"BULL", "NEUTRAL", "UNKNOWN"},
}
```
