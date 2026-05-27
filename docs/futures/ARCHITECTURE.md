# Futures Trading System — Architecture

**Tanggal:** 2026-05-25 · **Fase 2 (Desain)** · Penulis: Claude (delegasi penuh dari Worm)
**Baca dulu:** [`RESEARCH_NOTES.md`](RESEARCH_NOTES.md)

---

## Prinsip desain

1. **Extend, jangan rewrite.** Reuse ~9k LOC existing (`brokers`/`risk_guard`/`cpcv`/`storage`/`telegram`).
2. **Dua thesis, satu fondasi** (funding-carry = lead, pairs-neutral = secondary).
3. **Validation gate sebelum live** (keras, non-negotiable).
4. **Risk konservatif:** Quarter Kelly + cap leverage 2x + margin ISOLATED + liq-distance guard.
5. **Dry-run dulu di mana-mana** (mirror pola `DryRunSpotBroker`).

---

## Reuse map (existing → futures)

| Modul existing | Peran di sistem futures |
|---|---|
| `brokers.py` | Pola di-mirror → `futures_broker.py` (NEW). `BinanceSpotBroker` dipakai buat **spot leg** carry. |
| `risk_guard.py` | Reuse daily killswitch + permanent floor + max-trades. **Extend** margin/likuidasi/leverage. |
| `cpcv.py` | Validation gate (PBO, DSR, verdict). **Extend** returns-series Sharpe buat carry. |
| `cointegration.py` | Engle-Granger buat seleksi pair-neutral. |
| `spot_pairs.py` | Pola z-score signal → adaptasi `pairs_neutral.py`. |
| `ingest_funding.py` + `backfill_binance_funding.py` | Data funding (real-time + historis) — **inti** carry. |
| `ingest_ohlcv.py` | OHLCV spot + perp (buat basis = perp − spot). |
| `storage.py` (DuckDB) | Journal + market data. **Extend** schema futures. |
| `telegram.py` | Alert (reuse apa adanya). |
| `atr_sizing.py` / `sizing.py` | Basis buat `kelly_sizing.py` (NEW). |
| `monitor_positions.py` | Pola monitor → extend buat liq-distance + margin ratio + funding accrual. |

---

## Arsitektur berlapis

```
[1 DATA] funding + OHLCV(spot,perp) + mark/index price  →  DuckDB
   │
[2 BROKER] futures_broker.py (USDT-M) + spot broker (existing)
   │
[3 STRATEGY] funding_carry.py (lead) | pairs_neutral.py (secondary)
   │            └─ CarryPositionManager (koordinasi 2 leg)
[4 RISK] risk_guard(extended) + kelly_sizing.py  (leverage cap, liq guard, margin, funding cost)
   │
[5 BACKTEST+VALIDATE] backtest_funding_carry.py / backtest_pairs_neutral.py  →  cpcv.py GATE
   │                                                                              (DSR>0.5, PBO<30%)
[6 EXEC] futures_executor.py  (gated: cuma jalan kalau lolos gate + risk clear)
   │
[7 MONITOR] monitor (liq distance, margin, funding) + Telegram + killswitch
```

### 1. Data layer
- **Funding:** pastiin coverage USDT-M perp funding historis (per 8h) + real-time. Model carry = `Σ funding − fees − basis_drift`.
- **Harga:** OHLCV **spot DAN perp** (carry butuh dua-duanya; basis = perp − spot). Mark price + index price buat hitung likuidasi.
- Simpan ke DuckDB via `storage.py`.

### 2. Broker layer — `data_pipe/futures_broker.py` (NEW)
- `BinanceFuturesBroker` (ccxt `options.defaultType="future"`): `set_leverage(symbol, lev)`, `set_margin_mode("ISOLATED")`, `place_market_order(symbol, side, amount, reduce_only)`, `place_limit_order(...)`, `fetch_positions()` (liq price, margin, uPnL), `fetch_funding_rate(symbol)`, `fetch_mark_price(symbol)`, `fetch_futures_balance()`.
- `DryRunFuturesBroker` — sintesis dari mark price + funding (no real order).
- Dataclass `FuturesOrderResult` + `Position` (side LONG/SHORT, leverage, liq_price, margin, funding_accrued).
- Pre-flight: min notional futures, **leverage cap (≤2x)**, margin cukup.
- Factory `make_futures_broker(live=False)`.

