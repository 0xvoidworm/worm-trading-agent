# Step 15 — Strategic Verdict (Slippage Sensitivity + 3y Window Extension)

**Window:** 2023-04-27 → 2026-04-26 (1095 days, 12 quarters)
**Detector:** FUNDING_FLIP only, dominant band [0.01%, 0.03%)
**Variants:** V1 (defaults) / V2 (max_hold=24h) / V3 (regime_flip OFF)

---

## Section A — 15A Slippage Sensitivity (1y trades.csv recompute)

| Variant | bps/side | n | Realized% | Sharpe | MaxDD% | α vs BnH (-18.35%) |
|---|---|---|---|---|---|---|
| V1 | 0  | 27 | -0.98 | -0.21 | 3.58 | +17.37 |
| V1 | 5  | 27 | -1.51 | -0.34 | 3.75 | +16.84 |
| V1 | 10 | 27 | -2.05 | -0.46 | 3.92 | +16.30 |
| V1 | 20 | 27 | -3.13 | -0.70 | 4.27 | +15.22 |
| **V2** | 0  | 28 | **+2.66** | +0.76 | 2.32 | +21.01 |
| **V2** | 5  | 28 | **+2.09** | +0.60 | 2.51 | +20.44 |
| **V2** | 10 | 28 | **+1.53** | +0.45 | 2.69 | +19.88 |
| **V2** | 20 | 28 | **+0.39** | +0.13 | 3.08 | +18.74 |
| V3 | 0  | 27 | -2.91 | -0.62 | 4.70 | +15.44 |
| V3 | 5  | 27 | -3.45 | -0.73 | 4.88 | +14.90 |
| V3 | 10 | 27 | -3.98 | -0.84 | 5.05 | +14.37 |
| V3 | 20 | 27 | -5.06 | -1.06 | 5.41 | +13.29 |

**Decision Gate:** V2 @ 10bps = +1.53% > 0% → **PROCEED 15B**.
V2 stays positive even @ 20bps (+0.39%) — friction-resistant **on 1y window**.

---

## Section B — Backfill log + integrity

- Daemon idle pre-backfill (no PID active)
- Backup: `signal_events_pre_step15` table (7,375 rows)
- `scripts/backfill_historical.py --days 1095` runtime: ~120s
- Result: 8 symbols × 26,280 OHLCV bars × 3y, 8 × 3,285 funding rows
- Integrity check: `gap_h=0` for all 8 symbols (contiguous 26,279h span = 26,280 bars exactly)

---

## Section C — Inventory post-extend (FUNDING_FLIP per quarter)

`scripts/detector_replay.py --start 2023-04-27 --end 2026-04-26` runtime: 13.1 min, 22,757 events emitted, 15,891 inserted.

FUNDING_FLIP totals: **106 events** across 3y (vs 41 in 1y; 2.6× scaling).

| Quarter (calendar) | n FF events |
|---|---|
| 2023-Q2 | 5 |
| 2023-Q3 | 15 |
| 2023-Q4 | 12 |
| 2024-Q1 | 4 |
| 2024-Q2 | 8 |
| 2024-Q3 | 9 |
| 2024-Q4 | 3 |
| 2025-Q1 | 5 |
| 2025-Q2 | 9 |
| **2025-Q3** | **0** ← signal drought |
| 2025-Q4 | 25 ← spike |
| 2026-Q1 | 6 |
| 2026-Q2 (partial) | 5 |

Notable: BNB/USDT dominates (47/106 = 44%); BTC/USDT has **0** FUNDING_FLIP events across 3y in dominant band.

---

## Section D — Sweep 3 variants × 12 quarters

| Variant | Closed | Realized% | MTM% | BnH BTC% | α realized | Sharpe | MaxDD% |
|---|---|---|---|---|---|---|---|
| V1 | 56 | -11.53 | -11.53 | +168.57 | -180.10 | -1.03 | 13.08 |
| V2 | 57 | -6.33 | -6.33 | +168.57 | -174.90 | -0.69 | 10.10 |
| V3 | 56 | -13.65 | -13.65 | +168.57 | -182.22 | -1.15 | 13.96 |

Per-quarter sign distribution (identical pattern across V1/V2/V3):

| Q  | Window | V1 sign | V2 sign | V3 sign |
|---|---|---|---|---|
| Q1  | 2023-04-27 → 2023-07-27 | SPARSE | SPARSE | SPARSE |
| Q2  | 2023-07-27 → 2023-10-26 | NEG | NEG | NEG |
| Q3  | 2023-10-26 → 2024-01-25 | SPARSE | SPARSE | SPARSE |
| Q4  | 2024-01-25 → 2024-04-25 | SPARSE | SPARSE | SPARSE |
| Q5  | 2024-04-25 → 2024-07-25 | SPARSE | SPARSE | SPARSE |
| Q6  | 2024-07-25 → 2024-10-24 | SPARSE | SPARSE | SPARSE |
| Q7  | 2024-10-24 → 2025-01-23 | SPARSE | SPARSE | SPARSE |
| Q8  | 2025-01-23 → 2025-04-24 | SPARSE | SPARSE | SPARSE |
| Q9  | 2025-04-24 → 2025-07-24 | SPARSE | SPARSE | SPARSE |
| Q10 | 2025-07-24 → 2025-10-23 | SPARSE | SPARSE | SPARSE |
| Q11 | 2025-10-23 → 2026-01-22 | **POS** | **POS** | **POS** |
| Q12 | 2026-01-22 → 2026-04-26 | NEG | NEG | NEG |

