# Step 14B — FUNDING_FLIP-Only Paper Backtest — `step14b_funding_flip_365d`

**Window:** 2025-04-27T00:00:00 → 2026-04-25T00:00:00 (363 days)  
**Quarters:** 4 non-overlapping segments  
**Variants tested:** 3 (V1, V2, V3 — no cherry-pick)  
**Detector whitelist:** `FUNDING_FLIP` only  
**Dominant band:** |curr| ∈ [0.01%, 0.03%) — Step 13B D3 evidence  
**Min trades/quarter for STABLE verdict:** 8  
**Min total closed for non-INSUFFICIENT verdict:** 30  

## Variant Comparison

| Variant | Closed | Open@End | Realized% | MTM% | BnH BTC% | α realized | α MTM | Sharpe | MaxDD% | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **V1** | 27 | 0 | -0.98 | -0.98 | -18.35 | +17.37 | +17.37 | -0.18 | 3.58 | **INSUFFICIENT_SAMPLE** |
| **V2** | 28 | 0 | +2.66 | +2.66 | -18.35 | +21.01 | +21.01 | 0.76 | 2.32 | **INSUFFICIENT_SAMPLE** |
| **V3** | 27 | 0 | -2.91 | -2.91 | -18.35 | +15.44 | +15.44 | -0.55 | 4.70 | **INSUFFICIENT_SAMPLE** |

## Per-Variant Detail

### V1 — Existing exits (SL=5% TP=5% max_hold=72h regime_flip_t=6.0)

_Verdict: **INSUFFICIENT_SAMPLE** — only 27 closed trades (<30)_  
_quarters_with_data: 4/4 · sign_consistency: 1/4_  
_exits_overrides: `{}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2025-04-27 → 2025-07-26 | 5 | $-100.32 | -1.00% | `SPARSE` |
| Q2 | 2025-07-26 → 2025-10-24 | 3 | $+4.22 | +0.04% | `SPARSE` |
| Q3 | 2025-10-24 → 2026-01-22 | 11 | $+365.67 | +3.66% | `POSITIVE` |
| Q4 | 2026-01-22 → 2026-04-25 | 8 | $-367.18 | -3.67% | `NEGATIVE` |

Health: trading_OK=8713 trading_GAP=0 trading_STALE=0
Sub-run elapsed: 499.4s

### V2 — max_hold=24h (regime_flip enabled t=6.0)

_Verdict: **INSUFFICIENT_SAMPLE** — only 28 closed trades (<30)_  
_quarters_with_data: 4/4 · sign_consistency: 1/4_  
_exits_overrides: `{'max_hold_hours': 24}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2025-04-27 → 2025-07-26 | 5 | $-15.74 | -0.16% | `SPARSE` |
| Q2 | 2025-07-26 → 2025-10-24 | 3 | $+4.26 | +0.04% | `SPARSE` |
| Q3 | 2025-10-24 → 2026-01-22 | 11 | $+521.05 | +5.21% | `POSITIVE` |
| Q4 | 2026-01-22 → 2026-04-25 | 9 | $-243.62 | -2.44% | `NEGATIVE` |

Health: trading_OK=8713 trading_GAP=0 trading_STALE=0
Sub-run elapsed: 554.4s

### V3 — max_hold=72h, regime_flip DISABLED (t=999 ≈ off)

_Verdict: **INSUFFICIENT_SAMPLE** — only 27 closed trades (<30)_  
_quarters_with_data: 4/4 · sign_consistency: 1/4_  
_exits_overrides: `{'regime_flip_t_threshold': 999.0}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2025-04-27 → 2025-07-26 | 5 | $-194.13 | -1.94% | `SPARSE` |
| Q2 | 2025-07-26 → 2025-10-24 | 3 | $+85.50 | +0.85% | `SPARSE` |
| Q3 | 2025-10-24 → 2026-01-22 | 11 | $+296.34 | +2.96% | `POSITIVE` |
| Q4 | 2026-01-22 → 2026-04-25 | 8 | $-478.70 | -4.79% | `NEGATIVE` |

Health: trading_OK=8713 trading_GAP=0 trading_STALE=0
Sub-run elapsed: 563.6s

## Verdict Heuristic

Per-quarter sign label uses Step 7 taxonomy. Aggregate verdict requires ≥3 quarters with data and ≥8 closed trades per quarter for STABLE classification.

- `STABLE_POSITIVE` — ≥3/4 quarter (data ≥8) positive
- `STABLE_NEGATIVE` — ≥3/4 quarter (data ≥8) negative
- `INCONSISTENT` — mixed signs OR sparse quarter present
- `INSUFFICIENT_SAMPLE` — total closed < 30
- `INSUFFICIENT_SAMPLE_DISTRIBUTION` — <3 quarters punya trades

Alpha vs BnH BTC: `alpha = strategy_return - bnh_return`. Positive alpha = strategy beat passive BTC hold; negative = strategy lost vs passive. BnH baseline same across variants (computed on raw OHLCV).

## Caveat

**No cherry-pick. No 'winner' announcement.** Step 14B = bridge from D3 metric edge to actual P&L. STABLE_POSITIVE on ≥1 variant is evidence FUNDING_FLIP-only path is viable; INSUFFICIENT_SAMPLE on all variants means signal supply too sparse even after dominant-band filter (n=28 historical → likely <20 closed trades per quarter across 4 quarters of 8 symbols).

If all 3 variants INSUFFICIENT_SAMPLE: Step 14B inconclusive. Options for Step 14C/15: (a) widen band to capture more signals, (b) extend window beyond 365d, (c) abandon FUNDING_FLIP-only.
