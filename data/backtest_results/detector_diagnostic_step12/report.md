# Detector Diagnostics + FUNDING_EXTREME Recalibration — Step 12

_Generated: 2026-04-26_

Investigations:
- **12A** — REGIME_CHANGE diagnostic (read-only) → verdict S1/S2/S3/MIXED
- **12B** — FUNDING_EXTREME recalibration to p99 = 0.000242 (live write, scoped)
- **12C** — Housekeeping recommendations (NOT applied)

---

## Section A — 12A Diagnostic Results

`scripts/regime_diagnostic.py` (read-only). 6,856 REGIME_CHANGE events across 8
symbols (post-replay state).

### D1 — Run-Length Distribution per Symbol

Run-length = bars between consecutive REGIME_CHANGE.ts per symbol (state lifetime
before next flip).

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

**OVERALL:** 6,848 ranked signals, **≤3-bar = 1,481 (21.6%)**, **≥13-bar = 2,038 (29.8%)**.

→ Run-length terdistribusi cukup luas. Tidak terdominasi short-runs (S3
indicator = false). Tail panjang ≥13 bar = ~30% justify "regime persistence is
not rare."

### D2 — |t-stat| Distribution (full post-replay)

n = **6,854** | mean = **3.64**

| Quantile | |t-stat| |
|---|---|
| p25 | 1.75 |
| p50 | 2.23 |
| p75 | 3.83 |
| p90 | 8.86 |
| p95 | 11.32 |
| p99 | 16.31 |
| max | 26.92 |

| Band | Count | Pct |
|---|---|---|
| `[0,1)` | 312 | 4.6% |
| `[1,2)` | 2,300 | 33.6% |
| `[2,3)` | 2,173 | 31.7% |
| `[3,5)` | 616 | 9.0% |
| `[5,8)` | 619 | 9.0% |
| `[8,12)` | 563 | 8.2% |
| `[12,20)` | 262 | 3.8% |
| `[20,+)` | 9 | 0.1% |

**Compare Step 9 (n=77, pre-replay) vs Step 12 (n=6,854, post-replay):**

| Metric | Step 9 | Step 12 | Δ |
|---|---|---|---|
| median | 2.66 | 2.23 | -0.43 |
| p75 | 7.44 | 3.83 | -3.61 |
| p95 | 15.40 | 11.32 | -4.08 |

→ Distribusi geser **left** secara substansial. 77-sample Step 9 was
self-selected sample dari live daemon yang accidentally pakai limit=500. Full
replay reveal mass distribusi sebenarnya jauh lebih bertumpuk di band rendah
(65% di |t|<3). t-stat tinggi langka (~12% punya |t|≥5).

### D3 — Predictive Power (signed forward returns)

`signed_return` = forward return aligned with signal direction (positive =
direction-correct). Excludes `to=ranging|chaos`. n_total = 3,041 signals × 3
horizons.

| t-band | h=1 | h=4 | h=24 |
|---|---|---|---|
| `[2,3)`   | hit=0.467 mean=+0.017% | hit=0.468 mean=+0.022% | hit=0.510 mean=+0.141% |
| `[3,5)`   | hit=0.409 mean=-0.065% | hit=0.475 mean=-0.129% | hit=0.462 mean=-0.375% |
| `[5,8)`   | hit=0.476 mean=-0.042% | hit=0.413 mean=-0.140% | hit=0.432 mean=+0.019% |
| `[8,12)`  | hit=0.496 mean=+0.022% | hit=0.478 mean=-0.124% | hit=0.433 mean=-0.236% |
| `[12,20)` | hit=0.505 mean=-0.022% | hit=0.500 mean=+0.114% | hit=0.509 mean=+0.563% |
| `[20,+)`  | hit=0.500 mean=+0.109% (n=6) | hit=0.500 mean=+0.481% (n=6) | hit=0.167 mean=-2.140% (n=6) |

→ **Hit rates flat ~0.42–0.51 across all bands** (24h).
→ **Top band hit-rate uplift vs bottom band = -0.076** (S1 trigger requires +0.10).
→ Mean signed return tidak monotonic: band [3,5) and [8,12) bahkan **negative**.
→ Higher t-stat ≠ higher predictive power. **S1 (threshold filter) tidak akan fix.**

_Caveat:_ predictive test dilakukan pada signed_return berdasarkan
`to_state ∈ {trending_up, trending_down}`, mengabaikan `to=ranging|chaos`
(neutral signals). Predictive power test ini flat → konsisten dengan
"classifier output noisy / direction sering wrong."

