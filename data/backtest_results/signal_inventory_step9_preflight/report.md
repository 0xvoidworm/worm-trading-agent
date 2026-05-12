# Signal Inventory Pre-Flight — Step 9

**Window:** 2025-04-26T00:00:00 → 2026-04-26T00:00:00 (365 days)  
**Quarters:** 4 equal segments  
**Event types audited:** REGIME_CHANGE, FUNDING_EXTREME, FUNDING_FLIP, Z_SPIKE  
_Operational events (RISK_*, FEED_DEAD, CYCLE_ABNORMAL) excluded — tidak relevan utk trading decision._

## Section A — Inventory Table

| Event Type | Total | Date Range | Active Days | Pairs | Median | p75 | p95 | Max | Mean Gap (h) |
|---|---|---|---|---|---|---|---|---|---|
| `REGIME_CHANGE` | 77 | 2026-04-19 → 2026-04-25 | 5 | 8 | 2.66 | 7.44 | 15.40 | 18.03 | 2.1 |
| `FUNDING_EXTREME` | 3 | 2026-02-19 → 2026-02-20 | 2 | 1 | 0.12 | 0.18 | 0.24 | 0.25 | 8.0 |
| `FUNDING_FLIP` | 21 | 2025-11-14 → 2026-04-08 | 18 | 5 | 0.02 | 0.03 | 0.04 | 0.04 | 174.8 |
| `Z_SPIKE` | 35 | 2026-04-04 → 2026-04-24 | 16 | 2 | 2.25 | 2.68 | 3.75 | 4.12 | 14.3 |

_Catatan unit strength:_
- `REGIME_CHANGE` → |t-stat| (unitless, ~0..30)
- `Z_SPIKE`       → |z|      (unitless, ~2..6 typical)
- `FUNDING_*`     → |rate|   (percent, ~0.02..0.5%) — beda skala, histogram default-bucket akan crowd di [0,2).

### Histogram per event_type (strength buckets)

**REGIME_CHANGE** (n=77):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 24 | 31.2% |
| `[2,4)` | 22 | 28.6% |
| `[4,6)` | 9 | 11.7% |
| `[6,8)` | 4 | 5.2% |
| `[8,10)` | 2 | 2.6% |
| `[10,15)` | 11 | 14.3% |
| `[15,+inf)` | 5 | 6.5% |

**FUNDING_EXTREME** (n=3):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 3 | 100.0% |
| `[2,4)` | 0 | 0.0% |
| `[4,6)` | 0 | 0.0% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

**FUNDING_FLIP** (n=21):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 21 | 100.0% |
| `[2,4)` | 0 | 0.0% |
| `[4,6)` | 0 | 0.0% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

**Z_SPIKE** (n=35):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 0 | 0.0% |
| `[2,4)` | 10 | 28.6% |
| `[4,6)` | 1 | 2.9% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

### Per-pair breakdown

- **REGIME_CHANGE** (77): `SOL/USDT`=12, `AVAX/USDT`=12, `OP/USDT`=11, `ETH/USDT`=9, `BNB/USDT`=9, `BTC/USDT`=8, `XRP/USDT`=8, `ARB/USDT`=8
- **FUNDING_EXTREME** (3): `OP/USDT`=3
- **FUNDING_FLIP** (21): `ARB/USDT`=8, `AVAX/USDT`=7, `SOL/USDT`=4, `OP/USDT`=1, `BNB/USDT`=1
- **Z_SPIKE** (35): `SOL/USDT|ETH/USDT`=18, `AVAX/USDT|SOL/USDT`=17

## Section B — Quarter Heatmap

| Event Type | Q1 | Q2 | Q3 | Q4 | Total |
|---|---|---|---|---|---|
| `REGIME_CHANGE` | 0 | 0 | 0 | 77 | 77 |
| `FUNDING_EXTREME` | 0 | 0 | 0 | 3 | 3 |
| `FUNDING_FLIP` | 0 | 0 | 10 | 11 | 21 |
| `Z_SPIKE` | 0 | 0 | 0 | 35 | 35 |

Quarter windows:
- Q1: 2025-04-26 → 2025-07-26
- Q2: 2025-07-26 → 2025-10-25
- Q3: 2025-10-25 → 2026-01-24
- Q4: 2026-01-24 → 2026-04-26

## Section C — Complementarity Score

REGIME_CHANGE alone: **40** unique (date, pair) tuples · **5** unique days

All event types fused: **85** unique (date, pair) tuples · **37** unique days

**Fusion uplift (date, pair):** +112.5%
**Fusion uplift (unique days):** +640.0%

### Extra days per non-REGIME type (vs REGIME_CHANGE coverage)

- **FUNDING_EXTREME** adds 2 unique days: 2026-02-19, 2026-02-20
- **FUNDING_FLIP** adds 18 unique days: 2025-11-14, 2025-11-23, 2025-11-25, 2025-11-27, 2025-12-01, … (+13 more)
- **Z_SPIKE** adds 14 unique days: 2026-04-04, 2026-04-05, 2026-04-06, 2026-04-07, 2026-04-09, … (+9 more)

### Overlap bars (>1 event_type same hour+symbol): **0**

## Section D — Verdict

### Decision: **FUSION_GO**

_fusion uplifts (date,pair) tuples by 112% (≥2x density) — worth integrating_

**Key metrics:**
- REGIME_CHANGE total: 77
- Fusion (date,pair) uplift: +112.5% (40 → 85)
- Fusion unique-days uplift: +640.0%

**Notes:**
- LOW_SUPPLY: FUNDING_EXTREME only 3 signals — fusion contribution marginal

## Section E — Recommended Step 10 Framing

→ **Step 10 = Multi-detector fusion in `auto_advisor.py`.**

Plumbing tasks:
1. Extend `_load_candidates()` to query all 4 event_types, not just REGIME_CHANGE.
2. Define normalized `confidence` per type (e.g. t-stat / z / |rate*scale|).
3. Dedup logic untuk overlap bars (same hour+symbol multi-type) — pilih event dengan strength tertinggi atau combine confidence.
4. Tetap respect concurrent cap + per-pair cap dari Step 8.
5. Re-run sweep `step10_fusion_97d` dan compare ke `step8_multitrade_97d`.
