# SCALPING ALGORITHM — Work Proposal

**2026-05-27** · Author: Claude (utk Worm) · Status: **PROPOSAL (pre-build, minta nod)**

## ⚠️ Reality check (jangan di-skip)

P1-P3 kelar hari ini. **Gate verdict: 3 famili rule-based (momentum/mean-rev/breakout) di 1m
OHLCV = NET-NEGATIF after cost, SEMUA TF.** mean-rev punya edge arah real tapi cuma **+0.6bp
gross/trade** — ~6-10× kekecilan vs cost termurah (~3.6bp maker round-trip). Detail di
`SCALPING_SCOPE.md` § VERDICT.

**Konsekuensi keras:** algoritma scalping live di atas sinyal ini = **mesin RUGI otomatis**
(makin sering trade makin cepet tergerus cost). Proposal ini BUKAN "deploy yang udah ada".
Ini: **(A) bangun engine-nya (infra, paper-first), (B) buru edge yang lolos cost-bar,
(C) deploy duit HANYA lewat gate.** $0 real risk sampe (C).

## Tujuan

"Algoritma scalping" = loop real-time rule-based: `data → sinyal → gate → eksekusi
market+bracket → manage → log → ulang`. Lane: hold 1-5 menit (bukan HFT, bukan LLM-in-loop).

---

## PHASE A — Execution Engine (paper-mode) — *buildable NOW*

Inilah "algoritma"-nya sebagai infra. Jalan DRY-RUN dulu ($0 risk), validasi loop end-to-end
lawan data live. Files: `data_pipe/scalp_engine.py` + `scripts/run_scalp.py`.

| # | Komponen | Detail |
|---|---|---|
| 1 | `ScalpEngine` | Orchestration loop, config-driven (symbol, family, params, risk caps, max_trades/day, cooldown, `mode=paper\|live`). |
| 2 | Data feed | Poll 1m klines terbaru (ccxt), maintain rolling window. **Act CUMA di bar CLOSED** (no-lookahead, sama prinsip backtest). |
| 3 | Signal eval | Reuse `scalp_signals` di window live → sinyal terbaru + bracket (SL/TP ATR). |
| 4 | Risk + exec | Reuse `risk_guard` + `futures_executor.open_protected`: enforce ≤5% risk/trade, lev cap 2-3x, max concurrent=1, daily-loss killswitch, cooldown antar-trade. |
| 5 | Position manager | Bracket exchange-side; detect close; **auto-cancel leg yatim** (`cancel_order(conditional=True)` — fix udah ada). |
| 6 | Journal + Telegram | Log tiap sinyal/trade/close + alert. |

Default `mode=paper` → `futures_executor(live=False)` (DryRunFuturesBroker sintesis fill).
**Estimasi: ~1-2 sesi build + smoke.** Deliverable: engine kerja paper, kelihatan loop-nya idup.

## PHASE B — Edge Hunt (prasyarat duit real) — *make-or-break*

Gate kasih bar eksplisit: butuh **gross >~4-8bp/trade** (sekarang 0.6bp → gap ~10×). Avenue
disiplin (jujur: odds RENDAH, tapi principled, BUKAN grinding buta):

1. **Conditioning/filter** ← prioritas. Apakah edge mean-rev TERKONSENTRASI di sub-rezim
   (high-vol chop / time-of-day / pasca-likuidasi spike / funding ekstrem)? Kalo +0.6bp avg itu
   sebenernya +5bp di subset kecil + ~0 sisanya → filter ke subset = viable. Murah: tes via P3
   gate yang udah ada, tinggal split sinyal by kondisi.
2. **Maker-only fill model.** Cuma ambil limit (maker) fill entry+exit → cost ~3.6bp / lebih
   rendah (rebate tier). TAPI wajib model fill-probability + adverse selection (limit gak selalu
   fill) → butuh backtest maker-fill realistis. Cost lever-nya nyata tapi effort lebih.
3. **Sumber sinyal beda** — order-flow proxy dari 1m (volume pressure, absorption candle).

Tiap avenue → lewat **P3 gate yang ada**. **Rule keras: gak ada yang naik ke live tanpa lolos
cost dengan margin di walk-forward OOS.**
**Stop condition:** kalo 2-3 avenue conditioned gagal gate → scalp DEAD definitif, stop
(jangan grind hantu — [[worm-trading-psychology-discipline]]).

## PHASE C — Gated Deployment

HANYA kalo Phase B nemu sinyal lolos gate: engine paper-mode real-time di sinyal itu
(validation window) → micro-live (size terkecil) → scale cuma kalo konsisten. **$0 real sampe sini.**

---

## Rekomendasi jujur

- **Phase A worth dibangun** — infra reusable, the actual "algoritma", paper-first, zero downside.
- **Phase B = make-or-break.** Odds rendah (kita udah buktiin OHLCV alpha tipis), tapi #1
  (conditioning) paling mungkin + paling murah ditest. Itu yang nentuin ada gunanya engine ini
  bawa duit atau enggak.
- **Framing:** ini eksperimen high-variance + skill/infra build, **BUKAN mesin duit**. Engine =
  deliverable pasti. Edge = judi yang kemungkinan besar kalah — tapi kalo gak ada edge, kita
  berhenti di paper, gak rugi sepeser.

## Urutan eksekusi usulan

```
A1  ScalpEngine skeleton (paper)        → A2  smoke vs data live
B1  conditioning test (split via gate)  → [DECISION: lolos cost-bar? → C : stop scalp]
```

**Pilihan buat lo (Worm):**
- **Gas A dulu** (bangun engine paper) — paling konkret, ada artefak kerja.
- **Gas B dulu** (conditioning hunt) — jawab "ada edge gak?" sebelum capek bangun engine.
- (Gua condong **B1 dulu** sebenernya — kalo conditioning gagal, engine-nya gak akan pernah bawa
  duit, jadi jawab pertanyaan edge duluan lebih hemat. Tapi A kasih sesuatu yang jalan & reusable.)
