# Step 17.B3 — Cointegration pair-trade backtest

**Window:** 2023-04-27 → 2026-04-26 (1095 days)  
**Quarters:** 12  
**Entry-z mode:** dynamic  
**Lookback bars:** 720  
**Refit freq:** 24 bars  
**Slippage:** 10.0 bps/side  
**Risk per pair:** 5.0% equity (leg A notional)

## Per-pair summary

| Pair | Verdict | n_trades | Realized% | Sign | EntryZ | CoIntRate |
|---|---|---|---|---|---|---|
| ETH/USDT|BTC/USDT | **INCONSISTENT** | 58 | -4.80% | 0/12 | 2.56 | 0.17 |
| SOL/USDT|ETH/USDT | **INCONSISTENT** | 51 | -4.29% | 1/11 | 2.53 | 0.16 |
| ARB/USDT|OP/USDT | **INCONSISTENT** | 73 | -2.83% | 0/12 | 2.50 | 0.24 |
| AVAX/USDT|SOL/USDT | **INCONSISTENT** | 76 | -3.24% | 0/12 | 2.47 | 0.20 |

## Per-quarter detail

### ETH/USDT|BTC/USDT → INCONSISTENT
_quarter(s) [1, 2, 4, 5, 6, 7, 8, 9, 10, 11] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 2 | +0.15% | SPARSE |
| 2 | 7 | -0.32% | SPARSE |
| 3 | 10 | -1.11% | NEGATIVE |
| 4 | 1 | -0.03% | SPARSE |
| 5 | 3 | -0.55% | SPARSE |
| 6 | 2 | +0.05% | SPARSE |
| 7 | 1 | +0.01% | SPARSE |
| 8 | 5 | -0.47% | SPARSE |
| 9 | 4 | -1.59% | SPARSE |
| 10 | 1 | +0.05% | SPARSE |
| 11 | 7 | -0.17% | SPARSE |
| 12 | 15 | -0.81% | NEGATIVE |

### SOL/USDT|ETH/USDT → INCONSISTENT
_quarter(s) [1, 2, 3, 4, 5, 7, 8, 10, 12] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 2 | -1.80% | SPARSE |
| 2 | 6 | -0.23% | SPARSE |
| 3 | 3 | -0.16% | SPARSE |
| 4 | 4 | -0.39% | SPARSE |
| 5 | 1 | -0.39% | SPARSE |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 2 | +0.16% | SPARSE |
| 8 | 6 | -0.52% | SPARSE |
| 9 | 10 | -0.91% | NEGATIVE |
| 10 | 2 | -0.05% | SPARSE |
| 11 | 10 | +0.12% | POSITIVE |
| 12 | 5 | -0.11% | SPARSE |

### ARB/USDT|OP/USDT → INCONSISTENT
_quarter(s) [1, 2, 3, 4, 5, 6, 7, 9, 10] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.07% | SPARSE |
| 2 | 4 | -0.01% | SPARSE |
| 3 | 4 | +0.31% | SPARSE |
| 4 | 7 | -0.43% | SPARSE |
| 5 | 2 | -0.44% | SPARSE |
| 6 | 6 | -0.26% | SPARSE |
| 7 | 2 | +0.03% | SPARSE |
| 8 | 11 | -0.13% | NEGATIVE |
| 9 | 6 | -0.20% | SPARSE |
| 10 | 4 | +0.10% | SPARSE |
| 11 | 13 | -0.88% | NEGATIVE |
| 12 | 11 | -0.85% | NEGATIVE |

### AVAX/USDT|SOL/USDT → INCONSISTENT
_quarter(s) [1, 2, 3, 6, 7, 8, 9, 10, 11] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.10% | SPARSE |
| 2 | 2 | +0.07% | SPARSE |
| 3 | 6 | -0.99% | SPARSE |
| 4 | 13 | -0.28% | NEGATIVE |
| 5 | 9 | -0.51% | NEGATIVE |
| 6 | 4 | -0.49% | SPARSE |
| 7 | 2 | -0.08% | SPARSE |
| 8 | 7 | -0.25% | SPARSE |
| 9 | 5 | -0.14% | SPARSE |
| 10 | 7 | -0.39% | SPARSE |
| 11 | 6 | +0.16% | SPARSE |
| 12 | 12 | -0.23% | NEGATIVE |