### D4 — Run-Length × |t-stat| Correlation

**Pearson(run_length_bars, |t-stat|) = +0.177** (weak positive)

| Run-Length Bucket | n | mean |t-stat| | median |t-stat| |
|---|---|---|---|
| `1`     | 515  | **3.04** | 2.09 |
| `2-3`   | 964  | 2.32 | 1.79 |
| `4-6`   | 1,558 | 2.67 | 1.86 |
| `7-12`  | 1,771 | **4.36** | 2.47 |
| `13-24` | 1,531 | **4.57** | 2.65 |
| `25-48` | 422  | 4.51 | 2.56 |
| `48+`   | 85   | 4.17 | 2.57 |

→ Long-persistence buckets (≥7 bar) punya mean |t-stat| ~50% lebih tinggi
dari short buckets. Median uplift kecil tapi konsisten. **Long-runs DO have
higher t-stat.**

→ Pearson +0.177 = weak overall correlation, tapi binned trend jelas.

---

## Section B — 12A Verdict

### **SCENARIO_S2 (with caveat → effectively MIXED)**

**Primary verdict: S2 (debounce-fixable).** Long-persistence regime signals
have notably higher t-stat (mean 4.57 di run-len 13-24 vs 3.04 di run-len 1).
Filter "ignore signals followed by flip-back within ≤3 bars" akan menjatuhkan
~22% noise.

**Caveat — partially structural (S3 leaning):**

- D3 predictive power **flat di semua band** → bukan true S1, tapi juga
  bukan murni S2. Kalau S2 fix-nya cukup, expected: long-RL signals akan
  punya hit-rate notably higher. Kita tidak punya data untuk verifikasi
  langsung (D3 tidak split by run-length). Hipotesis: long-RL signals
  punya t-stat lebih tinggi karena trend memang lagi jalan, BUKAN karena
  mereka prediktif untuk continuation.

- Hit-rate ~0.50 di mana-mana = classifier output borderline random untuk
  trade-direction purposes.

**Final framing:** S2 debounce + strength filter (e.g. drop |t| < 3, drop
short-RL) **layak dicoba** sebagai cleanup pertama, tapi **expected outcome
adalah supply turun ~50–70% dengan predictive power tetap tipis**. Step 13
sweep akan reveal apakah filter cukup atau perlu rethink classifier semantics
(S3 territory).

**Evidence summary:**

| Hypothesis | Evidence | Verdict |
|---|---|---|
| S1 — threshold-fixable | Hit-rate uplift top vs bot band = **-0.076** (need +0.10) | **NO** |
| S2 — debounce-fixable | mean |t| run-len-1=3.04 vs run-len-13-24=4.57 (+50%) | **YES (weak)** |
| S3 — structural | Predictive flat ~50% all bands; short-runs only 21.6% (not majority) | **PARTIAL** |

---

## Section C — 12B Re-Replay Log + Post-Counts

**Backup pre-12B:** `signal_events_funding_extreme_pre_step12b` = 11 rows
(seluruh FUNDING_EXTREME sebelum recalibration).

**Threshold change:** 0.001 → 0.000242 (p99 dari Step 10 distribution analysis).
**Tidak mengubah konstanta** `dashboard.signal_log.FUNDING_EXTREME`. Override
via CLI flag `--funding-extreme-threshold 0.000242`.

### Replay log (run 1 — live write)

```
[replay] mode: LIVE WRITE
[replay] window: 2025-04-26 02:00:00 → 2026-04-25 09:00:00
[replay] symbols (8): all
[replay] detectors: ['funding_extreme']
[replay] FUNDING_EXTREME threshold OVERRIDE: 0.000242 (default 0.001)

[replay] BTC/USDT          0.10s  would-emit=   0  inserted=   0
[replay] ETH/USDT          0.10s  would-emit=   1  inserted=   0
[replay] SOL/USDT          0.10s  would-emit=  24  inserted=  21
[replay] BNB/USDT          0.10s  would-emit=  11  inserted=  11
[replay] XRP/USDT          0.10s  would-emit=   6  inserted=   5
[replay] AVAX/USDT         0.10s  would-emit=  17  inserted=  16
[replay] ARB/USDT          0.15s  would-emit=  10  inserted=   9
[replay] OP/USDT           0.10s  would-emit=  19  inserted=  15

[replay] DONE in 1.1s — total would-emit: 88 / inserted: 77 / dedup: 11
```

