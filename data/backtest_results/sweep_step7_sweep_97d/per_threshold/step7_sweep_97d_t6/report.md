# Backtest Report — `step7_sweep_97d_t6`

**Window:** 2026-01-19T00:00:00 → 2026-04-26T00:00:00  
**Cycles:** 2329 × 1h  
**Exchange/TF:** bybit / 1h  
**Starting capital:** $10000.00  

## Edge Verdict: **INSUFFICIENT_SAMPLE**

> ⚠ **INSUFFICIENT_SAMPLE**: only `4` closed trades (< 30 threshold). Edge verdict statistically meaningless — return/sharpe figures below are descriptive, not inferential. Need more closed trades (longer window or denser signal regime) sebelum bisa conclude.

## Returns — Realized vs Mark-to-Market

| Metric | Value |
|---|---|
| Realized return % (closed trades only) | -2.47% |
| MTM return % (incl. open trades at end) | -2.47% |
| Final equity (MTM) | $9753.09 |
| Max drawdown | 3.88% |
| Sharpe (annualized) | -1.83 |
| BnH BTC return % | -16.39% |

## Trade Stats

- Total trades opened: **4**
- Closed: **4** | Open at end: **0** | Wins: 0 | Losses: 4
- Win rate (closed only): **0.0%**
- Avg PnL/closed trade: $-61.73
- Best: $-54.54 | Worst: $-73.76

## Health Breakdown (per cycle)

| Dimension | OK | GAP | STALE |
|---|---|---|---|
| trading | 2329 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| operational | 99 (4.3%) | 2230 (95.7%) | — |

_Backtest gates ENTRY hanya pada `trading.status != OK` (Step 6). Operational status (funding/news feeds) di-record untuk transparency tapi tidak gate trading decision._

## Cycle Action Breakdown

- `SKIP`: 2321 (99.7%)
- `OPEN`: 4 (0.2%)
- `CLOSE`: 4 (0.2%)

## Notes

- Sample-size threshold: ≥30 closed trades required for verdict to be statistically meaningful.
- Verdict heuristic (only applied when sample ≥ threshold): `positive` if return > BnH AND sharpe > 0.5; `negative` if return < BnH-10pp OR sharpe < 0; `neutral` otherwise.
- Look-ahead bias prevented via `StorageClamped` (view-based time clamping over `_as_of` table).
- Trades booked on `PaperBroker` with 0.11% per-side fee at close.
- Killswitch state persisted to per-run `tmp_dir` (production filesystem semantics preserved).
