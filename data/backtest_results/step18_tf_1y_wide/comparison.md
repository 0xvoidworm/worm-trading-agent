# Step 18.B — Trend-follow standalone backtest

**Window:** 2025-04-27 → 2026-04-26 (364 days)  
**Quarters:** 4  
**BB:** SMA(50) ± 2.0×stdev  
**ATR:** 14 bars  
**Stop ATR mult:** 4.0× (initial), 6.0× (trailing)  
**Max hold:** 720h  
**Slippage:** 10.0 bps/side  
**Risk/trade:** 5.0% equity notional

## Per-symbol summary

| Symbol | Verdict | n_trades | Realized% | Sign | Win% | Exits |
|---|---|---|---|---|---|---|
| BTC/USDT | **STABLE_NEGATIVE** | 136 | -2.89% | 0/4 | 27.9% | sma_cross=113 trailing_stop=15 stop_loss=8 |
| ETH/USDT | **INCONSISTENT** | 138 | -1.86% | 2/4 | 27.5% | sma_cross=110 trailing_stop=20 stop_loss=8 |
| SOL/USDT | **STABLE_NEGATIVE** | 140 | -3.52% | 1/4 | 30.7% | sma_cross=119 trailing_stop=14 stop_loss=7 |
| BNB/USDT | **STABLE_NEGATIVE** | 143 | -3.13% | 1/4 | 26.6% | sma_cross=118 trailing_stop=16 stop_loss=9 |
| XRP/USDT | **INCONSISTENT** | 130 | -1.23% | 2/4 | 33.1% | sma_cross=107 trailing_stop=19 stop_loss=4 |
| AVAX/USDT | **OUTLIER_DRIVEN** | 138 | -2.62% | 1/4 | 33.3% | sma_cross=112 trailing_stop=17 stop_loss=9 |
| ARB/USDT | **STABLE_POSITIVE** | 135 | +0.82% | 3/4 | 37.0% | sma_cross=113 trailing_stop=16 stop_loss=6 |
| OP/USDT | **STABLE_POSITIVE** | 134 | +1.78% | 3/4 | 35.8% | sma_cross=112 trailing_stop=16 stop_loss=6 |

## Per-quarter detail

### BTC/USDT → STABLE_NEGATIVE
_4/4 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 35 | -0.70% | NEGATIVE |
| 2 | 34 | -0.75% | NEGATIVE |
| 3 | 34 | -0.79% | NEGATIVE |
| 4 | 33 | -0.65% | NEGATIVE |

### ETH/USDT → INCONSISTENT
_mixed signs across 4 quarter(s) with data_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 36 | +0.21% | POSITIVE |
| 2 | 32 | +0.23% | POSITIVE |
| 3 | 36 | -1.15% | NEGATIVE |
| 4 | 34 | -1.15% | NEGATIVE |

### SOL/USDT → STABLE_NEGATIVE
_3/4 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 36 | -0.68% | NEGATIVE |
| 2 | 32 | +0.71% | POSITIVE |
| 3 | 38 | -2.82% | NEGATIVE |
| 4 | 34 | -0.71% | NEGATIVE |

### BNB/USDT → STABLE_NEGATIVE
_3/4 quarter(s) with data are negative_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 35 | -0.90% | NEGATIVE |
| 2 | 33 | +0.91% | POSITIVE |
| 3 | 37 | -1.63% | NEGATIVE |
| 4 | 38 | -1.50% | NEGATIVE |

### XRP/USDT → INCONSISTENT
_mixed signs across 4 quarter(s) with data_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 29 | +1.22% | POSITIVE |
| 2 | 34 | -1.37% | NEGATIVE |
| 3 | 31 | +0.18% | POSITIVE |
| 4 | 36 | -1.26% | NEGATIVE |

### AVAX/USDT → OUTLIER_DRIVEN
_Q2 contributes 61.7% of |total realized PnL| (≥60.0%) — single-quarter outlier dominates result, edge claim NOT reliable_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 34 | +0.48% | POSITIVE |
| 2 | 36 | -2.21% | NEGATIVE |
| 3 | 35 | -0.35% | NEGATIVE |
| 4 | 33 | -0.54% | NEGATIVE |

### ARB/USDT → STABLE_POSITIVE
_3/4 quarter(s) with data are positive_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 34 | +2.03% | POSITIVE |
| 2 | 36 | +0.63% | POSITIVE |
| 3 | 35 | -2.36% | NEGATIVE |
| 4 | 30 | +0.51% | POSITIVE |

### OP/USDT → STABLE_POSITIVE
_3/4 quarter(s) with data are positive_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 31 | +2.59% | POSITIVE |
| 2 | 31 | +0.09% | POSITIVE |
| 3 | 39 | -2.25% | NEGATIVE |
| 4 | 33 | +1.35% | POSITIVE |

## Aggregate
- Symbols sweep: 8
- STABLE_POSITIVE: 2
- STABLE_NEGATIVE: 3
- OUTLIER_DRIVEN: 1
- Total trades: 1094
- Total realized PnL: -1265.50