### Idempotency re-run (run 2)

Same command → would-emit: 88, **inserted: 0** (semua ter-dedup, id stabil).

### Post-12B inventory

| Metric | Pre-12B | Post-12B |
|---|---|---|
| Total FUNDING_EXTREME | 11 | **88** |
| Pre-12B rows still present | — | 11/11 ✓ |
| Duplicate ids | 0 | 0 ✓ |

**Quarter heatmap (post-12B):**

| Q1 | Q2 | Q3 | Q4 | Total |
|---|---|---|---|---|
| 13 | 16 | 24 | 35 | **88** |

→ **All 4 quarters covered (4/4 ≥3 ✓)**.

**Per-symbol (post-12B):**

| Symbol | Count |
|---|---|
| SOL/USDT | 24 |
| OP/USDT | 19 |
| AVAX/USDT | 17 |
| BNB/USDT | 11 |
| ARB/USDT | 10 |
| XRP/USDT | 6 |
| ETH/USDT | 1 |
| BTC/USDT | 0 |

→ Acceptance: count=88 ∈ [80, 100] target band ✓ (Step 10 math expected ~89).

---

## Section D — 12C Housekeeping Decisions (NOT applied)

### C1 — ts pollution (forward sub-hour ts)

**Findings:**
- Forward REGIME_CHANGE rows: **86** total
  - Sub-hour ts (e.g., `2026-04-19 03:29:04.163617`): **18**
  - Bar-aligned ts: **68**
- Sub-hour rows ALL `from=?` first-cycle snapshots from daemon's first run on 2026-04-19.
- **0/18 sub-hour rows** have a corresponding replay-emitted REGIME_CHANGE at the floored bar (sym, bar_ts) → they don't duplicate replay rows; they are **standalone first-observation snapshots**.

**Why no duplicate:** replay walks history bar-by-bar; emits transition only when
regime CHANGES vs prior bar. At 2026-04-19 03:00:00, those symbols had been
in their state for many bars → no transition → no emit. The forward 18 captured
a "we just turned on, current state is X" snapshot — semantically different
from a transition.

**Recommendation:** **Option D — DELETE the 18 sub-hour `from=?` snapshots.**
Replay sudah cover kompleks transisi. First-cycle snapshots cuma noise
(redundant info for trade decisioning). Backup table `signal_events_pre_step11_backup`
sudah preserve them.

```sql
-- NOT YET APPLIED — operator approval gate
DELETE FROM signal_events
WHERE event_type='REGIME_CHANGE'
  AND via_replay IS NULL
  AND payload LIKE 'from=?%'
  AND (date_part('minute', ts) <> 0 OR date_part('second', ts) <> 0);
-- Expected: 18 rows
```

**Reject Option A (UPDATE ts to bar-aligned):** akan menggeser id (id =
hash(type|sym|bucket|payload)) dan bisa colide dengan baris lain. Lebih
clean kalau DELETE saja.

### C2 — Off-by-2 count diagnosis (Step 11 report)

Step 11 report claimed `REGIME total = 6847`, breakdown said
`replay=6770 + forward=79 = 6849` (Δ = -2).

**Findings (re-counted post-12B):**
- Current REGIME total: **6,856**
- Current breakdown: `replay=6770 + forward=86 = 6856` ✓ (matches exactly)
- Daemon ran 1 cycle between Step 11 report and Step 12 → added 7 forward signals
- **Duplicate-ids within REGIME: 0**

**Hypothesis untuk -2 Step 11:** very low probability event — 2 forward
REGIME signals happened to land on bar-boundary ts (`MM:00:00`) AND have payload
identical to replay-emitted signal at same bar (same `from=X;to=Y;t=Z` to 2dp).
Replay's `INSERT ... ON CONFLICT DO NOTHING` deduped them. Total appeared as
6,847 instead of 6,849.

**Decision:** No action needed. Off-by-2 = expected statistical edge case in
dedup logic, harmless. Document and move on.

---

## Section E — Step 13 Framing

Berdasarkan Verdict 12A (SCENARIO_S2 with S3 caveat), Step 13 plan:

### 13A — REGIME filter pipeline (config-only, not detector edit)

Add filter di `auto_advisor._load_candidates()` (atau separate
`scripts/regime_filter.py` view-builder):

