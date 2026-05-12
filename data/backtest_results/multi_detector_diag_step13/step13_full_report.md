# Multi-Detector Predictive Diagnostic + Operational Cleanup — Step 13

_Generated: 2026-04-26_

Investigations:
- **13A** — REGIME direction inversion test
- **13B** — Multi-detector predictive power (FUNDING_EXTREME, FUNDING_FLIP, Z_SPIKE)
- **13C** — Operational cleanup (apply): O1 threshold update, O2 sub-hour ts cleanup

Source script: `scripts/multi_detector_diagnostic.py` (read-only).

---

## Section A — 13A REGIME Inversion Test

Source: 6,856 REGIME_CHANGE events, signed forward returns at h=1/4/24.

### Original direction (`to=trending_up`→LONG, `to=trending_down`→SHORT)

| Band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[2,3)`   | n=1731  hit=0.467  ret=+0.017% | hit=0.468  ret=+0.022% | hit=0.510  ret=+0.141% |
| `[3,5)`   | n=335   hit=0.409  ret=-0.065% | hit=0.475  ret=-0.129% | hit=0.462  ret=-0.375% |
| `[5,8)`   | n=368   hit=0.476  ret=-0.042% | hit=0.413  ret=-0.140% | hit=0.432  ret=+0.019% |
| `[8,12)`  | n=389   hit=0.496  ret=+0.022% | hit=0.478  ret=-0.124% | hit=0.433  ret=-0.236% |
| `[12,20)` | n=212   hit=0.505  ret=-0.022% | hit=0.500  ret=+0.114% | hit=0.509  ret=+0.563% |
| `[20,+)`  | n=6     hit=0.500  ret=+0.109% | hit=0.500  ret=+0.481% | hit=0.167  ret=-2.140% |

### Inverted direction (flip both)

| Band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[2,3)`   | n=1731  hit=0.518  ret=-0.017% | hit=0.523  ret=-0.022% | hit=0.489  ret=-0.141% |
| `[3,5)`   | n=335   hit=0.558  ret=+0.065% | hit=0.516  ret=+0.129% | hit=0.535  ret=+0.375% |
| `[5,8)`   | n=368   hit=0.495  ret=+0.042% | hit=0.584  ret=+0.140% | hit=0.566  ret=-0.019% |
| `[8,12)`  | n=389   hit=0.496  ret=-0.022% | hit=0.512  ret=+0.124% | hit=0.564  ret=+0.236% |
| `[12,20)` | n=212   hit=0.486  ret=+0.022% | hit=0.495  ret=-0.114% | hit=0.491  ret=-0.563% |
| `[20,+)`  | n=6     hit=0.500  ret=-0.109% | hit=0.500  ret=-0.481% | hit=0.833  ret=+2.140% |

### Verdict

| Direction | Overall hit-rate (h=1/4/24) | Top-vs-bot uplift (h=1/4/24) |
|---|---|---|
| Forward  | 0.468 / 0.466 / 0.484 | +0.037 / +0.032 / -0.000 |
| Inverted | 0.514 / 0.526 / 0.514 | -0.032 / -0.028 / +0.001 |

→ **INVERSION_NEUTRAL.** Inverted direction marginally better (overall 0.51 vs
0.47), but neither side breaches 0.55 hit-rate threshold or +0.05 uplift.
Difference between forward and inverted ≈ 5pp — symmetrically near coin-flip.

**S3 confirmed.** REGIME_CHANGE classifier output is not predictive of next-bar
direction in either sign convention. Step 12's S2 verdict was misled by
"longer runs have higher t-stat" — a self-correlated property of the classifier,
not a directional edge.

---

## Section B — 13B Multi-Detector Predictive Power

### FUNDING_EXTREME (n=88, post-12B recalibration)

_side=long-pays→SHORT, side=short-pays→LONG. Strength = |rate| in decimal._

