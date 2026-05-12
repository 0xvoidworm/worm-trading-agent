# Step 18.B — Trend-follow standalone backtest

**Window:** 2023-04-27 → 2026-04-26 (1095 days)  
**Quarters:** 12  
**BB:** SMA(20) ± 2.0×stdev  
**ATR:** 14 bars  
**Stop ATR mult:** 2.0× (initial), 3.0× (trailing)  
**Max hold:** 168h  
**Slippage:** 10.0 bps/side  
**Risk/trade:** 5.0% equity notional

## Per-symbol summary

| Symbol | Verdict | n_trades | Realized% | Sign | Win% | Exits |
|---|---|---|---|---|---|---|
| BTC/USDT | **STABLE_NEGATIVE** | 861 | -15.32% | 1/12 | 26.1% | sma_cross=531 trailing_stop=171 stop_loss=159 |
| ETH/USDT | **STABLE_NEGATIVE** | 941 | -15.59% | 1/12 | 26.5% | sma_cross=609 trailing_stop=174 stop_loss=158 |
| SOL/USDT | **STABLE_NEGATIVE** | 934 | -15.99% | 1/12 | 31.5% | sma_cross=657 trailing_stop=139 stop_loss=137 time_stop=1 |
| BNB/USDT | **STABLE_NEGATIVE** | 902 | -19.15% | 1/12 | 25.4% | sma_cross=583 stop_loss=161 trailing_stop=158 |
| XRP/USDT | **STABLE_NEGATIVE** | 906 | -9.21% | 3/12 | 28.1% | sma_cross=593 trailing_stop=180 stop_loss=133 |
| AVAX/USDT | **STABLE_NEGATIVE** | 927 | -12.79% | 1/12 | 32.7% | sma_cross=614 trailing_stop=167 stop_loss=145 time_stop=1 |
| ARB/USDT | **STABLE_NEGATIVE** | 919 | -16.63% | 2/12 | 29.6% | sma_cross=598 stop_loss=165 trailing_stop=156 |
| OP/USDT | **STABLE_NEGATIVE** | 951 | -16.98% | 2/12 | 29.5% | sma_cross=651 trailing_stop=151 stop_loss=149 |

## Per-quarter detail

### BTC/USDT → STABLE_NEGATIVE
_11/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 76 | -1.73% | NEGATIVE |
| 2 | 53 | -0.49% | NEGATIVE |
| 3 | 73 | -1.02% | NEGATIVE |
| 4 | 78 | -0.74% | NEGATIVE |
| 5 | 71 | -1.08% | NEGATIVE |
| 6 | 74 | -1.85% | NEGATIVE |
| 7 | 84 | -2.12% | NEGATIVE |
| 8 | 68 | -1.27% | NEGATIVE |
| 9 | 79 | -2.05% | NEGATIVE |
| 10 | 71 | -1.47% | NEGATIVE |
| 11 | 68 | -1.70% | NEGATIVE |
| 12 | 66 | +0.20% | POSITIVE |

### ETH/USDT → STABLE_NEGATIVE
_11/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 79 | -1.49% | NEGATIVE |
| 2 | 61 | -1.21% | NEGATIVE |
| 3 | 82 | -1.50% | NEGATIVE |
| 4 | 76 | -1.74% | NEGATIVE |
| 5 | 81 | -1.45% | NEGATIVE |
| 6 | 82 | -2.11% | NEGATIVE |
| 7 | 81 | -1.16% | NEGATIVE |
| 8 | 80 | -1.07% | NEGATIVE |
| 9 | 82 | -0.61% | NEGATIVE |
| 10 | 83 | -1.33% | NEGATIVE |
| 11 | 82 | -2.12% | NEGATIVE |
| 12 | 73 | +0.18% | POSITIVE |

### SOL/USDT → STABLE_NEGATIVE
_11/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 77 | -1.73% | NEGATIVE |
| 2 | 78 | -1.38% | NEGATIVE |
| 3 | 75 | -0.42% | NEGATIVE |
| 4 | 76 | -1.82% | NEGATIVE |
| 5 | 72 | -0.05% | NEGATIVE |
| 6 | 83 | -2.81% | NEGATIVE |
| 7 | 81 | -0.99% | NEGATIVE |
| 8 | 78 | -2.19% | NEGATIVE |
| 9 | 81 | -1.29% | NEGATIVE |
| 10 | 79 | -1.12% | NEGATIVE |
| 11 | 76 | -2.67% | NEGATIVE |
| 12 | 78 | +0.49% | POSITIVE |

