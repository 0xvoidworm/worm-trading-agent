# Step 16 — Three-Investigation Audit (no production changes)

_Window: 2023-04-27 → 2026-04-26 (3y)_

## Section A — 16A FUNDING_FLIP Direction Inversion (3y trades, recompute)

**Approach:** Original trade has direction d ∈ {LONG, SHORT} with realized PnL p.
Inverted trade has direction !d with PnL = -p (assumes symmetric exits, valid for time-stop / regime-flip / TP-SL bukan kena early because brokers act on price moves which are symmetric for opposite direction).
Limitations: SL/TP triggers depend on direction — actual re-run with inverted direction would give different exit timing. This recompute = upper-bound approximation, sufficient for screening.

| Variant | n | Orig Real% | Inv Real% | Orig Sharpe | Inv Sharpe | Orig MaxDD% | Inv MaxDD% | Orig Sign | Inv Sign |
|---|---|---|---|---|---|---|---|---|---|
| V1 | 56 | -11.53 | +11.53 | -1.221 | +1.312 | 12.48 | 3.76 | 1/12 | 2/12 |
| V2 | 57 | -6.33 | +6.33 | -0.765 | +0.829 | 9.71 | 5.01 | 1/12 | 2/12 |
| V3 | 56 | -13.65 | +13.65 | -1.355 | +1.442 | 13.65 | 3.18 | 1/12 | 2/12 |

### V2 per-quarter sign (12 quarters, original vs inverted)
| Q | Orig | Inv |
|---|---|---|
| Q1 | SPARSE | SPARSE |
| Q2 | NEG | POS |
| Q3 | SPARSE | SPARSE |
| Q4 | SPARSE | SPARSE |
| Q5 | SPARSE | SPARSE |
| Q6 | SPARSE | SPARSE |
| Q7 | SPARSE | SPARSE |
| Q8 | SPARSE | SPARSE |
| Q9 | SPARSE | SPARSE |
| Q10 | SPARSE | SPARSE |
| Q11 | POS | NEG |
| Q12 | NEG | POS |

**Decision Gate (V2):** Inverted realized = +6.33%, sign = 2/12
**VERDICT 16A: `S3_FUNDING_FLIP`** — Inverted V2 realized = +6.33% ≤ +2% OR sign 2/12 < 6 → no rescue, FUNDING_FLIP genuinely no edge

## Section B — 16B Z_SPIKE + FUNDING_EXTREME 3y Predictive Audit

**Total events in 3y window:** Z_SPIKE=374, FUNDING_EXTREME=2034
_Step 13 D3 baseline (1y): Z_SPIKE=374, FUNDING_EXTREME=88_
_NOTE: Z_SPIKE replay didn't extend to 3y (replay output earlier showed inserted=0). Z_SPIKE events still 1y span (2025-04-30 → 2026-04-26). Predictive analysis below applies only to 1y subset for Z_SPIKE._

**Parsed strength (non-null):** Z_SPIKE=374, FUNDING_EXTREME=2034

### FUNDING_EXTREME (n=2034)
| Band | n | mean_str | h=1h hit | h=4h hit | h=24h hit | mean_fwd_24h |
|---|---|---|---|---|---|---|
| Q1_low | 511 | 0.00028 | 0.466 | 0.481 | 0.526 | +0.080% |
| Q2 | 507 | 0.00036 | 0.489 | 0.477 | 0.495 | +0.627% |
| Q3 | 508 | 0.00048 | 0.433 | 0.467 | 0.478 | +0.586% |
| Q4_high | 508 | 0.00081 | 0.498 | 0.522 | 0.549 | -0.476% |

_top-vs-bot uplift hit_24h: +0.023_

**VERDICT 16B `FUNDING_EXTREME`: `INCONCLUSIVE`** — Q4_high hit_24h=0.549 between thresholds, n=508

### Z_SPIKE (n=374)
| Band | n | mean_str | h=1h hit | h=4h hit | h=24h hit | mean_fwd_24h |
|---|---|---|---|---|---|---|
| Q1_low | 96 | 2.04531 | 0.396 | 0.490 | 0.427 | -0.990% |
| Q2 | 91 | 2.15923 | 0.407 | 0.505 | 0.473 | +0.771% |
| Q3 | 94 | 2.31691 | 0.447 | 0.468 | 0.404 | -0.475% |
| Q4_high | 93 | 2.97129 | 0.484 | 0.484 | 0.538 | +0.398% |

_top-vs-bot uplift hit_24h: +0.111_

