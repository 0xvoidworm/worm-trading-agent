# Backtest Report — `step14b_funding_flip_365d_V3`

**Window:** 2025-04-27T00:00:00 → 2026-04-25T00:00:00  
**Cycles:** 8713 × 1h  
**Exchange/TF:** bybit / 1h  
**Starting capital:** $10000.00  

## Edge Verdict: **INSUFFICIENT_SAMPLE**

> ⚠ **INSUFFICIENT_SAMPLE**: only `27` closed trades (< 30 threshold). Edge verdict statistically meaningless — return/sharpe figures below are descriptive, not inferential. Need more closed trades (longer window or denser signal regime) sebelum bisa conclude.

## Returns — Realized vs Mark-to-Market

| Metric | Value |
|---|---|
| Realized return % (closed trades only) | -2.91% |
| MTM return % (incl. open trades at end) | -2.91% |
| Final equity (MTM) | $9709.01 |
| Max drawdown | 4.70% |
| Sharpe (annualized) | -0.55 |
| BnH BTC return % | -18.35% |

## Trade Stats

- Total trades opened: **27**
- Closed: **27** | Open at end: **0** | Wins: 11 | Losses: 16
- Win rate (closed only): **40.7%**
- Avg PnL/closed trade: $-10.78
- Best: $+125.43 | Worst: $-125.01

## Health Breakdown (per cycle)

| Dimension | OK | GAP | STALE |
|---|---|---|---|
| trading | 8713 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| operational | 88 (1.0%) | 8625 (99.0%) | — |

_Backtest gates ENTRY hanya pada `trading.status != OK` (Step 6). Operational status (funding/news feeds) di-record untuk transparency tapi tidak gate trading decision._

## Cycle Action Breakdown

- `WAIT`: 8663 (99.4%)
- `CLOSE`: 24 (0.3%)
- `OPEN`: 22 (0.3%)
- `MULTI(E2X0)`: 2 (0.0%)
- `MULTI(E1X1)`: 1 (0.0%)
- `MULTI(E0X2)`: 1 (0.0%)

## Notes

- Sample-size threshold: ≥30 closed trades required for verdict to be statistically meaningful.
- Verdict heuristic (only applied when sample ≥ threshold): `positive` if return > BnH AND sharpe > 0.5; `negative` if return < BnH-10pp OR sharpe < 0; `neutral` otherwise.
- Look-ahead bias prevented via `StorageClamped` (view-based time clamping over `_as_of` table).
- Trades booked on `PaperBroker` with 0.11% per-side fee at close.
- Killswitch state persisted to per-run `tmp_dir` (production filesystem semantics preserved).