| Band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[0.01,0.03%)` | n=31  hit=0.484  ret=+0.073% | hit=0.516  ret=-0.009% | hit=0.387  ret=-0.528% |
| `[0.03,0.05%)` | n=41  hit=0.415  ret=-0.019% | hit=0.488  ret=-0.173% | hit=0.463  ret=-0.376% |
| `[0.05,0.10%)` | n=5   hit=0.800  ret=+0.366% | hit=0.600  ret=+0.768% | hit=0.600  ret=+3.054% |
| `[0.10,0.30%)` | n=7   hit=0.429  ret=-1.086% | hit=0.000  ret=-2.119% | hit=0.286  ret=-2.702% |
| `[0.30%+)`     | n=4   hit=0.000  ret=-2.259% | hit=0.750  ret=+2.499% | hit=0.000  ret=-2.869% |

- Overall hit-rate: h=1→0.443 / h=4→0.477 / h=24→0.409
- Top-vs-bot uplift: h=1→-0.069 / h=4→-0.028 / h=24→+0.076
- Script verdict: **HAS_EDGE** (h=24 uplift +0.076 > +0.05 threshold)

**Honest read:** marginal. Two large bands [0.01-0.05%) cover 72/88 signals
with hit-rate **below 0.50** at all horizons. Uplift +0.076 at h=24 driven by
n=5 mini-band [0.05-0.10%). Bands [0.10%+) (n=7, n=4) show large negative
returns. **No reliable edge** at the dominant signal mass; potential small-band
artefact in higher tail.

### FUNDING_FLIP (n=41)

_curr>0→LONG (bullish flip), curr<0→SHORT (bearish flip). Strength = |curr|._

| Band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[0.01,0.03%)` | n=28  hit=0.714  ret=+0.196% | hit=0.679  ret=+0.438% | hit=0.607  ret=+1.085% |
| `[0.03,0.05%)` | n=7   hit=0.429  ret=+0.154% | hit=0.857  ret=+0.501% | hit=0.429  ret=-0.563% |
| `[0.10,0.30%)` | n=2   hit=1.000  ret=+3.585% | hit=1.000  ret=+1.055% | hit=1.000  ret=+6.577% |
| `[0.30%+)`     | n=4   hit=1.000  ret=+2.259% | hit=0.250  ret=-2.499% | hit=1.000  ret=+2.869% |

- Overall hit-rate: h=1→**0.707** / h=4→**0.683** / h=24→**0.634**
- Top-vs-bot uplift: insufficient bands with n≥10 to compute (only `[0.01,0.03%)` qualifies)
- Script verdict: **HAS_EDGE** (overall hit-rate > 0.55)

**Honest read:** **looks promising.** Dominant band `[0.01,0.03%)` n=28 has
hit-rate 0.71 / 0.68 / 0.61 with positive mean returns +0.20% / +0.44% / +1.09%.
At n=28 a hit-rate of 0.71 has 95% CI roughly ±0.17 (so 0.54–0.88). Lower bound
~0.54 still indicates likely real edge.

**Caveat:** total n=41 = 1 signal per ~9 days. Sparse. Concentration di band
rendah (≤0.05%) berarti event yang ter-detect adalah "borderline flip", bukan
extreme. Higher bands (n=2, n=4) terlalu kecil untuk valid CI.

### Z_SPIKE (n=374)

_side=short-spread→SHORT A, side=long-spread→LONG A. Strength = |z|.
Predictive on first-symbol price._

