# Step 18 — Strategic Verdict (Trend-Follow Thesis REJECTED)

**Tanggal:** 2026-05-11
**Window data:** 2023-04-27 → 2026-04-26 (3y / 12 quarters) + 1y subset 2025-04-27 → 2026-04-26
**Test infrastructure:** 310/310 hijau (294 baseline + 16 new Step 18)

## Modules added

| File | Purpose | LOC |
|---|---|---|
| `data_pipe/trend_follow.py` | Bollinger + ATR panel + breakout detector | ~190 |
| `scripts/backtest_trend_follow.py` | Single-leg trend-follow backtest engine | ~370 |
| `tests/test_trend_follow.py` | 9 unit tests (panel + emit + filters) | |
| `tests/test_backtest_trend_follow.py` | 5 smoke tests (simulate_trend_trades) | |

## Classifier bug fix

Sweep mengungkap bug latent di `backtest_sweep.classify()` — XRP run #1 keliru STABLE_POSITIVE (n_pos=3 vs n_neg=9, realized -9.21%). Fix: require majority (`n_pos > n_neg`). 2 new tests. Affects ALL future backtests — Step 17 results unchanged karena cointegration semua INCONSISTENT (sparse quarters), tidak hit STABLE branch.

## Sweep results

### Run 1 — Default (BB20 / stop 2×ATR / trail 3×ATR / max-hold 168h / 5% sizing / 10bps slip)

| Symbol | Verdict | n_trades | Realized% | Sign | Win% |
|---|---|---|---|---|---|
| BTC | STABLE_NEGATIVE | 861 | -15.32% | 1/12 | 26.1% |
| ETH | STABLE_NEGATIVE | 941 | -15.59% | 1/12 | 26.5% |
| SOL | STABLE_NEGATIVE | 934 | -15.99% | 1/12 | 31.5% |
| BNB | STABLE_NEGATIVE | 902 | -19.15% | 1/12 | 25.4% |
| XRP | STABLE_NEGATIVE | 906 | -9.21% | 3/12 | 28.1% |
| AVAX | STABLE_NEGATIVE | 927 | -12.79% | 1/12 | 32.7% |
| ARB | STABLE_NEGATIVE | 919 | -16.63% | 2/12 | 29.6% |
| OP | STABLE_NEGATIVE | 951 | -16.98% | 2/12 | 29.5% |

**All 8 STABLE_NEGATIVE.** SMA-cross exit dominates 60-69% of trades.

### Run 2 — Wide (BB50 / stop 4×ATR / trail 6×ATR / max-hold 720h)

| Symbol | Verdict | n_trades | Realized% | Sign | Win% |
|---|---|---|---|---|---|
| BTC | STABLE_NEGATIVE | 396 | -5.67% | 2/12 | 28.3% |
| ETH | STABLE_NEGATIVE | 415 | -6.44% | 4/12 | 27.7% |
| SOL | STABLE_NEGATIVE | 412 | -4.91% | 4/12 | 32.0% |
| BNB | STABLE_NEGATIVE | 403 | -7.69% | 4/12 | 28.3% |
| XRP | STABLE_NEGATIVE | 405 | -1.15% | 2/12 | 27.4% |
| AVAX | STABLE_NEGATIVE | 404 | -2.18% | 3/12 | 32.7% |
| ARB | STABLE_NEGATIVE | 413 | -5.69% | 5/12 | 33.7% |
| OP | STABLE_NEGATIVE | 419 | -5.27% | 5/12 | 32.9% |

**Wider stops reduce loss magnitude tapi tidak flip sign.** ARB/OP closest to majority (5/12) but still NEG.

### Run 3 — 1y bull subset (sanity-check window-specific behaviour)

| Symbol | Verdict | n_trades | Realized% | Sign |
|---|---|---|---|---|
| BTC | STABLE_NEGATIVE | 134 | -3.40% | 1/4 |
| ETH | STABLE_NEGATIVE | 144 | -2.40% | 1/4 |
| SOL | STABLE_NEGATIVE | 143 | -3.13% | 1/4 |
| BNB | STABLE_NEGATIVE | 138 | -3.06% | 0/4 |
| XRP | INCONSISTENT | 130 | -1.23% | 2/4 |
| AVAX | OUTLIER_DRIVEN | 138 | -2.62% | 1/4 |
| **ARB** | **STABLE_POSITIVE** | 135 | **+0.82%** | 3/4 |
| **OP** | **STABLE_POSITIVE** | 134 | **+1.78%** | 3/4 |

**ARB/OP STABLE_POSITIVE marginal di 1y BUT** 3y same params -5.69%/-5.27%. Per Step 14B precedent (V2 "+2.66% 1y" → -6.33% 3y), **1y POSITIVE pada subset = sample artefact**, bukan genuine edge.

