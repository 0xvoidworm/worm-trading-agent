# PROJECT SCALPING — Scope Definition (P1)

**Started:** 2026-05-27 · **Status:** P1 (data + scope)
**Decision owner:** Worm · **Build:** Claude (delegated)

## Premise & lane

Rule-based **CODE**, bukan LLM-in-loop. LLM ~1-5 dtk/keputusan → kalah telak vs HFT (μs).
Laptop + WiFi retail **gak bisa** true HFT. Lane realistis = **fast rule-based, hold 1-5 menit**
di market paling likuid. Sinyal dihitung dari 1m OHLCV, eksekusi market + immediate
exchange-side bracket (gaya BTC#1 yang RELIABLE; limit+monitor FRAGILE — lihat WIF post-mortem).

## Honest framing (baca tiap kali tergoda skip gate)

- Ekspektasi edge survive OOS = **RENDAH**. Systematic alpha retail crypto = largely
  arbitraged + decaying (5 spot thesis + carry + pairs SEMUA gagal sebelum ini).
- Scalp edge **mati di biaya** + fake-positive gampang (inget hantu +67% dari bug β-refit).
- Yang dikejar: infra reusable + skill, bukan dijamin mesin duit.
- **$0 risk capital sampai lolos backtest gate (P3).** $20 free margin tetap SIMPEN.

## Data scope (P1) — LOCKED

| Param | Value | Alasan |
|---|---|---|
| Symbols | `BTC/USDT:USDT`, `ETH/USDT:USDT` | Perp paling likuid / spread tertipis. 2 simbol → cross-check sinyal bukan noise khas-1-koin. |
| Timeframe | `1m` | Lane 1-5 menit hold. |
| Lookback | 180 hari | Multi-regime (trend/chop/high-low vol). ~260k bar/simbol. |
| Source | Binance USDT-M perp | Venue yang kita trade (ccxt `get_exchange` defaultType=`swap`). |
| Storage | `data/scalp_1m.duckdb` | **TERPISAH** dari `market.duckdb` live → zero lock contention sama 4 service spot. |
| Reuse | `ingest_ohlcv.backfill_symbol` + `storage.Storage` | Pagination + retry + rate-limit + idempotent upsert udah ada & tervalidasi. |

Extend ke 365d / tambah SOL = gampang (idempotent, resumable) kalau engine promising.

## Cost hurdle (INI yang bunuh kebanyakan sinyal)

Diverifikasi dari BTC#1: fee ~$0.06 round-trip @ ~$76 notional ≈ **8bp taker round-trip**
(0.04%/side, match standar Binance USDT-M).

```
Biaya round-trip ≈ taker 8bp (4bp×2) + crossing spread ~1-2bp ≈ ~10bp (0.10%)
Slippage @ size mini ($50-150 notional BTC/ETH) ≈ ~0 (book dalam) — satu keuntungan size kecil
```

→ Tiap scalp WAJIB net **> ~10bp** baru BEP. Rata-rata |return| 1m BTC cuma ~5-15bp →
cuma sinyal terkuat yang bisa lewat hurdle. **Maker (limit, 1.8bp/side → 3.6bp RT) memangkas
biaya separuh** tapi nambah fill-risk → pertimbangan P2/P3, bukan default.

## Signal families (P2 scope) — dikunci oleh data (OHLCV-only)

1. **Momentum burst** — thrust continuation (price+volume surge → ikut arah).
2. **Mean-reversion** — z-score / Bollinger fade ke VWAP/MA.
3. **Volatility breakout** — range/ATR expansion break level.

❌ **Excluded: orderbook imbalance** — butuh L2 depth stream, GAK bisa di-backtest dari klines.
Defer ke eksperimen live-only nanti kalau ada famili lain yang lolos.

## Phase plan

- **P1** (now): data + scope ← *ini*
- **P2**: signal engine rule-based (3 famili di atas) → entry/exit + thesis per bar
- **P3 GATE**: backtest KERAS — fee+spread+slippage+latency penuh + walk-forward OOS.
  Reject kalau gak survive. **Gate sebelum $1 risk.**
- **P4** (gated): paper dry-run real-time → micro-live kalau konsisten.

## Risk rules (warisan discretionary, adapt buat scalp)

Risk ≤5% modal/trade · lev 2-3x · tiap entry SL keras + TP + R:R jelas · daily-loss-limit +
permanent-floor killswitch · no revenge / no add-to-loser. Scalp = banyak trade kecil →
disiplin cut + cap jumlah trade/hari ekstra penting.
