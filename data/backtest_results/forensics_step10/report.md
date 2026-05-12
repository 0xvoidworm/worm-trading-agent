# Empty-Quarter Forensics — Step 10

_Generated: 2026-04-26T02:52:19.203887+00:00_

## H1 — Detector Code Archaeology

Git available: **False** (proxy: file_mtime + log_head)  
Detector file: `dashboard/signal_log.py`  
Detector mtime: **2026-04-22T23:50:49.113667**  
Daemon mtime: **2026-04-26T11:00:48.212973**  
Backfill historical mtime: **2026-04-19T18:22:27.198598**  
Daemon first-run (log head): **2026-04-24 17:42:43**  

### Per-event-type birth proxy

| Event Type | First Signal TS | Last Signal TS | Total |
|---|---|---|---|
| `REGIME_CHANGE` | 2026-04-19T03:29:04.163617 | 2026-04-26T01:00:00 | 79 |
| `FUNDING_EXTREME` | 2026-02-19T16:00:00 | 2026-02-20T08:00:00 | 3 |
| `FUNDING_FLIP` | 2025-11-14T00:00:00 | 2026-04-08T16:00:00 | 21 |
| `Z_SPIKE` | 2026-04-04T15:00:00 | 2026-04-24T20:00:00 | 35 |

## H2 — Raw Data Coverage

| Symbol | OHLCV range | OHLCV bars | OHLCV days | Funding range | Funding rows | Funding days |
|---|---|---|---|---|---|---|
| `ARB/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `AVAX/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `BNB/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `BTC/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `ETH/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `OP/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `SOL/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |
| `XRP/USDT` | 2025-04-26 → 2026-04-26 | 8761 | 366 | 2025-04-26 → 2026-04-26 | 1095 | 366 |

News: 465 items · 25 active days · 2025-12-29 → 2026-04-25

## H3 — Detector Replay Mechanism

### Detector storage queries (lookback caps)

| Storage call | limit |
|---|---|
| `storage.get_ohlcv(...)` | 500 |
| `storage.get_funding(...)` | 500 |
| `storage.get_ohlcv(...)` | 500 |
| `storage.get_ohlcv(...)` | 500 |

- Uses watermark walk (`_last_event_ts`): **True**
- Forward-only after first seed: **True**
- Explicit replay scripts found: **None**
- Backfill historical script: `scripts/backfill_historical.py` (data only, NOT detector replay)

### Implication

Detector dipanggil setiap cycle dengan `limit=500` per storage query:
- OHLCV (1h timeframe) → 500 bars = **~20.8 days back from now**
- Funding (8h interval) → 500 rows = **~166.7 days back from now**

Walaupun OHLCV table punya 8,761 bars per symbol (366 days), detector tidak pernah melihat bar di luar 500 terakhir. Q1/Q2/Q3 unreachable by design saat ini — bukan karena raw data hilang, melainkan karena detector tidak punya replay mechanism untuk batch-walk historical.

## Bonus — FUNDING_EXTREME Recalibration

Total funding rows: **8760**  
Current threshold: **0.001** (0.1% per 8h (~109% APR))  
Above current threshold: **11** (0.126%)  

### |funding_rate| distribution

| Pct | |rate| | APR proxy |
|---|---|---|
| median | 0.000078 | +8.5% |
| p75    | 0.000100    | +11.0% |
| p90    | 0.000100    | +11.0% |
| p95    | 0.000123    | +13.5% |
| p99    | 0.000242    | +26.5% |
| max    | 0.008416    | +921.6% |

Suggested thresholds:
- **p95 cut** = 0.000123 → ~439 historical emits (vs current 11)
- **p99 cut** = 0.000242 → ~89 historical emits

_Recommendation:_ ganti `FUNDING_EXTREME = 0.001` di `dashboard/signal_log.py` ke **p99** (~tail event) atau **p95** (broader, ~26 emits/year extrapolated). p95 = better fusion supply, p99 = preserve 'extreme' semantics.

_JANGAN auto-apply._ Recalibration ini suggestion untuk Step 11+, bukan auto-fix di Step 10.

## VERDICT

**CAUSE = H3**  
**FIXABLE = Yes**  
**EFFORT = Option A: 30min — bump limit to None (full history) di detect_*() calls. Option B: 2-4h — write detector_replay.py yang feed historical ohlcv batched + idempotent emit.**  

### Issues flagged

- H1_CODE_YOUNG: detector lahir 3d ago
- H3_LOOKBACK_CAP: detector queries limit=500 baris tanpa replay script → Q1-Q3 unreachable meskipun raw data ada

### RECOMMENDATION

→ **Detector replay** adalah unblocker terbesar. Dua opsi:

**A. Quick fix (30min):** ubah `dashboard/signal_log.py` — drop `limit=500` di `detect_regime_changes`, `detect_funding_events`, `detect_cointegration_zspikes`. Detector akan auto-walk full history tiap cycle. Risiko: cycle latency naik dari ~38s ke mungkin 200s+ karena classify() bar-per-bar 8,761× per symbol. Belum optimal untuk daemon production cycle.

**B. Proper fix (2-4h):** tulis `scripts/detector_replay.py` one-shot CLI yang load full history sekali, jalankan classify + spread per bar, emit signal_events backdated dengan `as_of_ts = bar_ts`. Idempotent via dedup id (sudah ada). Setelah replay selesai, daemon resume forward-only dengan limit=500 (cycle latency tetap normal). Run sekali, hasil persistent.

Setelah replay, re-run `signal_inventory_audit.py` → expect Q1-Q3 ter-isi → THEN execute Step 10 fusion plumbing dengan supply yang honest temporal-distributed.
