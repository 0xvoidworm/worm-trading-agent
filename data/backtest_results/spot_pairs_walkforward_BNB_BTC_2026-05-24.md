# Spot Pairs Backtest — 2026-05-24

_Generated: 2026-05-24T05:08:30.000216+00:00_

## Params
- **lookback_bars**: 720
- **z_entry**: 2.0
- **z_exit**: 0.0
- **max_hold_days**: 14
- **stop_loss_pct**: 10.0
- **fee_pct_per_side**: 0.1
- **n_pairs**: 1

## Summary Table
| Pair | N | Win% | Avg net% | Median net% | Total compound% | Best | Worst | Median hold (h) | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BNB/USDT/BTC/USDT [P1(2023-05-22 06:00:00→2024-05-22 04:00:00)] | 16 | 62.5 | +1.370 | +1.647 | +19.38 | +12.07 | -10.45 | 236 | STABLE_POSITIVE |
| BNB/USDT/BTC/USDT [P2(2024-05-22 05:00:00→2025-05-23 03:00:00)] | 17 | 76.5 | +2.898 | +3.653 | +53.74 | +17.51 | -12.13 | 227 | STABLE_POSITIVE |
| BNB/USDT/BTC/USDT [P3(2025-05-23 04:00:00→2026-05-24 03:00:00)] | 16 | 31.2 | -2.308 | -3.132 | -33.14 | +8.71 | -12.25 | 265 | STABLE_NEGATIVE |

## Per-Pair Detail

### BNB/USDT/BTC/USDT [P1(2023-05-22 06:00:00→2024-05-22 04:00:00)]
- N trades: 16 (10 wins, 6 losses, 62.5% wr)
- Net avg/trade: **+1.370%** (median +1.647%)
- Compound total: **+19.38%**
- Best/worst: +12.07% / -10.45%
- Median hold: 236h (9.9 days)
- First signal: 2023-06-24 15:00:00, last: 2024-04-18 04:00:00
  - BUY_A: 9 trades, avg +2.943%
  - BUY_B: 7 trades, avg -0.651%
  - exit STOP_LOSS: 2, avg -10.420%
  - exit TIME_STOP: 6, avg +0.012%
  - exit Z_REVERT: 8, avg +5.337%
- **Verdict: STABLE_POSITIVE**

### BNB/USDT/BTC/USDT [P2(2024-05-22 05:00:00→2025-05-23 03:00:00)]
- N trades: 17 (13 wins, 4 losses, 76.5% wr)
- Net avg/trade: **+2.898%** (median +3.653%)
- Compound total: **+53.74%**
- Best/worst: +17.51% / -12.13%
- Median hold: 227h (9.5 days)
- First signal: 2024-07-05 02:00:00, last: 2025-04-29 12:00:00
  - BUY_A: 11 trades, avg +3.608%
  - BUY_B: 6 trades, avg +1.596%
  - exit STOP_LOSS: 3, avg -11.252%
  - exit TIME_STOP: 5, avg +2.490%
  - exit Z_REVERT: 9, avg +7.841%
- **Verdict: STABLE_POSITIVE**

### BNB/USDT/BTC/USDT [P3(2025-05-23 04:00:00→2026-05-24 03:00:00)]
- N trades: 16 (5 wins, 11 losses, 31.2% wr)
- Net avg/trade: **-2.308%** (median -3.132%)
- Compound total: **-33.14%**
- Best/worst: +8.71% / -12.25%
- Median hold: 265h (11.0 days)
- First signal: 2025-06-25 13:00:00, last: 2026-05-01 22:00:00
  - BUY_A: 9 trades, avg -0.524%
  - BUY_B: 7 trades, avg -4.601%
  - exit STOP_LOSS: 2, avg -11.249%
  - exit TIME_STOP: 5, avg -2.993%
  - exit Z_REVERT: 9, avg +0.059%
- **Verdict: STABLE_NEGATIVE**
