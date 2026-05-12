# Backtest Report — `step15b_funding_flip_3y_V1`

**Window:** 2023-04-27T00:00:00 → 2026-04-26T00:00:00  
**Cycles:** 26281 × 1h  
**Exchange/TF:** bybit / 1h  
**Starting capital:** $10000.00  

## Edge Verdict: **NEGATIVE**

## Returns — Realized vs Mark-to-Market

| Metric | Value |
|---|---|
| Realized return % (closed trades only) | -11.53% |
| MTM return % (incl. open trades at end) | -11.53% |
| Final equity (MTM) | $8846.73 |
| Max drawdown | 13.08% |
| Sharpe (annualized) | -1.03 |
| BnH BTC return % | +168.57% |

## Trade Stats

- Total trades opened: **56**
- Closed: **56** | Open at end: **0** | Wins: 18 | Losses: 38
- Win rate (closed only): **32.1%**
- Avg PnL/closed trade: $-20.59
- Best: $+136.37 | Worst: $-155.69

## Health Breakdown (per cycle)

| Dimension | OK | GAP | STALE |
|---|---|---|---|
| trading | 26269 (100.0%) | 0 (0.0%) | 12 (0.0%) |
| operational | 99 (0.4%) | 26182 (99.6%) | — |

_Backtest gates ENTRY hanya pada `trading.status != OK` (Step 6). Operational status (funding/news feeds) di-record untuk transparency tapi tidak gate trading decision._

## Cycle Action Breakdown

- `WAIT`: 26163 (99.6%)
- `CLOSE`: 51 (0.2%)
- `OPEN`: 49 (0.2%)
- `GATED_TRADING(STALE)`: 12 (0.0%)
- `MULTI(E2X0)`: 3 (0.0%)
- `MULTI(E0X2)`: 2 (0.0%)
- `MULTI(E1X1)`: 1 (0.0%)

## Gate Reason Counts

- `trading=STALE`: 12

## Notes

- Sample-size threshold: ≥30 closed trades required for verdict to be statistically meaningful.
- Verdict heuristic (only applied when sample ≥ threshold): `positive` if return > BnH AND sharpe > 0.5; `negative` if return < BnH-10pp OR sharpe < 0; `neutral` otherwise.
- Look-ahead bias prevented via `StorageClamped` (view-based time clamping over `_as_of` table).
- Trades booked on `PaperBroker` with 0.11% per-side fee at close.
- Killswitch state persisted to per-run `tmp_dir` (production filesystem semantics preserved).
