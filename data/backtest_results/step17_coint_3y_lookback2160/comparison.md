# Step 17.B3 — Cointegration pair-trade backtest

**Window:** 2023-04-27 → 2026-04-26 (1095 days)  
**Quarters:** 12  
**Entry-z mode:** dynamic  
**Lookback bars:** 2160  
**Refit freq:** 72 bars  
**Slippage:** 10.0 bps/side  
**Risk per pair:** 5.0% equity (leg A notional)

## Per-pair summary

| Pair | Verdict | n_trades | Realized% | Sign | EntryZ | CoIntRate |
|---|---|---|---|---|---|---|
| ETH/USDT|BTC/USDT | **INCONSISTENT** | 61 | -2.57% | 0/7 | 2.56 | 0.19 |
| SOL/USDT|ETH/USDT | **INCONSISTENT** | 48 | -2.64% | 0/8 | 2.53 | 0.14 |
| ARB/USDT|OP/USDT | **INCONSISTENT** | 37 | -1.16% | 0/8 | 2.47 | 0.12 |
| AVAX/USDT|SOL/USDT | **INCONSISTENT** | 60 | -0.44% | 1/6 | 2.48 | 0.20 |

## Per-quarter detail

### ETH/USDT|BTC/USDT → INCONSISTENT
_quarter(s) [2, 3, 7, 8, 10] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 6 | -0.23% | SPARSE |
| 3 | 5 | -0.70% | SPARSE |
| 4 | 0 | +0.00% | NO_DATA |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 4 | +0.05% | SPARSE |
| 8 | 4 | +0.09% | SPARSE |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 3 | -0.39% | SPARSE |
| 11 | 14 | -0.49% | NEGATIVE |
| 12 | 25 | -0.91% | NEGATIVE |

### SOL/USDT|ETH/USDT → INCONSISTENT
_quarter(s) [2, 3, 4, 6, 7, 10] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 4 | -0.65% | SPARSE |
| 3 | 7 | +0.47% | SPARSE |
| 4 | 3 | -1.02% | SPARSE |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 5 | -0.32% | SPARSE |
| 7 | 1 | -0.20% | SPARSE |
| 8 | 0 | +0.00% | NO_DATA |
| 9 | 0 | +0.00% | NO_DATA |
| 10 | 6 | +0.01% | SPARSE |
| 11 | 10 | -0.80% | NEGATIVE |
| 12 | 12 | -0.14% | NEGATIVE |

### ARB/USDT|OP/USDT → INCONSISTENT
_quarter(s) [2, 3, 4, 6, 7, 9, 11] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 1 | -0.01% | SPARSE |
| 3 | 2 | +0.12% | SPARSE |
| 4 | 2 | +0.09% | SPARSE |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 2 | -0.26% | SPARSE |
| 7 | 7 | -0.30% | SPARSE |
| 8 | 20 | -0.32% | NEGATIVE |
| 9 | 2 | -0.39% | SPARSE |
| 10 | 0 | +0.00% | NO_DATA |
| 11 | 1 | -0.10% | SPARSE |
| 12 | 0 | +0.00% | NO_DATA |

### AVAX/USDT|SOL/USDT → INCONSISTENT
_quarter(s) [4, 8, 11] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 0 | +0.00% | NO_DATA |
| 2 | 0 | +0.00% | NO_DATA |
| 3 | 0 | +0.00% | NO_DATA |
| 4 | 5 | +0.04% | SPARSE |
| 5 | 0 | +0.00% | NO_DATA |
| 6 | 0 | +0.00% | NO_DATA |
| 7 | 0 | +0.00% | NO_DATA |
| 8 | 4 | +0.18% | SPARSE |
| 9 | 17 | +0.25% | POSITIVE |
| 10 | 10 | -0.25% | NEGATIVE |
| 11 | 7 | -0.30% | SPARSE |
| 12 | 17 | -0.36% | NEGATIVE |