### 3. Strategy layer
- **`funding_carry.py` (NEW, LEAD):** sinyal = state funding (entry kalau funding > `entry_bps`, exit kalau < `exit_bps` atau flip negatif). Seleksi simbol: funding positif tertinggi × likuiditas cukup. Sizing leg: **notional spot = notional perp** (delta ≈ 0).
- **`CarryPositionManager`:** buka long spot (`BinanceSpotBroker`) + short perp (`futures_broker`) ~atomic, track funding accrual + net delta + basis, exit dua leg barengan.
- **`pairs_neutral.py` (NEW, SECONDARY):** cointegration (Engle-Granger) + filter OU half-life **<30 hari** + z-score entry/exit. Long perp A / short perp B (true market-neutral). Reuse `cointegration.py` + pola `spot_pairs.py`.

### 4. Risk layer — extend `risk_guard.py` + `kelly_sizing.py` (NEW)
- **Reuse:** daily killswitch (block ENTRY), permanent floor (drawdown), max trades/day.
- **NEW:** leverage cap enforce (hard 2x), **liquidation-distance guard** (tolak entry / alert kalau harga likuidasi terlalu deket), margin-ratio monitor, funding-cost accounting masuk PnL.
- **`kelly_sizing.py`:** Quarter Kelly `0.25 × f*` dari estimasi edge/win-rate, ber-cap. Masukin funding ke edge.

### 5. Backtest + Validation
- **`backtest_funding_carry.py` (NEW):** simulasi long spot + short perp; akru funding tiap interval 8h (data historis); kurangi fee (spot taker 0.1% + perp taker 0.04% / maker 0.02%); model basis drift; output **returns series**.
- **`backtest_pairs_neutral.py` (NEW):** spread pair + funding dua leg + liquidation math.
- **Validasi:** `cpcv.py` (PBO, DSR, verdict). **TODO:** tambah helper Sharpe berbasis returns-series (carry = stream kontinu, bukan trade diskret).
- **GATE:** lolos = verdict ROBUST/MARGINAL **DAN** DSR > 0.5 **DAN** PBO < 30% **DAN** walk-forward konsisten. Gagal → cari thesis lain.

### 6. Execution layer — `data_pipe/futures_executor.py` (NEW)
- `execute_carry_open/close` (koordinasi 2 leg, `reduce_only` saat close), `execute_pair_open/close`.
- Gates berurutan: leverage cap → liq distance → daily killswitch → permanent floor → max trades.
- Tulis journal + Telegram (reuse `telegram.py`). Rollback kalau salah satu leg gagal fill.

### 7. Monitoring
- Extend `monitor_positions.py`: poll liq distance, margin ratio, funding accrual. Alert Telegram. Auto-killswitch kalau liq-distance breach.

---

## Storage schema extension

`trade_journal` tambah kolom (atau tabel `futures_journal` baru):
`leverage`, `liq_price`, `funding_accrued`, `mark_price`, `leg_type` (SPOT/PERP), `group_id` (nyambungin 2 leg carry). `direction` (LONG/SHORT) + `size` **udah ada**.

## Config — `config/futures.yaml` (NEW)

```yaml
futures:
  leverage_cap: 2          # hard cap
  margin_mode: ISOLATED
  kelly_fraction: 0.25     # quarter Kelly
  liq_distance_min_pct: 15 # tolak entry kalau likuidasi <15% dari mark
  fees: { spot_taker: 0.001, perp_taker: 0.0004, perp_maker: 0.0002 }
carry:
  funding_entry_bps: 1.0   # entry kalau funding/8h > 0.01%
  funding_exit_bps: 0.5
  min_notional_usdt: 25
pairs:
  half_life_max_days: 30
  z_entry: 2.0
  z_exit: 0.5
```

---

## Build sequence (prioritas)

1. **`futures_broker.py` + tests** (dry-run smoke) ← MULAI SINI
2. Verifikasi/backfill funding data USDT-M
3. `risk_guard` futures extensions + `kelly_sizing.py`
4. **`backtest_funding_carry.py` + CPCV** ← **GATE carry**
5. *(kalau lolos)* `futures_executor` + monitor + live 2x kecil
6. `pairs_neutral` + backtest + validate (paralel/sesudah)

---

## Catatan jujur & risiko desain

- **Capital minimum buat carry:** carry butuh modal di **dua wallet** (spot leg + margin perp). Di $50 (split ~$25/leg) masih di atas min-notional tapi fee + funding income jadi tipis banget. **$100–200 lebih masuk akal** buat carry — relevan ke keputusan alokasi yang belum lo kunci.
- **cpcv Sharpe proxy** trade-based → wajib tambah returns-series Sharpe sebelum percaya verdict carry.
- **Funding flip** → exit rule + monitor wajib (jangan hold pas funding negatif).
- **Leg execution risk** (satu leg fill, satu nggak) → buka ~atomic + rollback.
- **Likuidasi short leg** walau delta-neutral: kalau perp spike, margin futures bisa keliquidasi sebelum gain spot terealisasi → short leg **well-margined (~1x efektif)**, ISOLATED.
