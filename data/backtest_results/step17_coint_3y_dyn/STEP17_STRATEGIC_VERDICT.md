# Step 17 — Strategic Verdict (Cointegration Thesis REJECTED)

**Tanggal:** 2026-05-11
**Window data:** 2023-04-27 → 2026-04-26 (3y, 1095 days, 12 quarters)
**Test infrastructure:** 294/294 hijau (276 baseline Phase A + 18 Phase B baru)

## Phase A — Engineering Hardening (SELESAI ✓)

| Step | Subject | Result |
|---|---|---|
| 17.A1 | Daemon restart audit | Clean idle exit Apr 27; killswitch engaged intentionally; restarted PID 6352 → 14082 |
| 17.A2 | REGIME debounce | Stability-counter ≥3 bar; suppresses Step 11's 89× classifier flicker; 4 new tests |
| 17.A3 | Slippage in PaperBroker | bps/side cost; `--slippage-bps` CLI flag plumbed via backtest_production + backtest_sweep; 5 new tests |
| 17.A4 | Sample-bias guard | OUTLIER_DRIVEN verdict when max-quarter contributes ≥60% of \|total realized PnL\|; catches Step 14B-style Q11 outlier dominance; 6 new tests |
| 17.A5 | Test suite | 261 → 276 hijau (+15 Phase A) |

**Result Phase A:** infrastructure clean, future backtest runs slippage-aware + sample-bias guarded. Zero production-code regressions.

## Phase B — Cointegration Thesis Investigation (SELESAI ✓)

### Modules added

| Module | LOC | Tests |
|---|---|---|
| `data_pipe/cointegration.py` | ~250 | 15 (B1 panel + B2 dynamic-z) |
| `scripts/backtest_cointegration.py` | ~340 | 3 (B3 simulate_pair_trades) |

### Methodology (per Step 15 disciplinary precedent)
- Rolling Engle-Granger (lookback 720 bars = 30d × 24h, refit every 24 bars = daily).
- Dynamic z-threshold (B2): p95 of \|z\| historical, bounded [1.5, 3.5].
- Pair-trade: LONG leg A + SHORT leg B × β, exit on z-revert to ±0.5 OR stop \|z\|≥4 OR time-stop = half_life × 4.
- Slippage 10 bps/side (from A3), fee 0.11%/side per PaperBroker default.
- Risk 5% equity per pair (leg A notional).
- 12 quarters walk-forward.

### Sweep results — Default (30d lookback)

| Pair | Verdict | n_trades | Realized% | Sign | EntryZ | CoIntRate |
|---|---|---|---|---|---|---|
| ETH/BTC | **INCONSISTENT** | 58 | -4.80% | 0/12 | 2.56 | 0.17 |
| SOL/ETH | **INCONSISTENT** | 51 | -4.29% | 1/11 | 2.53 | 0.16 |
| ARB/OP | **INCONSISTENT** | 73 | -2.83% | 0/12 | 2.50 | 0.24 |
| AVAX/SOL | **INCONSISTENT** | 76 | -3.24% | 0/12 | 2.47 | 0.20 |

### Sanity-check — 90d lookback (alternative construction)

| Pair | Verdict | n_trades | Realized% | Sign |
|---|---|---|---|---|
| ETH/BTC | INCONSISTENT | 61 | -2.57% | 0/7 |
| SOL/ETH | INCONSISTENT | 48 | -2.64% | 0/8 |
| ARB/OP | INCONSISTENT | 37 | -1.16% | 0/8 |
| AVAX/SOL | INCONSISTENT | 60 | -0.44% | 1/6 |

### Key findings

1. **Semua 4 pair NEGATIVE di kedua parameter set.** Realized -0.44% (best, AVAX/SOL @ 90d) sd -4.80% (worst, ETH/BTC @ 30d). Sign 0-1/(6-12) → konsisten random / mild-negative.
2. **Sample adequate** (37-76 trades per pair × 3y) — bukan undersampling artefact.
3. **CoIntRate 0.16-0.31** — pair-pair candidate ini *hanya* cointegrated 16-31% rolling windows. Bukan stable cointegrating relationships di crypto perp 3y. Bahkan ETH/BTC (paling "obvious" pair) hanya 17%.
4. **AVAX/SOL marginal** (-0.44% @ 90d lookback, 1/6 sign) — closest to break-even tapi tetap NEGATIVE; not edge.

