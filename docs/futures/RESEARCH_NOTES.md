# Futures Trading System — Research Notes

**Tanggal:** 2026-05-25 · **Fase 1 (Riset)** · Penulis: Claude (delegasi penuh dari Worm)

> Dokumen ini = hasil sapu literatur + repo untuk nentuin edge & arsitektur sistem
> Binance USDT-M Futures. Prinsip inti: **riset → validasi keras (CPCV) → baru live.**

---

## 0. Misi & Constraint

- **Tujuan:** sistem trading Binance USDT-M Futures yang **market-neutral**, autonomous, profitable, risk-controlled.
- **Capital:** $50–200 (eksperimen, loss-tolerant tapi disiplin). Akun: Binance spot + USDT-M futures (key `P5vF`: Reading+Spot+Futures ON, Withdrawals OFF).
- **Reuse:** codebase `~/trading_agent` (~9k LOC) — extend, jangan rewrite.
- **Pelajaran saga spot:** 5 thesis class REJECTED, edge tipis. **Jangan bangun infra banyak sebelum edge terbukti.** Urutan dibalik: thesis & validasi dulu.

---

## 1. Kandidat Edge (ranked)

### 🥇 #1 — Funding-Rate Carry (delta-neutral cash-and-carry) — **LEAD**

**Konsep:** long spot + short perp notional sama → delta ≈ 0 (harga ke-hedge). Income = funding yang dibayar long ke short saat funding **positif**.

- **Bukti kuantitatif (paling kuat):** strategi statis (entry funding >0.01%, exit <0.005%) → **~18%/thn, Sharpe 1.4** (2019–2023). Versi ML-predict funding 4h ke depan + sizing by confidence → **31%/thn, Sharpe 2.3**.
- **Kenapa LEAD:**
  1. Bukti kuantitatif terkuat dari semua kandidat.
  2. Genuinely **market-neutral** → directional risk minim.
  3. **BEDA dari `FUNDING_FLIP` yang kita REJECT** — itu *directional* (tebak arah harga dari funding); ini *harvest* funding sambil hedged. **Belum pernah kita tes.**
  4. Cocok persis sama akun kita (spot + USDT-M, satu exchange = "cash and carry").
- **Tailwind struktural:** funding **positif mayoritas waktu** (demand leverage-long persisten).
- **Risiko:** funding bisa **FLIP negatif** (jadi cost) — *"renting a yield that can become a cost."* Plus basis risk, eksekusi 2-leg, exchange risk.
- **Catatan capital kecil:** 18%/thn di $100 = ~$18/thn — modest. Fee bisa dominan kalau turnover tinggi → desain **low-turnover** (hold lewat beberapa funding interval). Short leg **well-margined (~1x efektif)** biar gak keliquidasi sebelum hedge kebayar.

### 🥈 #2 — Market-Neutral Pairs (cointegration long/short) — **SECONDARY**

**Konsep:** long coin A, short coin B yang ko-integrasi → cuan dari konvergensi spread, beta ke-hedge. Futures bikin short beneran mungkin (mustahil di spot).

- **Metodologi:** cointegration test Engle-Granger / KSS / Johansen; OU process → **half-life = ln(2)/θ**.
- **Threshold actionable:** half-life **<30 hari = tradeable**; **>90 hari = alpha abis ketelan biaya** sebelum balik modal.
- **Bukti:** akademik bilang bisa beat buy-and-hold + max DD rendah — **TAPI** "mixed success" di praktik (kompetisi, eksekusi), dan *"a class of coins better than others"* = inkonsisten. Tes spot kita sendiri tipis (PBO 10.5%, mostly MARGINAL).
- **Confidence: RENDAH.** Cuma lanjut kalau lolos CPCV.

---

## 2. Mekanik Funding Binance (untuk implementasi)

- **Formula:** `F = PremiumIndex(P) + clamp(interest − P, −0.05%, +0.05%)`
- **Interest rate** default **0.01% / interval** (0.03%/hari).
- **Settlement tiap 8 jam** (mayoritas kontrak; sebagian 4h). Premium = time-weighted avg sampel tiap 5 detik.
- **Cap:** ±0.75% / 8h (umumnya).
- **Implikasi kode:** ingest funding real-time + historis (udah ada `ingest_funding.py` + `backfill_binance_funding.py`). Model carry: `Σ funding − fees − basis_drift`.

---

## 3. Position Sizing — Fractional Kelly

- Kelly: `f* = (b·p − q) / b` (b = reward/risk, p = win prob).
- **Full Kelly = drawdown 50–80%** (ganas). **Half Kelly** = ~75% growth, ~½ DD. **Quarter Kelly** = ~50% growth, DD manageable bahkan saat black swan.
- **Keputusan:** **Quarter Kelly** (fractional, ber-cap), DI BAWAH hard cap:
  - **2x** untuk directional/pairs leg.
  - **~1x well-margined** untuk funding-carry short leg (anti-likuidasi).
