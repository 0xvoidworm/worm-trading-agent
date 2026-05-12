# Step 18.B — Trend-follow standalone backtest

**Window:** 2023-04-27 → 2026-04-26 (1095 days)  
**Quarters:** 12  
**BB:** SMA(50) ± 2.0×stdev  
**ATR:** 14 bars  
**Stop ATR mult:** 4.0× (initial), 6.0× (trailing)  
**Max hold:** 720h  
**Slippage:** 10.0 bps/side  
**Risk/trade:** 5.0% equity notional

## Per-symbol summary

| Symbol | Verdict | n_trades | Realized% | Sign | Win% | Exits |
|---|---|---|---|---|---|---|
| BTC/USDT | **STABLE_NEGATIVE** | 396 | -5.67% | 2/12 | 28.3% | sma_cross=325 trailing_stop=51 stop_loss=20 |
| ETH/USDT | **STABLE_NEGATIVE** | 415 | -6.44% | 4/12 | 27.7% | sma_cross=324 trailing_stop=67 stop_loss=24 |
| SOL/USDT | **STABLE_NEGATIVE** | 412 | -4.91% | 4/12 | 32.0% | sma_cross=367 trailing_stop=31 stop_loss=14 |
| BNB/USDT | **STABLE_NEGATIVE** | 403 | -7.69% | 4/12 | 28.3% | sma_cross=329 trailing_stop=49 stop_loss=25 |
| XRP/USDT | **STABLE_NEGATIVE** | 405 | -1.15% | 2/12 | 27.4% | sma_cross=321 trailing_stop=62 stop_loss=22 |
| AVAX/USDT | **STABLE_NEGATIVE** | 404 | -2.18% | 3/12 | 32.7% | sma_cross=340 trailing_stop=47 stop_loss=17 |
| ARB/USDT | **STABLE_NEGATIVE** | 413 | -5.69% | 5/12 | 33.7% | sma_cross=347 trailing_stop=47 stop_loss=19 |
| OP/USDT | **STABLE_NEGATIVE** | 419 | -5.27% | 5/12 | 32.9% | sma_cross=359 trailing_stop=46 stop_loss=14 |

## Per-quarter detail

### BTC/USDT → STABLE_NEGATIVE
_10/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 34 | -1.04% | NEGATIVE |
| 2 | 26 | -0.29% | NEGATIVE |
| 3 | 36 | -0.51% | NEGATIVE |
| 4 | 35 | -0.14% | NEGATIVE |
| 5 | 34 | -0.67% | NEGATIVE |
| 6 | 33 | +0.22% | POSITIVE |
| 7 | 33 | +0.47% | POSITIVE |
| 8 | 27 | -1.13% | NEGATIVE |
| 9 | 37 | -0.46% | NEGATIVE |
| 10 | 35 | -0.76% | NEGATIVE |
| 11 | 33 | -0.93% | NEGATIVE |
| 12 | 34 | -0.47% | NEGATIVE |

### ETH/USDT → STABLE_NEGATIVE
_8/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 37 | -1.54% | NEGATIVE |
| 2 | 29 | -0.05% | NEGATIVE |
| 3 | 37 | -1.30% | NEGATIVE |
| 4 | 34 | -0.17% | NEGATIVE |
| 5 | 34 | -0.00% | NEGATIVE |
| 6 | 34 | +0.42% | POSITIVE |
| 7 | 34 | +0.50% | POSITIVE |
| 8 | 35 | -2.53% | NEGATIVE |
| 9 | 38 | +0.41% | POSITIVE |
| 10 | 32 | +0.21% | POSITIVE |
| 11 | 37 | -1.31% | NEGATIVE |
| 12 | 34 | -1.10% | NEGATIVE |

### SOL/USDT → STABLE_NEGATIVE
_8/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 34 | -1.04% | NEGATIVE |
| 2 | 33 | +0.59% | POSITIVE |
| 3 | 36 | -0.42% | NEGATIVE |
| 4 | 36 | -0.32% | NEGATIVE |
| 5 | 33 | -0.64% | NEGATIVE |
| 6 | 31 | +0.89% | POSITIVE |
| 7 | 37 | +0.92% | POSITIVE |
| 8 | 28 | -0.96% | NEGATIVE |
| 9 | 39 | -1.24% | NEGATIVE |
| 10 | 32 | +1.06% | POSITIVE |
| 11 | 39 | -3.06% | NEGATIVE |
| 12 | 34 | -0.70% | NEGATIVE |