### BNB/USDT → STABLE_NEGATIVE
_11/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 71 | -2.29% | NEGATIVE |
| 2 | 61 | -1.35% | NEGATIVE |
| 3 | 83 | -3.07% | NEGATIVE |
| 4 | 78 | -1.46% | NEGATIVE |
| 5 | 69 | -0.41% | NEGATIVE |
| 6 | 79 | -1.61% | NEGATIVE |
| 7 | 85 | -3.76% | NEGATIVE |
| 8 | 81 | -0.99% | NEGATIVE |
| 9 | 69 | -1.32% | NEGATIVE |
| 10 | 67 | +0.62% | POSITIVE |
| 11 | 76 | -2.06% | NEGATIVE |
| 12 | 84 | -1.58% | NEGATIVE |

### XRP/USDT → STABLE_NEGATIVE
_9/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 75 | +0.00% | POSITIVE |
| 2 | 69 | -0.40% | NEGATIVE |
| 3 | 77 | -3.41% | NEGATIVE |
| 4 | 81 | -0.52% | NEGATIVE |
| 5 | 80 | -0.99% | NEGATIVE |
| 6 | 77 | -2.38% | NEGATIVE |
| 7 | 68 | +2.97% | POSITIVE |
| 8 | 74 | -0.45% | NEGATIVE |
| 9 | 74 | -1.42% | NEGATIVE |
| 10 | 84 | -1.84% | NEGATIVE |
| 11 | 76 | -0.83% | NEGATIVE |
| 12 | 72 | +0.02% | POSITIVE |

### AVAX/USDT → STABLE_NEGATIVE
_11/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 82 | -1.29% | NEGATIVE |
| 2 | 82 | -2.10% | NEGATIVE |
| 3 | 71 | -0.51% | NEGATIVE |
| 4 | 83 | -3.21% | NEGATIVE |
| 5 | 83 | -0.64% | NEGATIVE |
| 6 | 74 | -1.18% | NEGATIVE |
| 7 | 74 | -0.93% | NEGATIVE |
| 8 | 73 | +0.33% | POSITIVE |
| 9 | 74 | -1.44% | NEGATIVE |
| 10 | 71 | -0.10% | NEGATIVE |
| 11 | 84 | -1.61% | NEGATIVE |
| 12 | 77 | -0.19% | NEGATIVE |

### ARB/USDT → STABLE_NEGATIVE
_10/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 79 | -1.36% | NEGATIVE |
| 2 | 77 | -1.44% | NEGATIVE |
| 3 | 80 | -4.91% | NEGATIVE |
| 4 | 82 | -2.84% | NEGATIVE |
| 5 | 75 | -0.97% | NEGATIVE |
| 6 | 86 | -2.24% | NEGATIVE |
| 7 | 69 | -0.60% | NEGATIVE |
| 8 | 75 | -0.28% | NEGATIVE |
| 9 | 75 | +1.28% | POSITIVE |
| 10 | 73 | -0.10% | NEGATIVE |
| 11 | 80 | -3.26% | NEGATIVE |
| 12 | 69 | +0.16% | POSITIVE |

### OP/USDT → STABLE_NEGATIVE
_10/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 82 | -0.88% | NEGATIVE |
| 2 | 79 | -2.69% | NEGATIVE |
| 3 | 79 | -2.56% | NEGATIVE |
| 4 | 78 | -2.43% | NEGATIVE |
| 5 | 79 | -2.57% | NEGATIVE |
| 6 | 82 | -1.21% | NEGATIVE |
| 7 | 78 | -3.48% | NEGATIVE |
| 8 | 78 | -0.27% | NEGATIVE |
| 9 | 77 | +1.09% | POSITIVE |
| 10 | 80 | -0.34% | NEGATIVE |
| 11 | 80 | -1.74% | NEGATIVE |
| 12 | 79 | +0.09% | POSITIVE |

## Aggregate
- Symbols sweep: 8
- STABLE_POSITIVE: 0
- STABLE_NEGATIVE: 8
- OUTLIER_DRIVEN: 0
- Total trades: 7341
- Total realized PnL: -12166.33