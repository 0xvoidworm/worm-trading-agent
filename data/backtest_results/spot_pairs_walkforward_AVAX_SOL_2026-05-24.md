# Spot Pairs Backtest — 2026-05-24

_Generated: 2026-05-24T05:08:28.479654+00:00_

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
| AVAX/USDT/SOL/USDT [P1(2023-05-22 06:00:00→2024-05-22 04:00:00)] | 18 | 61.1 | +8.989 | +6.393 | +260.61 | +70.48 | -12.16 | 206 | STABLE_POSITIVE |
| AVAX/USDT/SOL/USDT [P2(2024-05-22 05:00:00→2025-05-23 03:00:00)] | 19 | 52.6 | +3.469 | +4.327 | +62.31 | +28.04 | -11.11 | 195 | MARGINAL_POSITIVE |
| AVAX/USDT/SOL/USDT [P3(2025-05-23 04:00:00→2026-05-24 03:00:00)] | 21 | 52.4 | +0.130 | +4.103 | -7.26 | +25.23 | -12.18 | 147 | MARGINAL_POSITIVE |

## Per-Pair Detail

### AVAX/USDT/SOL/USDT [P1(2023-05-22 06:00:00→2024-05-22 04:00:00)]
- N trades: 18 (11 wins, 7 losses, 61.1% wr)
- Net avg/trade: **+8.989%** (median +6.393%)
- Compound total: **+260.61%**
- Best/worst: +70.48% / -12.16%
- Median hold: 206h (8.6 days)
- First signal: 2023-06-26 17:00:00, last: 2024-04-13 21:00:00
  - BUY_A: 13 trades, avg +11.338%
  - BUY_B: 5 trades, avg +2.882%
  - exit STOP_LOSS: 5, avg -11.034%
  - exit TIME_STOP: 5, avg +13.799%
  - exit Z_REVERT: 8, avg +18.498%
- **Verdict: STABLE_POSITIVE**

### AVAX/USDT/SOL/USDT [P2(2024-05-22 05:00:00→2025-05-23 03:00:00)]
- N trades: 19 (10 wins, 9 losses, 52.6% wr)
- Net avg/trade: **+3.469%** (median +4.327%)
- Compound total: **+62.31%**
- Best/worst: +28.04% / -11.11%
- Median hold: 195h (8.1 days)
- First signal: 2024-06-22 04:00:00, last: 2025-05-04 13:00:00
  - BUY_A: 10 trades, avg +4.414%
  - BUY_B: 9 trades, avg +2.419%
  - exit STOP_LOSS: 8, avg -10.437%
  - exit TIME_STOP: 3, avg +9.884%
  - exit Z_REVERT: 8, avg +14.968%
- **Verdict: MARGINAL_POSITIVE**

### AVAX/USDT/SOL/USDT [P3(2025-05-23 04:00:00→2026-05-24 03:00:00)]
- N trades: 21 (11 wins, 10 losses, 52.4% wr)
- Net avg/trade: **+0.130%** (median +4.103%)
- Compound total: **-7.26%**
- Best/worst: +25.23% / -12.18%
- Median hold: 147h (6.1 days)
- First signal: 2025-06-28 17:00:00, last: 2026-05-07 09:00:00
  - BUY_A: 12 trades, avg -0.301%
  - BUY_B: 9 trades, avg +0.703%
  - exit STOP_LOSS: 8, avg -10.740%
  - exit TIME_STOP: 1, avg +7.086%
  - exit Z_REVERT: 12, avg +6.796%
- **Verdict: MARGINAL_POSITIVE**