### Verdict per spec (Step 15 precedent: STABLE_POSITIVE = ≥9/12 quarters positive)

| Pair | Verdict | Edge? |
|---|---|---|
| ETH/BTC | INCONSISTENT | ❌ |
| SOL/ETH | INCONSISTENT | ❌ |
| ARB/OP | INCONSISTENT | ❌ |
| AVAX/SOL | INCONSISTENT | ❌ |

## STRATEGIC VERDICT — Cointegration Thesis REJECTED

**Bukti:**
1. 4 pairs × 2 parameter sets = 8 backtest runs, **semua NEGATIVE**.
2. CoIntRate rendah (16-31%) menunjukkan **pair candidates tidak structurally cointegrated** di crypto perp 3y. Crypto ekosistem moves together secara directional, tapi tidak stable mean-revert antar-pair.
3. Walaupun pair temporarily cointegrated (Q-by-Q), execution signal-based pair-trade tidak menangkap edge — kemungkinan karena:
   - Slippage 10bps × 2 leg × round-trip = 40 bps overhead per trade
   - Mean-reversion lag > half-life estimate (OU fit on log-spread tidak robust di crypto)
   - Spread-broken stop-loss (\|z\|≥4) triggers too late after spread breakdown

**Pivot rekomendasi Step 18:**

| Opsi | Detail | Cost |
|---|---|---|
| **(ε1)** Order-book imbalance | New data pipeline (bid/ask snapshots); short-horizon mean-reversion thesis | 4-6 weeks data infra + R&D |
| **(ε2)** Cross-exchange spread arb | BTC perp Bybit vs Binance vs Bitmex; latency-sensitive | 2-3 weeks data + 3-4 weeks impl |
| **(ε3)** On-chain flow signals | Glassnode/whale movements as leading indicator | 6+ weeks data infra |
| **(ζ)** Pause R&D, accumulate paper data | Daemon idle 3-6 bulan, revisit dengan fresh window | passive |
| **(ζ-alt)** Pause + paper-trade existing rejected thesis | Verify rejection via live paper-trading 1-2 quarters (data we now have to confirm theoretical NEGATIVE = practical NEGATIVE) | 1-2 quarters passive |

**Honest recommendation per Step 16 precedent:** Worm sudah delegasi sekali; sebelum kasih recommendation strict, sajikan options dan biarkan user pilih budget time/effort untuk Step 18.

**Cheapest signal-quality investigation sebelum pivot major:**
- Apakah crypto-pair cointegrasi memang bukan kelas thesis yang tepat (statistical), atau cuma current parameter/exit-rule belum optimal?
- Tidak murah: butuh formal sweep parameter (lookback, refit_freq, exit_z, slippage tolerance, kill threshold) yang masuk teritori p-hacking kalau tidak hati-hati.
- Cleaner: accept verdict, pivot ke kelas thesis berbeda (ε) atau pause (ζ).

## Disciplinary Compliance

- [x] Sweep parameter fixed (1 default + 1 sanity-check construction), bukan exhaustive p-hack.
- [x] Per Step 15: "No cherry-pick" — all 4 pairs reported apa adanya, AVAX/SOL marginal tidak di-cherrypick sebagai "edge".
- [x] Sample bias guard (A4) tidak fire — pairs gagal di INCONSISTENT layer, bukan OUTLIER_DRIVEN. Sample distribution genuinely spread across quarters (sparse but not concentrated).
- [x] Slippage included from start (10bps/side) — bukan friction-resistance check post-hoc.
- [x] Daemon paused only during DB-locked backtest run, then restarted clean.
- [x] 294/294 tests hijau pre & post Phase B.

## Artifacts

```
data/backtest_results/
├── step17_coint_1y_dyn/                 (1y exploratory, 4 pairs all NEG)
│   └── comparison.md + summary.json
├── step17_coint_3y_dyn/                 (3y / 30d lookback / 12Q sweep, PRIMARY)
│   ├── comparison.md + summary.json
│   └── STEP17_STRATEGIC_VERDICT.md     (this file)
└── step17_coint_3y_lookback2160/        (90d lookback sanity-check)
    └── comparison.md + summary.json
```
