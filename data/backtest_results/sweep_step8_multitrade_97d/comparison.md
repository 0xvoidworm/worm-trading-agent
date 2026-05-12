# Threshold Sensitivity Sweep — `step8_multitrade_97d`

**Window:** 2026-01-19T00:00:00 → 2026-04-26T00:00:00 (97 days)  
**Quarters:** 4 non-overlapping segments  
**Thresholds tested:** 4  
**Min trades/quarter for STABLE verdict:** 8  
**Min total closed for non-INSUFFICIENT verdict:** 30  

## Sensitivity Table

| Threshold | Total | Closed | Open@End | Realized% | MTM% | Sharpe | Q1 sign | Q2 sign | Q3 sign | Q4 sign | Q1 N (ret%) | Q2 N (ret%) | Q3 N (ret%) | Q4 N (ret%) | QwD | Sign Cons | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| t≥2.66 | 6 | 6 | 0 | -3.29 | -3.29 | -2.30 | NO_DATA | NO_DATA | NO_DATA | SPARSE | 0 (NO_DATA) | 0 (NO_DATA) | 0 (NO_DATA) | 6 (-3.29) | 1/4 | 0/1 | **INSUFFICIENT_SAMPLE** |
| t≥4 | 6 | 6 | 0 | -3.29 | -3.29 | -2.30 | NO_DATA | NO_DATA | NO_DATA | SPARSE | 0 (NO_DATA) | 0 (NO_DATA) | 0 (NO_DATA) | 6 (-3.29) | 1/4 | 0/1 | **INSUFFICIENT_SAMPLE** |
| t≥6 | 6 | 6 | 0 | -3.29 | -3.29 | -2.30 | NO_DATA | NO_DATA | NO_DATA | SPARSE | 0 (NO_DATA) | 0 (NO_DATA) | 0 (NO_DATA) | 6 (-3.29) | 1/4 | 0/1 | **INSUFFICIENT_SAMPLE** |
| t≥8 | 6 | 6 | 0 | -3.29 | -3.29 | -2.30 | NO_DATA | NO_DATA | NO_DATA | SPARSE | 0 (NO_DATA) | 0 (NO_DATA) | 0 (NO_DATA) | 6 (-3.29) | 1/4 | 0/1 | **INSUFFICIENT_SAMPLE** |

## Per-Threshold Detail

### t≥2.66 → **INSUFFICIENT_SAMPLE**

_only 6 closed trades (<30)_  
_quarters_with_data: 1/4 · sign_consistency: 0/1_

| Quarter | Window | Closed Trades | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2026-01-19 → 2026-02-12 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q2 | 2026-02-12 → 2026-03-08 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q3 | 2026-03-08 → 2026-04-01 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q4 | 2026-04-01 → 2026-04-26 | 6 | $-329.40 | -3.29% | `SPARSE` |

Health: trading_OK=2329 trading_GAP=0 trading_STALE=0 | operational_OK=99
Sub-run elapsed: 123.1s

### t≥4 → **INSUFFICIENT_SAMPLE**

_only 6 closed trades (<30)_  
_quarters_with_data: 1/4 · sign_consistency: 0/1_

| Quarter | Window | Closed Trades | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2026-01-19 → 2026-02-12 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q2 | 2026-02-12 → 2026-03-08 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q3 | 2026-03-08 → 2026-04-01 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q4 | 2026-04-01 → 2026-04-26 | 6 | $-329.40 | -3.29% | `SPARSE` |

Health: trading_OK=2329 trading_GAP=0 trading_STALE=0 | operational_OK=99
Sub-run elapsed: 143.3s

### t≥6 → **INSUFFICIENT_SAMPLE**

_only 6 closed trades (<30)_  
_quarters_with_data: 1/4 · sign_consistency: 0/1_

| Quarter | Window | Closed Trades | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2026-01-19 → 2026-02-12 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q2 | 2026-02-12 → 2026-03-08 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q3 | 2026-03-08 → 2026-04-01 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q4 | 2026-04-01 → 2026-04-26 | 6 | $-329.40 | -3.29% | `SPARSE` |

Health: trading_OK=2329 trading_GAP=0 trading_STALE=0 | operational_OK=99
Sub-run elapsed: 129.2s

### t≥8 → **INSUFFICIENT_SAMPLE**

_only 6 closed trades (<30)_  
_quarters_with_data: 1/4 · sign_consistency: 0/1_

| Quarter | Window | Closed Trades | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2026-01-19 → 2026-02-12 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q2 | 2026-02-12 → 2026-03-08 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q3 | 2026-03-08 → 2026-04-01 | 0 | $+0.00 | +0.00% | `NO_DATA` |
| Q4 | 2026-04-01 → 2026-04-26 | 6 | $-329.40 | -3.29% | `SPARSE` |

Health: trading_OK=2329 trading_GAP=0 trading_STALE=0 | operational_OK=99
Sub-run elapsed: 162.2s

## Caveat

Reported as-is. **NO cherry-picking, NO 'winner' announcement.** STABLE_POSITIVE results are CANDIDATES for further validation, NOT production-ready. Step 7 = sensitivity map + consistency check, BUKAN parameter selection.

- `STABLE_POSITIVE` / `STABLE_NEGATIVE`: ≥3 quarter (dari yang punya data) konsisten direction.
- `INCONSISTENT`: ada quarter SPARSE (1..<8 trades) ATAU mixed signs di quarters_with_data.
- `INSUFFICIENT_SAMPLE`: total closed trades < 30 (below CLT applicability).
- `INSUFFICIENT_SAMPLE_DISTRIBUTION`: <3 quarters punya trades — sample numpuk di sedikit segmen waktu, consistency tidak bisa diuji.

Per-quarter sign label:
- `POSITIVE` / `NEGATIVE` / `FLAT` — ≥8 trades, return positive/negative/exactly-zero
- `SPARSE` — 1..<8 trades (data ada tapi tidak reliable)
- `NO_DATA` — 0 trades closed (dikecualikan dari sign tally)

Quarter return uses **starting_capital** as denominator (apple-to-apple across quarters, NOT compounded). Goal: consistency check, not absolute return measurement.