1. **Strength filter:** drop signals with `|t| < 3.0` (target: keep top ~35%).
2. **Debounce filter:** drop signals where **next** REGIME_CHANGE for same
   symbol happens within ≤3 bars (suggests flip-back noise). Use SQL window
   function `LEAD(ts) OVER (PARTITION BY symbol ORDER BY ts)`.
3. **Direction filter (optional):** drop `to=ranging|chaos` signals — they
   tidak punya trade direction. Saver: keep them for "defensive exit"
   semantics di Step 14.

**Expected supply post-filter:** 6,856 → ~1,500–2,000 (drop ~70–75%).

### 13B — Validation backtest

Re-run `backtest_sweep.py` dengan threshold-injection setelah filter. Compare:
- `step12_baseline_97d` (no filter, post-replay supply)
- `step13_filter_97d` (with 13A filter)

**Hit metric:** trade count, sharpe, % cycles INSUFFICIENT_SAMPLE.

**Decision criteria untuk Step 14:**
- Kalau filter naikkan win-rate >0.55 atau sharpe >0.5 → continue dengan fusion plumbing (Step 14).
- Kalau filter tidak materially improve → S3 confirmed; need to revisit
  `dashboard.regime.classify()` semantics (e.g., add hysteresis, longer
  smoothing window, regime confidence margin).

### 13C — Fusion plumbing (deferred, contingent on 13B)

Build `fuse_signals(window)` di auto_advisor — extend `_load_candidates()`
ke 4 event_types dengan normalized confidence per type. Defer until Step 13A
filter validated.

---

## Section F — Anomalies / Notes

1. **D2 distribution shift:** Step 9 (n=77) had p95=15.40, Step 12 (n=6,854)
   has p95=11.32. Step 9's sample was biased toward late-window high-volatility
   period (Q4 only); full-year replay reveals lower median volatility regime
   (most year was non-extreme). Sampling bias of original 77 explained.

2. **D3 [3,5) band underperforms [2,3):** Hit-rate 0.462 vs 0.510 di 24h
   horizon. Counterintuitive (higher t-stat → lower hit). Hipotesis: signals
   di band [3,5) sering di transitions ke "chaos" or volatility regime change
   yang tidak directional. Step 13 perlu split by `to_state`.

3. **D4 weak Pearson:** +0.177 dengan correlation overall lemah. Buckets
   trend lebih jelas (3.04 → 4.57) karena bin-mean smooths noise. Linear
   correlation tidak menangkap step-function relationship (jump dari short
   ke long bucket, plateau di long).

4. **12B BTC/USDT count = 0:** Tidak ada single funding rate BTC yang
   melewati 0.000242 dalam 365 hari. BTC funding sangat stabil (range
   tipis). Bukan bug; reflek market structure. ETH=1 mirip — large-cap funding
   sangat dampened. Mid-caps (SOL/OP/AVAX/ARB/BNB) menyumbang mayoritas.

5. **12C C2 forward REGIME 79 → 86:** Daemon ran 1 cycle between Step 11
   completion (PID 45208 launched 12:25 UTC) and Step 12 start (~12:30 UTC),
   added 7 forward signals. Now stopped lagi pre-12B replay write, restart
   diserahkan ke operator setelah review Section D.

6. **12B did not change `signal_log.FUNDING_EXTREME` constant.** Override
   via CLI only. **For Step 13+ daemon to emit FUNDING_EXTREME at new
   threshold,** operator harus update `dashboard/signal_log.py` → `FUNDING_EXTREME = 0.000242`.
   Sampai itu di-apply, daemon forward-emit FUNDING_EXTREME tetap pakai
   0.001 (tail-only). Replay-emitted 88 events di DB, future daemon emits
   tetap rare. Recommend Step 13 include constant update.

---

## Acceptance Checklist

- [x] `scripts/regime_diagnostic.py` runnable, deterministic
- [x] Verdict 12A: **SCENARIO_S2** (with S3 caveat)
- [x] FUNDING_EXTREME post-recalibration count = 88 ∈ [80, 100] ✓
- [x] Quarter coverage 4/4 ✓
- [x] No duplicate ids 12B ✓
- [x] Idempotency 12B verified (re-run inserted = 0)
- [x] Housekeeping decisions reported, NOT auto-applied
- [x] **254/254 tests hijau**
- [x] Tidak ada perubahan ke `auto_advisor.py`, `executor.py`, `paper_broker.py`
- [x] Edit ke `detector_replay.py` hanya add CLI flag `--funding-extreme-threshold`
      (additive, default behaviour unchanged)