A4 OUTLIER_DRIVEN guard fired benar pada AVAX 1y — guard working as designed.

## STRATEGIC VERDICT — Trend-Follow Thesis REJECTED

**Bukti:**
1. 3y / 12Q default + wide param: 16 backtests, all STABLE_NEGATIVE
2. Win rate stuck 25-33% — typical trend-follow tail-distribution, tapi wins tidak cukup besar untuk offset losses + friction
3. SMA-cross exit kill 60-80% trades sebelum trend develop — fundamental design issue
4. 1y bull-subset ARB/OP marginal +1-2% TAPI same params 3y → -5%; window-dependent, bukan edge

**3 thesis class sekarang REJECTED:**
- FUNDING_FLIP (Step 15) — funding-rate mean reversion
- COINTEGRATION (Step 17) — pair-spread mean reversion
- TREND_FOLLOW BB+ATR (Step 18) — directional momentum

## REAL-ACCESS BARRIER REACHED

**User directive Step 18+ "mentokin sampai step yang butuh akses real":**

Current data stack: OHLCV + funding rate (Bybit public). Tested thesis classes berdasarkan apa yang bisa dihitung dari data ini. Untuk explore beyond, butuh data tambahan:

| Thesis class | Data needed | Access type |
|---|---|---|
| Order-book imbalance | Bid/ask snapshots, level-2/level-3 depth | **Real-time WebSocket feed** (no API key needed, tapi infrastructure baru) |
| Cross-exchange arb | OHLCV/orderbook dari Binance/OKX/Bitmex | **Public API per exchange** (multi-source) |
| On-chain flow | Glassnode / Nansen / Dune flow data | **PAID API key** ($200+/month) |
| News/social sentiment | Twitter feed + crypto news API | **PAID API key** atau scraping infrastructure |
| Whale-tracking | On-chain wallet labels + flow | **PAID API key** (Arkham, Nansen) |
| Real-money paper trade | (no extra data) actual exchange execution | **Exchange API key + REAL FUNDING** |

**Honest barrier categorisasi:**
- **Soft barrier** (infrastructure, no $$$): Order-book WebSocket + cross-exchange OHLCV aggregation. Bisa dieksplorasi dengan public APIs.
- **Hard barrier** ($$$): On-chain data, news/sentiment, whale-tracking — semua butuh paid subscriptions.
- **Real-money barrier**: Exchange API key + capital. **Tidak responsible deploy** tanpa edge confirmed di backtest.

## Step 19 Options — Strategic Pivot Required

**(A1) Cross-exchange data aggregation** — soft barrier. Add Binance/OKX OHLCV ingest layer. Open ε2 thesis (cross-exchange spread arb). ~2-3 weeks data infra.

**(A2) Order-book WebSocket** — soft barrier. Bybit WebSocket public, deeper signal granularity. Open new thesis classes (microstructure, depth imbalance). ~3-4 weeks.

**(B) Ensemble + portfolio methods** — no new data. Combine ALL existing signals (REGIME, FUNDING_FLIP, FUNDING_EXTREME, Z_SPIKE, TREND_BREAKOUT, COINTEGRATION_SPREAD) as weighted ensemble. Each individually rejected, but combinations might net positive (diversification). 1-2 weeks.

**(C) Operational hardening + accumulate** — no new data, no new thesis. Continue daemon paper-trading forward. Improve monitoring (live drawdown alerts, signal-quality dashboards, walk-forward auto-rerun). Accumulate 3-6 months fresh data, re-test rejected theses on out-of-sample. Capital-efficient passive.

**(D) Hand off ke Worm sekarang** — accept "no edge found in current data stack". User decides whether to:
  - Subscribe to paid data feeds (hard barrier)
  - Get exchange API key + fund account untuk live-paper validate rejected theses (real-money barrier — but paper trading via API tetap zero risk, just authenticated)
  - Pause project sambil ekosistem crypto matang

## Disciplinary Compliance

- [x] 2 parameter sets primary + 1 window subset (not exhaustive p-hack)
- [x] 1y bull POSITIVE (ARB/OP) NOT cherry-picked — flagged as window-artefact per Step 14B precedent
- [x] Classifier bug surfaced + fixed + 2 regression tests added
- [x] OUTLIER_DRIVEN guard (Step 17.A4) fired correctly on AVAX 1y — guard working
- [x] No production wire mutation — standalone backtest discipline maintained
- [x] 310/310 tests hijau pre & post Step 18

## Test/Code Status
- 310/310 tests hijau (+16 dari baseline 294)
- Daemon stopped during sweep (DB exclusive); restart pending decision Step 19
- Killswitch tetap engaged