### BNB/USDT → STABLE_NEGATIVE
_8/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 33 | -1.16% | NEGATIVE |
| 2 | 29 | -0.59% | NEGATIVE |
| 3 | 30 | -1.66% | NEGATIVE |
| 4 | 32 | +1.03% | POSITIVE |
| 5 | 34 | +0.04% | POSITIVE |
| 6 | 29 | +0.47% | POSITIVE |
| 7 | 38 | -2.03% | NEGATIVE |
| 8 | 34 | -0.70% | NEGATIVE |
| 9 | 36 | -0.96% | NEGATIVE |
| 10 | 32 | +0.91% | POSITIVE |
| 11 | 36 | -1.57% | NEGATIVE |
| 12 | 40 | -1.46% | NEGATIVE |

### XRP/USDT → STABLE_NEGATIVE
_10/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 33 | -0.09% | NEGATIVE |
| 2 | 33 | -0.64% | NEGATIVE |
| 3 | 35 | -2.35% | NEGATIVE |
| 4 | 38 | -2.03% | NEGATIVE |
| 5 | 37 | -1.15% | NEGATIVE |
| 6 | 35 | -0.43% | NEGATIVE |
| 7 | 30 | +7.57% | POSITIVE |
| 8 | 31 | -0.62% | NEGATIVE |
| 9 | 31 | +0.76% | POSITIVE |
| 10 | 34 | -0.90% | NEGATIVE |
| 11 | 32 | -0.00% | NEGATIVE |
| 12 | 36 | -1.26% | NEGATIVE |

### AVAX/USDT → STABLE_NEGATIVE
_9/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 35 | -1.60% | NEGATIVE |
| 2 | 35 | -0.32% | NEGATIVE |
| 3 | 37 | -0.69% | NEGATIVE |
| 4 | 32 | -0.83% | NEGATIVE |
| 5 | 34 | -0.62% | NEGATIVE |
| 6 | 30 | +1.91% | POSITIVE |
| 7 | 30 | +2.76% | POSITIVE |
| 8 | 31 | -0.06% | NEGATIVE |
| 9 | 36 | +0.16% | POSITIVE |
| 10 | 36 | -1.90% | NEGATIVE |
| 11 | 36 | -0.54% | NEGATIVE |
| 12 | 33 | -0.54% | NEGATIVE |

### ARB/USDT → STABLE_NEGATIVE
_7/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 36 | -1.99% | NEGATIVE |
| 2 | 36 | -1.02% | NEGATIVE |
| 3 | 34 | -1.48% | NEGATIVE |
| 4 | 36 | -2.57% | NEGATIVE |
| 5 | 36 | -0.65% | NEGATIVE |
| 6 | 32 | +1.18% | POSITIVE |
| 7 | 34 | +0.62% | POSITIVE |
| 8 | 32 | -0.67% | NEGATIVE |
| 9 | 35 | +1.97% | POSITIVE |
| 10 | 36 | +0.90% | POSITIVE |
| 11 | 35 | -2.45% | NEGATIVE |
| 12 | 31 | +0.47% | POSITIVE |

### OP/USDT → STABLE_NEGATIVE
_7/12 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 38 | +0.08% | POSITIVE |
| 2 | 37 | -0.94% | NEGATIVE |
| 3 | 39 | -1.25% | NEGATIVE |
| 4 | 34 | -0.93% | NEGATIVE |
| 5 | 32 | -1.20% | NEGATIVE |
| 6 | 33 | +0.65% | POSITIVE |
| 7 | 36 | -1.79% | NEGATIVE |
| 8 | 34 | -1.87% | NEGATIVE |
| 9 | 32 | +2.61% | POSITIVE |
| 10 | 31 | +0.55% | POSITIVE |
| 11 | 40 | -2.42% | NEGATIVE |
| 12 | 33 | +1.25% | POSITIVE |

## Aggregate
- Symbols sweep: 8
- STABLE_POSITIVE: 0
- STABLE_NEGATIVE: 8
- OUTLIER_DRIVEN: 0
- Total trades: 3267
- Total realized PnL: -3900.94