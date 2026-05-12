# Step 19 — Ensemble voting backtest

**Window:** 2023-04-27 → 2026-04-26 (1095 days)  
**Quarters:** 12  
**Lookback votes:** 24h  
**Min agreement:** 3  
**Min distinct types:** 3  
**Debounce:** 4h  
**Stops:** 2.0× ATR (initial), 4.0× ATR (trail)  
**Slippage:** 10.0 bps/side

## Per-symbol summary

| Symbol | Verdict | n_events | n_trades | Realized% | Sign | Win% |
|---|---|---|---|---|---|---|
| BTC/USDT | **INCONSISTENT** | 150 | 39 | -0.71% | 1/4 | 25.6% |
| ETH/USDT | **OUTLIER_DRIVEN** | 164 | 36 | -1.70% | 1/3 | 25.0% |
| SOL/USDT | **INCONSISTENT** | 245 | 69 | -1.70% | 1/10 | 31.9% |
| BNB/USDT | **INCONSISTENT** | 404 | 109 | -6.60% | 0/12 | 16.5% |
| XRP/USDT | **INCONSISTENT** | 233 | 62 | -1.68% | 0/7 | 33.9% |
| AVAX/USDT | **INCONSISTENT** | 236 | 70 | -3.59% | 1/11 | 31.4% |
| ARB/USDT | **INCONSISTENT** | 246 | 58 | -1.72% | 1/8 | 36.2% |
| OP/USDT | **INCONSISTENT** | 282 | 57 | -5.97% | 1/10 | 28.1% |

## Per-quarter detail

### BTC/USDT → INCONSISTENT
_quarter(s) [8] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 0 | +0.00% | NO_DATA |
| 3 | 12 | +0.09% | POSITIVE |
| 4 | 13 | -0.28% | NEGATIVE |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 12 | -0.74% | NEGATIVE |
| 8 | 2 | +0.21% | SPARSE |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 0 | +0.00% | NO_DATA |
| 11 | 0 | +0.00% | NO_DATA |
| 12 | 0 | +0.00% | NO_DATA |

### ETH/USDT → OUTLIER_DRIVEN
_Q7 contributes 79.0% of |total realized PnL| (≥60.0%) — single-quarter outlier dominates result, edge claim NOT reliable_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 0 | +0.00% | NO_DATA |
| 3 | 10 | -0.25% | NEGATIVE |
| 4 | 10 | +0.18% | POSITIVE |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 16 | -1.62% | NEGATIVE |
| 8 | 0 | +0.00% | NO_DATA |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 0 | +0.00% | NO_DATA |
| 11 | 0 | +0.00% | NO_DATA |
| 12 | 0 | +0.00% | NO_DATA |

### SOL/USDT → INCONSISTENT
_quarter(s) [1, 2, 5, 6, 8, 10, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.40% | SPARSE |
| 2 | 6 | +1.01% | SPARSE |
| 3 | 17 | +0.24% | POSITIVE |
| 4 | 16 | -1.45% | NEGATIVE |
| 5 | 2 | -0.08% | SPARSE |
| 6 | 2 | -0.09% | SPARSE |
| 7 | 13 | -0.84% | NEGATIVE |
| 8 | 3 | +0.65% | SPARSE |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 2 | -0.44% | SPARSE |
| 11 | 0 | +0.00% | NO_DATA |
| 12 | 5 | -0.31% | SPARSE |

### BNB/USDT → INCONSISTENT
_quarter(s) [6, 9, 10, 11, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 18 | -1.80% | NEGATIVE |
| 2 | 8 | -0.13% | NEGATIVE |
| 3 | 15 | -0.78% | NEGATIVE |
| 4 | 21 | -0.26% | NEGATIVE |
| 5 | 9 | -0.87% | NEGATIVE |
| 6 | 5 | -0.14% | SPARSE |
| 7 | 15 | -1.07% | NEGATIVE |
| 8 | 10 | -0.95% | NEGATIVE |
| 9 | 2 | -0.06% | SPARSE |
| 10 | 2 | -0.39% | SPARSE |
| 11 | 3 | -0.12% | SPARSE |
| 12 | 1 | -0.03% | SPARSE |

### XRP/USDT → INCONSISTENT
_quarter(s) [5, 8, 10, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 0 | +0.00% | NO_DATA |
| 3 | 23 | -0.32% | NEGATIVE |
| 4 | 22 | -1.41% | NEGATIVE |
| 5 | 2 | -0.13% | SPARSE |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 10 | -0.28% | NEGATIVE |
| 8 | 3 | +0.19% | SPARSE |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 1 | +0.39% | SPARSE |
| 11 | 0 | +0.00% | NO_DATA |
| 12 | 1 | -0.11% | SPARSE |

### AVAX/USDT → INCONSISTENT
_quarter(s) [1, 2, 5, 8, 10, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 1 | +0.12% | SPARSE |
| 2 | 1 | -0.07% | SPARSE |
| 3 | 20 | -0.26% | NEGATIVE |
| 4 | 15 | +0.16% | POSITIVE |
| 5 | 1 | +0.29% | SPARSE |
| 6 | 9 | -1.34% | NEGATIVE |
| 7 | 12 | -1.85% | NEGATIVE |
| 8 | 1 | -0.28% | SPARSE |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 1 | -0.48% | SPARSE |
| 11 | 8 | -0.26% | NEGATIVE |
| 12 | 1 | +0.37% | SPARSE |

### ARB/USDT → INCONSISTENT
_quarter(s) [5, 6, 10, 11, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 0 | +0.00% | NO_DATA |
| 3 | 19 | -1.79% | NEGATIVE |
| 4 | 19 | +0.33% | POSITIVE |
| 5 | 3 | +0.61% | SPARSE |
| 6 | 1 | -0.27% | SPARSE |
| 7 | 8 | -1.20% | NEGATIVE |
| 8 | 0 | +0.00% | NO_DATA |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 1 | -0.05% | SPARSE |
| 11 | 3 | -0.43% | SPARSE |
| 12 | 4 | +1.09% | SPARSE |

### OP/USDT → INCONSISTENT
_quarter(s) [2, 5, 6, 8, 9, 10, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 1 | -0.12% | SPARSE |
| 3 | 21 | -3.39% | NEGATIVE |
| 4 | 17 | +0.37% | POSITIVE |
| 5 | 1 | -0.25% | SPARSE |
| 6 | 1 | -0.22% | SPARSE |
| 7 | 10 | -1.96% | NEGATIVE |
| 8 | 1 | +0.11% | SPARSE |
| 9 | 1 | -0.14% | SPARSE |
| 10 | 1 | -0.49% | SPARSE |
| 11 | 0 | +0.00% | NO_DATA |
| 12 | 3 | +0.12% | SPARSE |

## Aggregate
- STABLE_POSITIVE: 0
- STABLE_NEGATIVE: 0
- Total realized PnL: -2367.36