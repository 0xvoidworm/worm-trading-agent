# Step 17.B3 — Cointegration pair-trade backtest

**Window:** 2025-04-27 → 2026-04-26 (364 days)  
**Quarters:** 4  
**Entry-z mode:** dynamic  
**Lookback bars:** 720  
**Refit freq:** 24 bars  
**Slippage:** 10.0 bps/side  
**Risk per pair:** 5.0% equity (leg A notional)

## Per-pair summary

| Pair | Verdict | n_trades | Realized% | Sign | EntryZ | CoIntRate |
|---|---|---|---|---|---|---|
| ETH/USDT|BTC/USDT | **INSUFFICIENT_SAMPLE** | 26 | -0.99% | 0/4 | 2.53 | 0.27 |
| SOL/USDT|ETH/USDT | **INSUFFICIENT_SAMPLE** | 27 | -0.74% | 1/4 | 2.45 | 0.27 |
| ARB/USDT|OP/USDT | **INCONSISTENT** | 31 | -1.99% | 0/4 | 2.53 | 0.31 |
| AVAX/USDT|SOL/USDT | **INSUFFICIENT_SAMPLE** | 29 | -0.47% | 0/4 | 2.46 | 0.27 |

## Per-quarter detail

### ETH/USDT|BTC/USDT → INSUFFICIENT_SAMPLE
_only 26 closed trades (<30)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.11% | SPARSE |
| 2 | 2 | +0.01% | SPARSE |
| 3 | 10 | -0.38% | NEGATIVE |
| 4 | 11 | -0.51% | NEGATIVE |

### SOL/USDT|ETH/USDT → INSUFFICIENT_SAMPLE
_only 27 closed trades (<30)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 6 | -0.50% | SPARSE |
| 2 | 3 | -0.17% | SPARSE |
| 3 | 13 | +0.05% | POSITIVE |
| 4 | 5 | -0.13% | SPARSE |

### ARB/USDT|OP/USDT → INCONSISTENT
_quarter(s) [1, 2] have <8 trades (sparse, unreliable)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.04% | SPARSE |
| 2 | 2 | +0.05% | SPARSE |
| 3 | 13 | -0.88% | NEGATIVE |
| 4 | 13 | -1.11% | NEGATIVE |

### AVAX/USDT|SOL/USDT → INSUFFICIENT_SAMPLE
_only 29 closed trades (<30)_

| Q | n | realized% | sign |
|---|---|---|---|
| 1 | 3 | -0.18% | SPARSE |
| 2 | 8 | -0.47% | NEGATIVE |
| 3 | 7 | +0.20% | SPARSE |
| 4 | 11 | -0.03% | NEGATIVE |