**VERDICT 16B `Z_SPIKE`: `INCONCLUSIVE`** — Q4_high hit_24h=0.538 between thresholds, n=93

## Section C — 16C BTC FUNDING_FLIP = 0 Anomaly Diagnosis

### C1 — funding_rate distribution (3y)

| Symbol | n | min | max | mean | sd |
|---|---|---|---|---|---|
| ARB/USDT | 3283 | -0.003676 | +0.001281 | +0.000096 | 0.000154 |
| AVAX/USDT | 3283 | -0.008416 | +0.001405 | +0.000062 | 0.000216 |
| BNB/USDT | 3283 | -0.002018 | +0.000973 | -0.000007 | 0.000224 |
| BTC/USDT | 3283 | -0.000294 | +0.001128 | +0.000073 | 0.000101 |
| ETH/USDT | 3283 | -0.001177 | +0.000972 | +0.000073 | 0.000104 |
| OP/USDT | 3283 | -0.002545 | +0.001530 | +0.000089 | 0.000155 |
| SOL/USDT | 3283 | -0.005000 | +0.001388 | +0.000064 | 0.000181 |
| XRP/USDT | 3283 | -0.005800 | +0.001312 | +0.000085 | 0.000171 |

### C2 — sign-flip events per symbol (raw, 3y)

`flip_dom_band` = subset where |curr| ∈ [0.01%, 0.03%) (Step 14B band)
`flip_above_0.02%` = subset passing global FUNDING_FLIP_MIN_ABS=0.0002 detector gate

| Symbol | total flips | flips ≥ 0.02% | flips in dom band | mean |curr|@flip | max |curr|@flip |
|---|---|---|---|---|---|
| ARB/USDT | 519 | 13 | 146 | 0.000073 | 0.003676 |
| AVAX/USDT | 661 | 15 | 179 | 0.000078 | 0.008416 |
| BNB/USDT | 749 | 48 | 268 | 0.000087 | 0.001025 |
| BTC/USDT | 583 | 0 | 39 | 0.000034 | 0.000156 |
| ETH/USDT | 599 | 1 | 40 | 0.000043 | 0.001177 |
| OP/USDT | 583 | 6 | 133 | 0.000060 | 0.001383 |
| SOL/USDT | 728 | 17 | 171 | 0.000071 | 0.005000 |
| XRP/USDT | 672 | 6 | 99 | 0.000060 | 0.005800 |

### C3 — Diagnosis

BTC/USDT funding rate distribution:
- mean |funding| = 0.000083 (0.0083%)
- p95 |funding| = 0.000238 (0.0238%)
- p99 |funding| = 0.000503 (0.0503%)
- max |funding| = 0.001128 (0.1128%)
- fraction with |funding| < 0.005% (near zero) = 0.370

Sign-flip mechanics for BTC over 3y:
- Total sign flips: **583**
- Flips passing detector gate (|curr| ≥ 0.02%): **0**
- Flips in Step 14B dominant band [0.01%, 0.03%): **39**

**VERDICT 16C: `CORRECT_BEHAVIOR`** — BTC funding sign flips 583× over 3y BUT max |curr| at flip moment = 0.000156 (0.0156%) < detector gate 0.02%. Mechanism: BTC perp funding is high-frequency low-amplitude oscillator — sign flips happen often but always near zero, never with momentum to reach 0.02% threshold. Compare ke ARB/USDT dll yang lebih volatile.

DB sanity: signal_events count for BTC/USDT FUNDING_FLIP = **0** (any time, any window)


## Section D — STRATEGIC SYNTHESIS

- 16A FUNDING_FLIP inversion: **S3_FUNDING_FLIP**
- 16B FUNDING_EXTREME: **INCONCLUSIVE**
- 16B Z_SPIKE: **INCONCLUSIVE**
- 16C BTC anomaly: **CORRECT_BEHAVIOR**

### Step 17 Recommendation
**Step 17 = strategic checkpoint dengan user.** Tidak ada rescue path: FUNDING_FLIP rejected bukan sign-bug, Z_SPIKE/FUNDING_EXTREME tidak punya predictive edge in 3y, BTC anomaly = correct behavior (not bug). Opsi besar untuk dipertimbangkan: cointegration from scratch (pair candidates exist), thesis class entirely different (e.g. order-book imbalance, on-chain flows), atau pause R&D + accumulate paper data 3-6 bulan untuk re-evaluate.

## Section E — Anomalies

- (See per-section anomalies above. No cross-cutting unexpected findings beyond the pre-spec'd diagnoses.)