| Band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[2.0,2.5)` | n=149  hit=0.430  ret=+0.059% | hit=0.470  ret=+0.003% | hit=0.443  ret=-0.136% |
| `[2.5,3.0)` | n=34   hit=0.559  ret=+0.238% | hit=0.471  ret=+0.074% | hit=0.382  ret=+0.010% |
| `[3.0,4.0)` | n=8    hit=0.625  ret=+0.177% | hit=0.625  ret=+0.237% | hit=0.750  ret=+1.264% |
| `[4.0,6.0)` | n=3    hit=0.333  ret=-1.036% | hit=0.667  ret=-0.248% | hit=0.667  ret=-1.253% |

- Overall hit-rate: h=1→0.459 / h=4→0.479 / h=24→0.448
- Top-vs-bot uplift: h=1→+0.129 / h=4→+0.001 / h=24→-0.061
- Script verdict: **HAS_EDGE** (h=1 uplift +0.129 > +0.05 threshold)

**Honest read:** marginal. Dominant band `[2.0,2.5)` n=149 has hit-rate
**below 0.50** at all horizons (mean return slightly positive at h=1).
Higher band `[2.5,3.0)` n=34 mixed (0.56 at h=1 but 0.38 at h=24). Small
bands n=8, n=3 too noisy for CI. **Predictive edge marginal at best.**

### Summary table

| Detector | Verdict | Best uplift | Best hit-rate | Honest read |
|---|---|---|---|---|
| REGIME_CHANGE (forward)  | FLAT | +0.037 | 0.484 | non-predictive |
| REGIME_CHANGE (inverted) | FLAT | +0.001 | 0.526 | non-predictive |
| FUNDING_EXTREME | HAS_EDGE* | +0.076 | 0.477 | **MARGINAL** — driven by n=5 mini-band |
| FUNDING_FLIP    | HAS_EDGE  | n/a    | 0.707 | **PROMISING** — n=28 base hit 0.71 |
| Z_SPIKE         | HAS_EDGE* | +0.129 | 0.479 | **MARGINAL** — driven by n=8 small band |

\* HAS_EDGE label triggered by uplift threshold but dominant band hit < 0.5 → not reliable

---

## Section C — 13C Cleanup Actions Taken

### Pre-cleanup backup

Created `signal_events_pre_step13` (full snapshot, 7,374 rows).
Plus prior backups still present: `signal_events_pre_step11_backup` (153),
`signal_events_funding_extreme_pre_step12b` (11).

### O1 — FUNDING_EXTREME threshold update

File: `dashboard/signal_log.py`

```python
# Step 13C: lowered 0.001 → 0.000242 (p99 dari distribution, Step 10/12 analysis)
# untuk align forward-emit dengan post-12B replay supply (~88 events/365d).
FUNDING_EXTREME = 0.000242   # 0.0242% / 8h → ~26.5% APR (p99 historical)
```

Daemon stopped/restarted (PID 54506). cycle #1 completed in 45s, no errors,
threshold loaded successfully. Live verification of new emit harus tunggu
funding row baru (8h interval) yang qualify ≥ 0.000242 — tidak material di
window evaluation singkat. Replay sudah inject 88 historical, future forward
emits akan join organically.

### O2 — Forward sub-hour ts cleanup

Polluted forward REGIME_CHANGE rows: **18** total
- 8 dari 2026-04-19 03:29:04 (`from=?` first-cycle snapshots saat daemon launch)
- 7 dari 2026-04-22 14:42:37 (regime transitions saat daemon restart Step 4)
- 1 dari 2026-04-25 10:18 + 1 dari 2026-04-25 20:18 (manual `[TEST]` payloads dari development)

**Per-row decision:** untuk tiap polluted row, cek apakah `(symbol, floor(ts,'h'))`
sudah ter-cover oleh replay row. Hasil: **0/18 covered** → semua **UPDATE**.

**Action:** UPDATE `ts` ke bar-aligned (`floor('h')`) dan **regenerate id**
(id = `sha1(event_type|symbol|YYYYMMDDHHmm|payload)[:16]`).

**Pre-collision check:** untuk masing-masing new_id, query `SELECT COUNT FROM signal_events WHERE id=?` → semua return 0 (tidak ada collision dengan existing rows).

**Result:**
- DELETE: 0
- UPDATE: 18 (all sub-hour ts → bar-aligned, id regenerated)
- Polluted post-cleanup: **0** ✓
- Total signals: 7,374 → 7,374 (preserved)
- REGIME_CHANGE total: 6,856 → 6,856 (preserved)

```sql
-- Applied via Python with id regeneration (id depends on payload + bucket)
UPDATE signal_events
SET id = <new_id>, ts = <bar_aligned_ts>
WHERE id = <old_id>;
```

---

## Section D — STRATEGIC VERDICT

### Detector viability assessment for Step 14+

| Detector | Step 13 verdict | Sample | Recommendation |
|---|---|---|---|
| REGIME_CHANGE  | **NON-VIABLE** | n=6,856, ample | **Drop from advisor.** Both forward & inverted ~coin-flip. S3 confirmed. |
| FUNDING_EXTREME | INCONCLUSIVE   | n=88, low      | **Keep but de-emphasize.** Dominant band hit < 0.5; tail bands too thin. |
| FUNDING_FLIP   | **PROMISING**  | n=41, sparse   | **Primary candidate.** 71% hit on n=28 dominant band. |
| Z_SPIKE        | INCONCLUSIVE   | n=374, moderate | **Hold.** Dominant band hit < 0.5; pair-trade semantics need own evaluation. |

### Honest summary

- **3 of 4 detectors are NOT validated as edge sources.** Only FUNDING_FLIP shows
  base-rate evidence of directional edge, dan itu pun pada sparse sample (~1
  signal per 9 days, n=28 di dominant band).
- **REGIME_CHANGE is the largest supply (94% dari semua signals) but predictively
  worthless** in either sign direction. Continued use as primary signal source
  akan mengencerkan apapun edge yang ada di detector lain.
- **Z_SPIKE** appears predictive only di small bands (n=8, n=3); dominant band
  n=149 hit < 0.5 → bukan edge.
- **FUNDING_EXTREME** post-recalibration: distribusi event masih heavy di low
  bands dengan hit < 0.5 → recalibration menambah supply tapi tidak menambah
  predictive value.

### Conclusion

**Pivot territory.** The current 4-detector design is built atas asumsi yang
sebagian besar **falsified by the data**. Filter+sweep di REGIME (Step 13A
plan from Step 12) **akan waste cycle** karena substansi predictive tidak ada
untuk di-filter. FUNDING_FLIP layak dikejar tetapi tidak cukup untuk standalone
strategy (volume terlalu kecil).

---

## Section E — Step 14 Framing

**TIDAK lanjut ke "REGIME filter + sweep" (Step 13 plan dari Step 12).** Per
Step 13B evidence, filter takkan menyelamatkan REGIME (FLAT in both directions).
Sweep terhadap signal yang non-predictive = wasted compute.

### Two pivot options

#### Option 14A — Detector redesign (multi-week effort)

Asumsi current `dashboard/regime.py classify()` semantics rusak (memang
classifying tapi output tidak prediktif). Redesign:

1. **Replace `classify()` dengan return-based regime model** — bukan trend slope
   t-stat tapi e.g. realized vol regime (low/med/high) atau return-percentile
   regime (bull/bear/range based on rolling cumulative return). Ini punya
   prior literature support.
2. **Atau abandon regime classifier** dan ganti dengan signal sources lain:
   - Order book imbalance (kalau exchange API cukup deep)
   - Funding rate trend (slope of funding over 7d)
   - Cross-asset divergence (BTC vs alts strength)
3. **Validate dulu** dengan predictive test serupa Step 13B sebelum integrate.

#### Option 14B — Trade FUNDING_FLIP only (narrow scope)

Asumsi: FUNDING_FLIP n=28 hit 0.71 = real edge meskipun sparse.

1. Build advisor branch yang HANYA respond ke FUNDING_FLIP (drop REGIME, hold
   EXTREME, hold Z_SPIKE).
2. Trade frequency akan rendah (~1/9 days = ~40 trades/year). Sizing aggressive
   (e.g., 30% notional per trade) untuk meaningful PnL.
3. Backtest dgn `backtest_production.py` constraint: FUNDING_FLIP-only signals,
   evaluate sharpe + max DD selama 365d.
4. **Kill criterion:** kalau live backtest sharpe < 0.5 atau hit drop ke <0.55,
   abandon and revisit Option 14A.

#### Recommendation: **14B first** (cheaper to validate)

- 14B = 1-2 day effort (advisor branch + backtest)
- 14A = multi-week (research + implement + validate)
- 14B akan tell us apakah ada SUPPLY edge sama sekali; kalau gagal, 14A jadi
  necessary. Kalau berhasil, sah jadi production.

### Anti-recommendations

- ❌ **JANGAN lanjut filter REGIME + sweep.** Step 13B menunjukkan signal-nya
  sama saja FLAT setelah filter.
- ❌ **JANGAN bangun fusion plumbing.** Fuse 4 detectors yang 3 tidak prediktif
  = noise amplification, bukan edge.
- ❌ **JANGAN recalibrate threshold lain (Z_SPIKE, FUNDING_FLIP)** sebelum
  validate base-rate edge. Adding more signal di non-predictive detector tidak
  membantu.

---

## Section F — Anomalies / Notes

1. **REGIME inverted overall hit-rate ~0.514 vs forward ~0.470.** Hampir mirror
   image (sum = 0.984, dekat 1.0). Ekspektasi sempurna inverted = 1.0 - forward;
   actual deviasi karena `to=ranging|chaos` signals di-exclude (no direction).
   Konsistensi ini konfirmasi sign-flip arithmetic sound.

2. **Z_SPIKE pair distribution:** 355/374 signals dari `AVAX/USDT|SOL/USDT`,
   18/374 dari `SOL/USDT|ETH/USDT`. Pair `ETH|BTC` dan `ARB|OP` zero
   (engle_granger fail di 365d window). Predictive test dominasi by AVAX|SOL
   spread behavior — generalization ke other pairs unverified.

3. **FUNDING_FLIP dominance di band [0.01,0.03%)**: 28/41 = 68% signals
   tertumpuk di lowest bin. Distribution sparseness mengurangi statistical
   power untuk uplift analysis. Kalau adoptable, FUNDING_FLIP harus di-trade
   tanpa strength filter (tail bands terlalu thin untuk membedakan).

4. **2 dari 18 sub-hour cleanup rows berisi `[TEST]` payload** — manual
   testing residue dari development saat regime classifier tweaks. Harmless,
   tetapi note untuk Step 14: pre-production audit harus DELETE manual test
   rows secara terpisah (mereka pollutant analytics).

5. **Daemon belum emit FUNDING_EXTREME baru sejak threshold update** karena
   `+0 funding` dalam cycle yang dipantau. Funding row ingest 8h-cadence;
   verifikasi struktural hanya bisa setelah next funding tick. Code change
   sudah valid (constant loaded saat module import), tinggal tunggu data.

6. **Predictive test punya look-ahead bias minimal:** semua signal `ts` di-floor
   ke bar boundary, return diukur dari bar close ke close. Tidak ada peek pada
   intra-bar data. Direct comparable ke realistik trade entry pada bar close.

---

## Acceptance Checklist

- [x] 13A verdict: **INVERSION_NEUTRAL** (S3 confirmed, neither direction has edge)
- [x] 13B verdict per detector with evidence tables
- [x] FUNDING_EXTREME constant updated `dashboard/signal_log.py` 0.001 → 0.000242
- [x] Daemon restarted (PID 54506), cycle ran without error
- [x] Sub-hour cleanup applied (18 UPDATE, 0 DELETE — semua bar-aligned, id regenerated)
- [x] Backup table `signal_events_pre_step13` created (7,374 rows)
- [x] **254/254 tests hijau**
- [x] No changes to `auto_advisor.py`, `executor.py`, `paper_broker.py`
- [x] Section D honestly proposes pivot, NOT auto-recommends "filter + sweep"
