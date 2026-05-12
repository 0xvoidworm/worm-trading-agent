# Step 15A — Slippage Sensitivity (Recompute, no re-run)

Source: `step14b_funding_flip_365d` V1/V2/V3 trades.csv
Starting capital: 10000 USDT
BnH BTC baseline (1y window): -18.3503%

## Scenarios

| Variant | bps/side | n | Realized% | Sharpe | MaxDD% | α vs BnH | Slip cost (USDT) |
|---|---|---|---|---|---|---|---|
| V1 | 0 | 27 | -0.9760 | -0.2099 | 3.5754 | +17.3743 | 0.00 |
| V1 | 5 | 27 | -1.5144 | -0.3359 | 3.7474 | +16.8359 | 53.84 |
| V1 | 10 | 27 | -2.0529 | -0.4606 | 3.9207 | +16.2974 | 107.69 |
| V1 | 20 | 27 | -3.1298 | -0.7047 | 4.2710 | +15.2205 | 215.38 |
| V2 | 0 | 28 | +2.6595 | +0.7601 | 2.3181 | +21.0097 | 0.00 |
| V2 | 5 | 28 | +2.0924 | +0.6039 | 2.5057 | +20.4427 | 56.70 |
| V2 | 10 | 28 | +1.5254 | +0.4462 | 2.6947 | +19.8757 | 113.40 |
| V2 | 20 | 28 | +0.3914 | +0.1283 | 3.0768 | +18.7417 | 226.81 |
| V3 | 0 | 27 | -2.9099 | -0.6160 | 4.6988 | +15.4404 | 0.00 |
| V3 | 5 | 27 | -3.4465 | -0.7297 | 4.8755 | +14.9038 | 53.66 |
| V3 | 10 | 27 | -3.9830 | -0.8417 | 5.0536 | +14.3673 | 107.31 |
| V3 | 20 | 27 | -5.0562 | -1.0601 | 5.4138 | +13.2941 | 214.63 |

## Decision Gate (per spec, V2 anchor)

- V2 @ 5bps realized = **+2.0924%**
- V2 @ 10bps realized = **+1.5254%**

**VERDICT: `PROCEED_15B`** — V2 @ 10bps masih > 0%

Decision rules:
- V2 @ 10bps > 0% → PROCEED 15B
- V2 @ 5bps ≤ 0% → STOP, pivot strategy (edge tipis untuk friction)
- V2 @ 5bps > 0% but V2 @ 10bps ≤ 0% → PROCEED 15B with concern flag

## Notes
- Slippage applied 2 sides (entry + exit), proportional to notional
- Sharpe: realized equity series, daily resampled, annualised √365
- MaxDD recomputed on slippage-adjusted equity
- Per-trade `pnl` di trades.csv sudah net of fees (0.11% per side via PaperBroker), slippage = friction tambahan on top of fees
