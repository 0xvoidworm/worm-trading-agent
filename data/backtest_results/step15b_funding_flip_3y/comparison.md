# Step 14B — FUNDING_FLIP-Only Paper Backtest — `step15b_funding_flip_3y`

**Window:** 2023-04-27T00:00:00 → 2026-04-26T00:00:00 (1095 days)  
**Quarters:** 12 non-overlapping segments  
**Variants tested:** 3 (V1, V2, V3 — no cherry-pick)  
**Detector whitelist:** `FUNDING_FLIP` only  
**Dominant band:** |curr| ∈ [0.01%, 0.03%) — Step 13B D3 evidence  
**Min trades/quarter for STABLE verdict:** 8  
**Min total closed for non-INSUFFICIENT verdict:** 30  

## Variant Comparison

| Variant | Closed | Open@End | Realized% | MTM% | BnH BTC% | α realized | α MTM | Sharpe | MaxDD% | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **V1** | 56 | 0 | -11.53 | -11.53 | +168.57 | -180.10 | -180.10 | -1.03 | 13.08 | **INCONSISTENT** |
| **V2** | 57 | 0 | -6.33 | -6.33 | +168.57 | -174.90 | -174.90 | -0.69 | 10.10 | **INCONSISTENT** |
| **V3** | 56 | 0 | -13.65 | -13.65 | +168.57 | -182.22 | -182.22 | -1.15 | 13.96 | **INCONSISTENT** |

## Per-Variant Detail

### V1 — Existing exits (SL=5% TP=5% max_hold=72h regime_flip_t=6.0)

_Verdict: **INCONSISTENT** — quarter(s) [1, 3, 4, 5, 6, 7, 8, 9, 10] have <8 trades (sparse, unreliable)_  
_quarters_with_data: 12/12 · sign_consistency: 1/12_  
_exits_overrides: `{}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2023-04-27 → 2023-07-27 | 2 | $-59.19 | -0.59% | `SPARSE` |
| Q2 | 2023-07-27 → 2023-10-26 | 8 | $-115.08 | -1.15% | `NEGATIVE` |
| Q3 | 2023-10-26 → 2024-01-25 | 2 | $-161.69 | -1.62% | `SPARSE` |
| Q4 | 2024-01-25 → 2024-04-25 | 2 | $-59.39 | -0.59% | `SPARSE` |
| Q5 | 2024-04-25 → 2024-07-25 | 3 | $-244.22 | -2.44% | `SPARSE` |
| Q6 | 2024-07-25 → 2024-10-24 | 5 | $-156.28 | -1.56% | `SPARSE` |
| Q7 | 2024-10-24 → 2025-01-23 | 3 | $-4.62 | -0.05% | `SPARSE` |
| Q8 | 2025-01-23 → 2025-04-24 | 4 | $-265.59 | -2.66% | `SPARSE` |
| Q9 | 2025-04-24 → 2025-07-24 | 5 | $-89.62 | -0.90% | `SPARSE` |
| Q10 | 2025-07-24 → 2025-10-23 | 3 | $+3.77 | +0.04% | `SPARSE` |
| Q11 | 2025-10-23 → 2026-01-22 | 11 | $+326.69 | +3.27% | `POSITIVE` |
| Q12 | 2026-01-22 → 2026-04-26 | 8 | $-328.04 | -3.28% | `NEGATIVE` |

Health: trading_OK=26269 trading_GAP=0 trading_STALE=12
Sub-run elapsed: 2342.4s

### V2 — max_hold=24h (regime_flip enabled t=6.0)

_Verdict: **INCONSISTENT** — quarter(s) [1, 3, 4, 5, 6, 7, 8, 9, 10] have <8 trades (sparse, unreliable)_  
_quarters_with_data: 12/12 · sign_consistency: 1/12_  
_exits_overrides: `{'max_hold_hours': 24}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2023-04-27 → 2023-07-27 | 2 | $-3.61 | -0.04% | `SPARSE` |
| Q2 | 2023-07-27 → 2023-10-26 | 8 | $-197.11 | -1.97% | `NEGATIVE` |
| Q3 | 2023-10-26 → 2024-01-25 | 2 | $-161.25 | -1.61% | `SPARSE` |
| Q4 | 2024-01-25 → 2024-04-25 | 2 | $-47.30 | -0.47% | `SPARSE` |
| Q5 | 2024-04-25 → 2024-07-25 | 3 | $-156.95 | -1.57% | `SPARSE` |
| Q6 | 2024-07-25 → 2024-10-24 | 5 | $-65.78 | -0.66% | `SPARSE` |
| Q7 | 2024-10-24 → 2025-01-23 | 3 | $+64.52 | +0.65% | `SPARSE` |
| Q8 | 2025-01-23 → 2025-04-24 | 4 | $-307.73 | -3.08% | `SPARSE` |
| Q9 | 2025-04-24 → 2025-07-24 | 5 | $-14.37 | -0.14% | `SPARSE` |
| Q10 | 2025-07-24 → 2025-10-23 | 3 | $+3.89 | +0.04% | `SPARSE` |
| Q11 | 2025-10-23 → 2026-01-22 | 11 | $+475.44 | +4.75% | `POSITIVE` |
| Q12 | 2026-01-22 → 2026-04-26 | 9 | $-222.30 | -2.22% | `NEGATIVE` |

Health: trading_OK=26269 trading_GAP=0 trading_STALE=12
Sub-run elapsed: 2225.4s

### V3 — max_hold=72h, regime_flip DISABLED (t=999 ≈ off)

_Verdict: **INCONSISTENT** — quarter(s) [1, 3, 4, 5, 6, 7, 8, 9, 10] have <8 trades (sparse, unreliable)_  
_quarters_with_data: 12/12 · sign_consistency: 1/12_  
_exits_overrides: `{'regime_flip_t_threshold': 999.0}`_

| Quarter | Window | Closed | Realized PnL | Return% | Sign |
|---|---|---|---|---|---|
| Q1 | 2023-04-27 → 2023-07-27 | 2 | $-59.19 | -0.59% | `SPARSE` |
| Q2 | 2023-07-27 → 2023-10-26 | 8 | $-217.58 | -2.18% | `NEGATIVE` |
| Q3 | 2023-10-26 → 2024-01-25 | 2 | $-107.89 | -1.08% | `SPARSE` |
| Q4 | 2024-01-25 → 2024-04-25 | 2 | $-73.96 | -0.74% | `SPARSE` |
| Q5 | 2024-04-25 → 2024-07-25 | 3 | $-245.89 | -2.46% | `SPARSE` |
| Q6 | 2024-07-25 → 2024-10-24 | 5 | $-155.20 | -1.55% | `SPARSE` |
| Q7 | 2024-10-24 → 2025-01-23 | 3 | $-12.56 | -0.13% | `SPARSE` |
| Q8 | 2025-01-23 → 2025-04-24 | 3 | $-223.91 | -2.24% | `SPARSE` |
| Q9 | 2025-04-24 → 2025-07-24 | 6 | $-182.82 | -1.83% | `SPARSE` |
| Q10 | 2025-07-24 → 2025-10-23 | 3 | $+76.04 | +0.76% | `SPARSE` |
| Q11 | 2025-10-23 → 2026-01-22 | 11 | $+263.56 | +2.64% | `POSITIVE` |
| Q12 | 2026-01-22 → 2026-04-26 | 8 | $-425.74 | -4.26% | `NEGATIVE` |

Health: trading_OK=26269 trading_GAP=0 trading_STALE=12
Sub-run elapsed: 2282.7s

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