---

## Section E — Aggregate verdict per variant (Step 15 spec)

Spec rule:
- STABLE_POSITIVE: ≥9/12 positif
- POSITIVE_INCONSISTENT: 6-8/12 positif
- INCONCLUSIVE: 4-5/12 positif
- **NEGATIVE: <4/12 positif**

| Variant | Quarters POSITIVE | Spec Verdict |
|---|---|---|
| V1 | 1/12 (Q11 only) | **NEGATIVE** |
| V2 | 1/12 (Q11 only) | **NEGATIVE** |
| V3 | 1/12 (Q11 only) | **NEGATIVE** |

n_closed (56, 57, 56) di bawah threshold "meaningful" 90, **tapi pola sign konsisten 1/12 di 3 variant berbeda**: bukan undersampling artefact, ini pure signal-quality artefact.

---

## Section F — STRATEGIC VERDICT

### **THESIS FUNDING_FLIP REJECTED**

Per Step 15 spec quote:
> kalau NEGATIVE: thesis FUNDING_FLIP rejected

### Bukti

1. **Step 14B "marginal V2 +2.66%" adalah pure sample artefact.** Window 365d (2025-04-27 → 2026-04-25) kebetulan menangkap Q11 (2025-10-23 → 2026-01-22 = SOL/USDT/SOL run-up era). Ketika diperluas ke 3y dengan walk-forward 12 quarters, **strategi kehilangan -6% to -14% capital sambil BTC naik +168%**.
2. **Sign pattern 1/12 identik di 3 variant** (V1/V2/V3 menggunakan exits berbeda) → bukan exit-rule problem. Edge tidak ada di entry-signal level.
3. **Slippage sensitivity 15A misleading scope.** V2 friction-resistant @ 20bps di 1y, **tapi 1y itu kebetulan 1 quarter outlier-positif (+5% Q11) overwhelming 3 quarter slightly-negative**. Friction tolerance hanya berarti kalau edge nyata; kalau edge palsu, friction tolerance irrelevant.
4. **Konsistensi negatif:** Q2 2023 (8 trades = NEG), Q12 2026 (8-9 trades = NEG). Hanya Q11 yang POS dan itu pun cuma karena 11 trades yang kebetulan menguntungkan. Selectivity rendah, hit rate rendah, edge tidak konfirmasi.

### Aksi Direkomendasikan (Step 15 spec quote, NEGATIVE branch)

> kalau NEGATIVE: thesis FUNDING_FLIP rejected

→ **Pivot ke Step 14A asli: detector redesign**, atau alternatif:

| Opsi | Detail | Risk |
|---|---|---|
| **(α)** Step 14A — redesign FUNDING_FLIP detector dari ground up | New entry conditions (mis. funding flip + price confirmation, atau funding flip + regime alignment) | Multi-week R&D; tidak ada jaminan edge muncul |
| **(β)** Drop FUNDING_FLIP entirely; eksplor REGIME_CHANGE dengan debounce (Step 12 deferred work) | REGIME inversi sudah ditest "INVERSION_NEUTRAL" Step 13A — coin-flip → low priority | Step 13 sudah refute; risk wasted effort |
| **(γ)** Drop semua existing detector, pivot ke pair-trading thesis (cointegration_candidates di pairs.yaml ada 4 candidate yet untouched) | Mean-reversion stat-arb, sudah punya infrastruktur (regime/storage/executor) | New thesis, multi-week, tapi domain-orthogonal dari funding |
| **(δ)** Stop strategy R&D, fokus operasional: integrate live exchange API, real money paper-track manual signals | Sebelum trade real money perlu testbed | Tidak directly answer "apakah ada edge" |

**Honest recommendation:** Opsi (γ) — pair trading mean-reversion. Funding-rate-driven directional thesis sudah dibukukan tidak punya edge di 3y data Bybit (8 majors). Cointegration arbitrage adalah 90° dari thesis ini, infrastruktur-existing kompatibel, dan ada candidates `[ETH/BTC, SOL/ETH, ARB/OP, AVAX/SOL]` yang belum pernah diuji statistical edge.

### Disciplinary Compliance Recheck

- [x] No p-hack: filter band fixed [0.01%, 0.03%) across V1/V2/V3 dan dari Step 14B identik
- [x] No variant config tweak setelah hasil: 3 variants spec'd di Step 14B reused
- [x] No threshold sweep p-hack: tidak ubah `regime_t_threshold`/`band_min`/`band_max` setelah lihat hasil 15B
- [x] Reported apa adanya: V1/V2/V3 semua NEGATIVE, no cherry-pick
- [x] 261/261 tests hijau pre & post sweep
- [x] Sample size n=56-57 above 30 → not INSUFFICIENT_SAMPLE excuse; verdict based on signal-quality not sample-size