- Perp: masukin funding ke edge (terima funding = +edge, bayar = −edge).

---

## 4. Protokol Anti-Overfitting (validation GATE — wajib)

Literatur crypto tegas: return non-normal + multi-testing masif → backtest naif = nipu diri.

- **Deflated Sharpe Ratio (DSR)** — koreksi selection bias multi-test + non-normality (Bailey & López de Prado).
- **PBO (Probability of Backtest Overfitting)** + **CPCV** — **udah ada di `cpcv.py`** kita. ✓
- **GATE:** thesis lolos HANYA kalau DSR signifikan + PBO < ~30% + walk-forward konsisten. Gagal → cari thesis lain, **jangan paksa live**.

---

## 5. Tooling & Repo Referensi (untuk pola, BUKAN rewrite)

- **Framework:** NautilusTrader (Rust+Python, event-driven, backtest=live no-code-change), Freqtrade, Hummingbot (MM/arb, 35+ exch), Jesse (futures), Backtrader. Index: `merovinh/best-of-algorithmic-trading`.
- **Repo funding-arb (mining pola + edge cases):**
  - `50shadesofgwei/funding-rate-arbitrage` — template delta-neutral (BinanceCaller class).
  - `kir1l/Funding-Arbitrage-Screener` — screener multi-exch (Binance/Bybit/OKX/MEXC).
  - `aoki-h-jp/funding-rate-arbitrage` — deteksi divergence funding antar-CEX.
  - `bradleyboyuyang/Binance-Arbitrage` — cash-and-carry bot.
- **Keputusan:** extend codebase kita (`brokers`/`risk_guard`/`cpcv`/`storage`). Repo di atas = referensi arsitektur.

---

## 6. Keputusan & Next

1. **Bangun fondasi SHARED** (layani dua thesis): futures broker (ccxt USDT-M), funding ingest, `risk_guard` sadar-margin/likuidasi, backtest funding-aware.
2. **Validasi funding-carry DULU** (lead), pairs kedua — lewat gate CPCV/DSR/PBO.
3. **Sizing:** Quarter Kelly + cap 2x (pairs) / ~1x well-margined (carry).
4. **Live HANYA lewat gate.** Fund wallet futures cuma kalau thesis lolos backtest.

→ **Lanjut Fase 2: `ARCHITECTURE.md`**

---

## Sumber

**Funding / delta-neutral:**
- [Risk & Return of Funding Rate Arbitrage CEX/DEX (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S2096720925000818)
- [Ultimate Guide to Funding Rate Arbitrage (Amberdata)](https://blog.amberdata.io/the-ultimate-guide-to-funding-rate-arbitrage-amberdata)
- [Delta Neutral Strategy (Cube)](https://www.cube.exchange/what-is/delta-neutral-strategy)

**Pairs / cointegration:**
- [Dynamic Cointegration-Based Pairs Trading in Crypto (arXiv 2109.10662)](https://arxiv.org/abs/2109.10662)
- [Pairs Trading in Crypto — Empirical (EUR thesis)](https://thesis.eur.nl/pub/67552/Thesis-Pairs-trading-.pdf)
- [Half-life of Mean-Reversion (arbitragelab / Hudson & Thames)](https://hudson-and-thames-arbitragelab.readthedocs-hosted.com/en/latest/cointegration_approach/half_life.html)

**Mekanik funding Binance:**
- [Introduction to Binance Futures Funding Rates](https://www.binance.com/en/support/faq/detail/360033525031)

**Sizing:**
- [Kelly Criterion for Crypto Position Sizing (Altrady)](https://www.altrady.com/blog/risk-management/kelly-criterion-crypto-position-sizing)

**Anti-overfitting:**
- [The Deflated Sharpe Ratio (Bailey & López de Prado, SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- [The Probability of Backtest Overfitting (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)

**Tooling / repo:**
- [best-of-algorithmic-trading](https://github.com/merovinh/best-of-algorithmic-trading) · [NautilusTrader](https://nautilustrader.io/)
- [50shadesofgwei/funding-rate-arbitrage](https://github.com/50shadesofgwei/funding-rate-arbitrage) · [kir1l/Funding-Arbitrage-Screener](https://github.com/kir1l/Funding-Arbitrage-Screener) · [aoki-h-jp/funding-rate-arbitrage](https://github.com/aoki-h-jp/funding-rate-arbitrage) · [bradleyboyuyang/Binance-Arbitrage](https://github.com/bradleyboyuyang/Binance-Arbitrage)
