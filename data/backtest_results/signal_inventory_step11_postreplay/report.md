# Signal Inventory Pre-Flight — Step 9

**Window:** 2025-04-26T00:00:00 → 2026-04-26T00:00:00 (365 days)  
**Quarters:** 4 equal segments  
**Event types audited:** REGIME_CHANGE, FUNDING_EXTREME, FUNDING_FLIP, Z_SPIKE  
_Operational events (RISK_*, FEED_DEAD, CYCLE_ABNORMAL) excluded — tidak relevan utk trading decision._

## Section A — Inventory Table

| Event Type | Total | Date Range | Active Days | Pairs | Median | p75 | p95 | Max | Mean Gap (h) |
|---|---|---|---|---|---|---|---|---|---|
| `REGIME_CHANGE` | 6847 | 2025-04-27 → 2026-04-25 | 364 | 8 | 2.23 | 3.84 | 11.34 | 26.92 | 1.3 |
| `FUNDING_EXTREME` | 11 | 2025-10-11 → 2026-02-20 | 3 | 6 | 0.14 | 0.43 | 0.71 | 0.84 | 317.6 |
| `FUNDING_FLIP` | 41 | 2025-05-06 → 2026-04-08 | 31 | 7 | 0.02 | 0.03 | 0.50 | 0.84 | 202.4 |
| `Z_SPIKE` | 373 | 2025-04-30 → 2026-04-24 | 213 | 2 | 2.19 | 2.46 | 3.03 | 5.12 | 23.2 |

_Catatan unit strength:_
- `REGIME_CHANGE` → |t-stat| (unitless, ~0..30)
- `Z_SPIKE`       → |z|      (unitless, ~2..6 typical)
- `FUNDING_*`     → |rate|   (percent, ~0.02..0.5%) — beda skala, histogram default-bucket akan crowd di [0,2).

### Histogram per event_type (strength buckets)

**REGIME_CHANGE** (n=6847):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 2608 | 38.1% |
| `[2,4)` | 2568 | 37.5% |
| `[4,6)` | 416 | 6.1% |
| `[6,8)` | 419 | 6.1% |
| `[8,10)` | 324 | 4.7% |
| `[10,15)` | 406 | 5.9% |
| `[15,+inf)` | 106 | 1.5% |

**FUNDING_EXTREME** (n=11):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 11 | 100.0% |
| `[2,4)` | 0 | 0.0% |
| `[4,6)` | 0 | 0.0% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

**FUNDING_FLIP** (n=41):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 41 | 100.0% |
| `[2,4)` | 0 | 0.0% |
| `[4,6)` | 0 | 0.0% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

**Z_SPIKE** (n=373):

| Bucket | Count | Pct |
|---|---|---|
| `[0,2)` | 0 | 0.0% |
| `[2,4)` | 190 | 50.9% |
| `[4,6)` | 3 | 0.8% |
| `[6,8)` | 0 | 0.0% |
| `[8,10)` | 0 | 0.0% |
| `[10,15)` | 0 | 0.0% |
| `[15,+inf)` | 0 | 0.0% |

### Per-pair breakdown

- **REGIME_CHANGE** (6847): `ETH/USDT`=895, `XRP/USDT`=869, `SOL/USDT`=865, `BTC/USDT`=852, `AVAX/USDT`=848, `OP/USDT`=845, `ARB/USDT`=837, `BNB/USDT`=836
- **FUNDING_EXTREME** (11): `OP/USDT`=4, `SOL/USDT`=3, `ETH/USDT`=1, `XRP/USDT`=1, `AVAX/USDT`=1, `ARB/USDT`=1
- **FUNDING_FLIP** (41): `BNB/USDT`=10, `ARB/USDT`=10, `AVAX/USDT`=9, `SOL/USDT`=7, `OP/USDT`=3, `XRP/USDT`=1, `ETH/USDT`=1
- **Z_SPIKE** (373): `AVAX/USDT|SOL/USDT`=355, `SOL/USDT|ETH/USDT`=18

## Section B — Quarter Heatmap

| Event Type | Q1 | Q2 | Q3 | Q4 | Total |
|---|---|---|---|---|---|
| `REGIME_CHANGE` | 1720 | 1590 | 1788 | 1749 | 6847 |
| `FUNDING_EXTREME` | 0 | 8 | 0 | 3 | 11 |
| `FUNDING_FLIP` | 5 | 11 | 14 | 11 | 41 |
| `Z_SPIKE` | 84 | 90 | 86 | 113 | 373 |

Quarter windows:
- Q1: 2025-04-26 → 2025-07-26
- Q2: 2025-07-26 → 2025-10-25
- Q3: 2025-10-25 → 2026-01-24
- Q4: 2026-01-24 → 2026-04-26

## Section C — Complementarity Score

REGIME_CHANGE alone: **2692** unique (date, pair) tuples · **364** unique days

All event types fused: **2915** unique (date, pair) tuples · **364** unique days

**Fusion uplift (date, pair):** +8.3%
**Fusion uplift (unique days):** +0.0%

### Extra days per non-REGIME type (vs REGIME_CHANGE coverage)

- **FUNDING_EXTREME** adds 0 unique days: —
- **FUNDING_FLIP** adds 0 unique days: —
- **Z_SPIKE** adds 0 unique days: —

### Overlap bars (>1 event_type same hour+symbol): **11**

Sample (max 20):

| Date | Hour | Symbol | Event Types |
|---|---|---|---|
| 2025-10-11 | 00 | OP/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 00 | AVAX/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 00 | XRP/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 00 | ARB/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 00 | ETH/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 00 | SOL/USDT | FUNDING_EXTREME, FUNDING_FLIP |
| 2025-10-11 | 16 | SOL/USDT | FUNDING_EXTREME, REGIME_CHANGE |
| 2025-10-24 | 16 | BNB/USDT | FUNDING_FLIP, REGIME_CHANGE |
| 2026-02-28 | 08 | OP/USDT | FUNDING_FLIP, REGIME_CHANGE |
| 2026-03-06 | 16 | AVAX/USDT | FUNDING_FLIP, REGIME_CHANGE |
| 2026-03-26 | 08 | SOL/USDT | FUNDING_FLIP, REGIME_CHANGE |

## Section D — Verdict

### Decision: **FUSION_INCONCLUSIVE**

_fusion uplift modest (dp=+8%, days=+0%) — fusion plumbing OK but won't move the needle alone_

**Key metrics:**
- REGIME_CHANGE total: 6847
- Fusion (date,pair) uplift: +8.3% (2692 → 2915)
- Fusion unique-days uplift: +0.0%

## Section E — Recommended Step 10 Framing

→ **Step 10 = Fusion plumbing OK to build, tapi expect modest impact.**

Catatan: complementarity rendah berarti detector saling redundan. Fusion bisa build untuk completeness, tapi jangan expect 2x trade count. Pertimbangkan parallel: tambah pair coverage atau event_type baru.
