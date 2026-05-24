# Spot Pairs Backtest — 2026-05-24

_Generated: 2026-05-24T05:02:36.453308+00:00_

## Params
- **lookback_bars**: 720
- **z_entry**: 2.0
- **z_exit**: 0.0
- **max_hold_days**: 14
- **stop_loss_pct**: 10.0
- **fee_pct_per_side**: 0.1
- **n_pairs**: 6

## Summary Table
| Pair | N | Win% | Avg net% | Median net% | Total compound% | Best | Worst | Median hold (h) | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ETH/USDT/BTC/USDT | 54 | 44.4 | +0.276 | -0.988 | -8.23 | +19.62 | -15.26 | 240 | INCONSISTENT |
| SOL/USDT/ETH/USDT | 50 | 48.0 | +0.511 | -0.144 | -4.22 | +25.78 | -15.27 | 232 | INCONSISTENT |
| AVAX/USDT/SOL/USDT | 63 | 52.4 | +3.267 | +4.327 | +311.67 | +70.48 | -12.92 | 149 | MARGINAL_POSITIVE |
| ARB/USDT/OP/USDT | 67 | 32.8 | -1.248 | -7.655 | -76.85 | +66.93 | -15.41 | 132 | STABLE_NEGATIVE |
| BNB/USDT/BTC/USDT | 52 | 55.8 | +0.625 | +0.939 | +20.47 | +17.51 | -12.25 | 234 | STABLE_POSITIVE |
| XRP/USDT/LTC/USDT | 0 | — | — | — | — | — | — | — | NO_SIGNALS |

## Per-Pair Detail

### ETH/USDT/BTC/USDT
- N trades: 54 (24 wins, 30 losses, 44.4% wr)
- Net avg/trade: **+0.276%** (median -0.988%)
- Compound total: **-8.23%**
- Best/worst: +19.62% / -15.26%
- Median hold: 240h (10.0 days)
- First signal: 2023-06-25 15:00:00, last: 2026-05-07 12:00:00
  - BUY_A: 36 trades, avg +0.228%
  - BUY_B: 18 trades, avg +0.371%
  - exit STOP_LOSS: 12, avg -11.400%
  - exit TIME_STOP: 21, avg +3.167%
  - exit Z_REVERT: 21, avg +4.056%
- **Verdict: INCONSISTENT**

### SOL/USDT/ETH/USDT
- N trades: 50 (24 wins, 26 losses, 48.0% wr)
- Net avg/trade: **+0.511%** (median -0.144%)
- Compound total: **-4.22%**
- Best/worst: +25.78% / -15.27%
- Median hold: 232h (9.6 days)
- First signal: 2023-07-06 17:00:00, last: 2026-05-07 06:00:00
  - BUY_A: 24 trades, avg -0.127%
  - BUY_B: 26 trades, avg +1.100%
  - exit STOP_LOSS: 17, avg -11.234%
  - exit TIME_STOP: 16, avg +4.614%
  - exit Z_REVERT: 17, avg +8.395%
- **Verdict: INCONSISTENT**

### AVAX/USDT/SOL/USDT
- N trades: 63 (33 wins, 30 losses, 52.4% wr)
- Net avg/trade: **+3.267%** (median +4.327%)
- Compound total: **+311.67%**
- Best/worst: +70.48% / -12.92%
- Median hold: 149h (6.2 days)
- First signal: 2023-06-26 17:00:00, last: 2026-05-07 09:00:00
  - BUY_A: 40 trades, avg +4.083%
  - BUY_B: 23 trades, avg +1.848%
  - exit STOP_LOSS: 24, avg -10.762%
  - exit TIME_STOP: 10, avg +11.510%
  - exit Z_REVERT: 29, avg +12.034%
- **Verdict: MARGINAL_POSITIVE**

### ARB/USDT/OP/USDT
- N trades: 67 (22 wins, 45 losses, 32.8% wr)
- Net avg/trade: **-1.248%** (median -7.655%)
- Compound total: **-76.85%**
- Best/worst: +66.93% / -15.41%
- Median hold: 132h (5.5 days)
- First signal: 2023-06-27 15:00:00, last: 2026-05-06 21:00:00
  - BUY_A: 34 trades, avg -1.441%
  - BUY_B: 33 trades, avg -1.049%
  - exit STOP_LOSS: 32, avg -11.547%
  - exit TIME_STOP: 11, avg +8.356%
  - exit Z_REVERT: 24, avg +8.083%
- **Verdict: STABLE_NEGATIVE**

### BNB/USDT/BTC/USDT
- N trades: 52 (29 wins, 23 losses, 55.8% wr)
- Net avg/trade: **+0.625%** (median +0.939%)
- Compound total: **+20.47%**
- Best/worst: +17.51% / -12.25%
- Median hold: 234h (9.8 days)
- First signal: 2023-06-24 15:00:00, last: 2026-05-01 22:00:00
  - BUY_A: 31 trades, avg +1.974%
  - BUY_B: 21 trades, avg -1.366%
  - exit STOP_LOSS: 7, avg -11.014%
  - exit TIME_STOP: 17, avg +0.013%
  - exit Z_REVERT: 28, avg +3.907%
- **Verdict: STABLE_POSITIVE**
