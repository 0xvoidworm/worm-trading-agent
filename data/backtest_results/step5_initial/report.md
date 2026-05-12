# Backtest Report — `step5_initial`

**Window:** 2026-02-26T00:00:00 → 2026-04-25T00:00:00  
**Cycles:** 1393 × 1h  
**Exchange/TF:** bybit / 1h  
**Starting capital:** $10000.00  

## Edge Verdict: **NEGATIVE**

| Metric | Strategy | BnH BTC |
|---|---|---|
| Return % | -1.07% | +13.36% |
| Final equity | $9892.67 | — |
| Max drawdown | 1.58% | — |
| Sharpe (annualized) | -2.63 | — |

## Trade Stats

- Total trades opened: **4**
- Closed: **1** | Wins: 0 | Losses: 1
- Win rate: **0.0%**
- Avg PnL/trade: $-60.62
- Best: $-60.62 | Worst: $-60.62

## Cycle Action Breakdown

- `GATED(GAP)`: 1305
- `SKIP`: 83
- `OPEN`: 4
- `CLOSE`: 1

## Health States

- `GAP`: 1305
- `OK`: 88

## Notes

- Verdict heuristic: `positive` if return > BnH AND sharpe > 0.5; `negative` if return < BnH-10pp OR sharpe < 0; `neutral` otherwise.
- Look-ahead bias prevented via `StorageClamped` (view-based time clamping over `_as_of` table).
- Trades booked on `PaperBroker` with 0.11% per-side fee at close.
- Killswitch state persisted to per-run `tmp_dir` (production filesystem semantics preserved).
