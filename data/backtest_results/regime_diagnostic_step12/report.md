# REGIME_CHANGE Diagnostic — Step 12A

_Generated: 2026-04-26T09:10:23.517925_
_Source DB: data/market.duckdb (post-replay state)_

Total REGIME_CHANGE events analysed: **6856** (per-symbol last signal excluded from run-length: 8 signals)

## D1 — Run-Length Distribution per Symbol

Run-length = bars between consecutive REGIME_CHANGE.ts per symbol (state lifetime before next flip).

| Symbol | 1 | 2-3 | 4-6 | 7-12 | 13-24 | 25-48 | 48+ | Total | %≤3bar | %≥13bar |
|---|---|---|---|---|---|---|---|---|---|---|
| `ARB/USDT` | 69 | 107 | 186 | 224 | 182 | 58 | 11 | 837 | 21.0% | 30.0% |
| `AVAX/USDT` | 72 | 113 | 191 | 218 | 177 | 64 | 12 | 847 | 21.8% | 29.9% |
| `BNB/USDT` | 48 | 105 | 202 | 224 | 198 | 46 | 12 | 835 | 18.3% | 30.7% |
| `BTC/USDT` | 56 | 126 | 186 | 222 | 204 | 49 | 9 | 852 | 21.4% | 30.8% |
| `ETH/USDT` | 71 | 141 | 198 | 223 | 203 | 55 | 7 | 898 | 23.6% | 29.5% |
| `OP/USDT` | 67 | 124 | 193 | 216 | 176 | 58 | 12 | 846 | 22.6% | 29.1% |
| `SOL/USDT` | 68 | 129 | 204 | 215 | 187 | 48 | 14 | 865 | 22.8% | 28.8% |
| `XRP/USDT` | 65 | 120 | 198 | 229 | 204 | 44 | 8 | 868 | 21.3% | 29.5% |

**OVERALL:** 6848 signals, ≤3bar=1481 (21.6%), ≥13bar=2038 (29.8%)

## D2 — |t-stat| Distribution (full post-replay)

n = **6854** | mean = 3.64

| Quantile | |t-stat| |
|---|---|
| p25 | 1.75 |
| p50 | 2.23 |
| p75 | 3.83 |
| p90 | 8.86 |
| p95 | 11.32 |
| p99 | 16.31 |
| max | 26.92 |

Histogram:

| Band | Count | Pct |
|---|---|---|
| `[0,1)` | 312 | 4.6% |
| `[1,2)` | 2300 | 33.6% |
| `[2,3)` | 2173 | 31.7% |
| `[3,5)` | 616 | 9.0% |
| `[5,8)` | 619 | 9.0% |
| `[8,12)` | 563 | 8.2% |
| `[12,20)` | 262 | 3.8% |
| `[20,+)` | 9 | 0.1% |

_Compare Step 9 (n=77, pre-replay):_ med=2.66, p75=7.44, p95=15.40
_Step 12 (n=6854, post-replay):_ med=2.23, p75=3.83, p95=11.32

## D3 — Predictive Power (signed forward returns)

`signed_return` = forward return aligned with signal direction (positive = direction-correct). Excludes `to=ranging|chaos`.

| t-band | horizon_h | n | mean signed | median signed | hit-rate |
|---|---|---|---|---|---|
| `[12,20)` | 1 | 212 | -0.022% | +0.012% | 0.505 |
| `[2,3)` | 1 | 1731 | +0.017% | -0.023% | 0.467 |
| `[20,+)` | 1 | 6 | +0.109% | -0.060% | 0.500 |
| `[3,5)` | 1 | 335 | -0.065% | -0.070% | 0.409 |
| `[5,8)` | 1 | 368 | -0.042% | -0.000% | 0.476 |
| `[8,12)` | 1 | 389 | +0.022% | -0.000% | 0.496 |
| `[12,20)` | 4 | 212 | +0.114% | +0.007% | 0.500 |
| `[2,3)` | 4 | 1731 | +0.022% | -0.055% | 0.468 |
| `[20,+)` | 4 | 6 | +0.481% | +0.349% | 0.500 |
| `[3,5)` | 4 | 335 | -0.129% | -0.044% | 0.475 |
| `[5,8)` | 4 | 368 | -0.140% | -0.205% | 0.413 |
| `[8,12)` | 4 | 389 | -0.124% | -0.022% | 0.478 |
| `[12,20)` | 24 | 212 | +0.563% | +0.050% | 0.509 |
| `[2,3)` | 24 | 1725 | +0.141% | +0.067% | 0.510 |
| `[20,+)` | 24 | 6 | -2.140% | -1.574% | 0.167 |
| `[3,5)` | 24 | 331 | -0.375% | -0.259% | 0.462 |
| `[5,8)` | 24 | 366 | +0.019% | -0.393% | 0.432 |
| `[8,12)` | 24 | 388 | -0.236% | -0.363% | 0.433 |

## D4 — Run-Length × |t-stat| Correlation

**Pearson(run_length_bars, |t-stat|) = +0.177**

| Run-Length Bucket | n | mean |t-stat| | median |t-stat| |
|---|---|---|---|
| `1` | 515 | 3.04 | 2.09 |
| `13-24` | 1531 | 4.57 | 2.65 |
| `2-3` | 964 | 2.32 | 1.79 |
| `25-48` | 422 | 4.51 | 2.56 |
| `4-6` | 1558 | 2.67 | 1.86 |
| `48+` | 85 | 4.17 | 2.57 |
| `7-12` | 1771 | 4.36 | 2.47 |

## VERDICT

### **SCENARIO_S2**

_Long-persistence signals have higher t-stat → debounce by run-length._

Evidence:
- S1 (threshold-fixable) evidence: **False**
- S2 (debounce-fixable) evidence: **True**
- Structural noise indicator (≥50% run-len ≤3 bars): **False**

Notes:
- overall pct run_length<=3 bars: 21.6%
- overall pct run_length>=13 bars: 29.8%
- 24h hit-rate uplift top-band vs bot-band: -0.076
- abs_t (run_len=1) mean = 3.04, long-runs mean = 4.57
- pearson(run_length, abs_t) = +0.177
