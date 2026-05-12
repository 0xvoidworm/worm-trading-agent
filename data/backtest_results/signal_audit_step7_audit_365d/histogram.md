# Signal Distribution Audit — `REGIME_CHANGE`

**Window:** 2025-04-26T00:00:00 → 2026-04-25T00:00:00 (365 days)  
**Total signals:** 56  
**Parseable t-stat:** 56 (unparseable: 0)  

## t-stat Histogram

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 18 | 32.1% |
| `[2,4)` | 15 | 26.8% |
| `[4,6)` | 5 | 8.9% |
| `[6,8)` | 4 | 7.1% |
| `[8,10)` | 2 | 3.6% |
| `[10,12)` | 4 | 7.1% |
| `[12,15)` | 4 | 7.1% |
| `[15,20)` | 4 | 7.1% |
| `[20,+inf)` | 0 | 0.0% |

## Distribution Stats

- Min: 0.06 | Max: 18.03 | Mean: 5.37
- p25: 1.69 | **Median: 2.66** | p75: 8.19 | p95: 15.49

## Recommended Thresholds (percentile cuts)

_Threshold yang akan capture top-N% paling-kuat signals. Semata referensi untuk Step 7 calibration — JANGAN auto-tune._

| Cut | t-stat threshold |
|---|---|
| top-25% (p75) | 8.19 |
| top-10% (p90) | 13.81 |
| top-5% (p95) | 15.49 |

## Per-Pair Breakdown

| Pair | Count |
|---|---|
| `OP/USDT` | 8 |
| `ETH/USDT` | 8 |
| `BTC/USDT` | 7 |
| `SOL/USDT` | 7 |
| `AVAX/USDT` | 7 |
| `XRP/USDT` | 7 |
| `ARB/USDT` | 6 |
| `BNB/USDT` | 6 |

## Per-Direction

- LONG (trending_up):  13
- SHORT (trending_down): 18
- UNKNOWN:               25

## Sparsity

- Active days (≥1 signal): 4 / 365 (1.1%)
- Avg signals/day: 0.15
