```text
╭─────────────────────────────────────────────────────────────────╮
│   t r a d i n g - a g e n t                                     │
│   [TRADING] observability first · sejak 18 April 2026           │
╰─────────────────────────────────────────────────────────────────╯
```

> Dashboard dulu, agent kemudian. Edge yang tidak bisa divalidasi manual tidak
> akan bisa di-debug otomatis — jadi fase ini **mengamati**, bukan mengeksekusi.

**Status:** Step 0 (dashboard-first) · tanggal mulai: 2026-04-18

## Filosofi
Dashboard dulu, agent kemudian. Alasan: edge yang tidak bisa lo validasi manual,
tidak akan lo debug otomatis.

## Struktur
```
trading_agent/
├── config/pairs.yaml         # exchange, symbols, thresholds
├── data_pipe/                # ccxt ingest + DuckDB storage
│   ├── storage.py
│   ├── exchanges.py
│   ├── ingest_ohlcv.py
│   └── ingest_funding.py
├── dashboard/                # observability layer (read-only)
│   ├── regime.py             # trend/range/chaos classifier
│   ├── cointegration.py      # Engle-Granger pairs check
│   └── tui.py                # Rich Live dashboard
├── scripts/
│   ├── backfill.py           # historical data collector
│   └── dashboard.py          # launcher
├── tests/test_storage.py
└── data/market.duckdb        # auto-created
```

## Quick start

```bash
# 1) aktifkan venv
source .venv/bin/activate

# 2) backfill historical (first run ~5-10 menit)
python scripts/backfill.py

# 3a) start auto-refresh daemon (writes every 5 min in background)
./scripts/daemonctl.sh start            # default cycle 300s
./scripts/daemonctl.sh start 900        # custom cycle 15 min
./scripts/daemonctl.sh status           # check + heartbeat
./scripts/daemonctl.sh stop
./scripts/daemonctl.sh log              # tail -f daemon log

# 3b) launch dashboard (reads DB, refreshes view every 60s)
python scripts/dashboard.py
```

Daemon + dashboard run as separate processes. Dashboard connects read-only per
refresh; saat daemon sedang menulis (~35s/cycle), dashboard retry hingga 2-3 detik
kemudian render. Data freshness ditampilkan di status bar dashboard via
`data/daemon.heartbeat`.

## Apa yang dashboard tampilkan

Tampilan Bloomberg-style: amber pada hitam, heavy borders, numbered windows.

1. **[1] FUNDING RATES** — live rate perp semua symbol + APR + 7d sparkline + signal ARB WINDOW kalau >0.1%/8h
2. **[2] REGIME CLASSIFIER** — uptrend / downtrnd / ranging / chaos per symbol, ATR + t-stat + 7d price sparkline
3. **[3] PAIRS COINTEGRATION** — ADF p-value, hedge ratio, half-life, z-score + status (long/short spread)
4. **[4] NEWS FEED (RSS)** — headlines dari 5 sumber (CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Mag) + auto-tag symbol
5. **[5] SIGNAL EVENTS** — log regime change, funding extreme, funding flip, z-score spike
6. **Ticker bar** — scrolling news headlines dengan separator ▶
7. **Status bar** — DB stats, daemon heartbeat, render clock, next refresh countdown

## Apa yang dashboard TIDAK lakukan (by design)

- Tidak trade
- Tidak kasih advice sizing
- Tidak pakai LLM
- Tidak simpan API key

Tujuan fase ini: lo lihat sinyal → bandingkan sama pergerakan harga sebenarnya →
kalibrasi intuisi. Baru setelah lo yakin sinyal X legit, kita automate.

## Next steps (roadmap)

**Sprint 1 — Essential Foundation** (DONE)
- [x] Auto-refresh daemon + concurrency model
- [x] News stream integration (RSS: CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Mag)
- [x] Signal history log (REGIME_CHANGE, FUNDING_EXTREME, FUNDING_FLIP, Z_SPIKE) + CLI viewer
- [x] Bloomberg-style dashboard (sparklines, ticker bar, numbered windows)

CLI signal viewer:
```bash
python scripts/signals.py                              # last 50 events
python scripts/signals.py --type REGIME_CHANGE
python scripts/signals.py --symbol BTC/USDT --since 24
```

**Sprint 2 — Calibration Tools**
- [ ] Manual trade journal (CLI add/list)
- [ ] Data quality monitor
- [ ] Signal retrospective viewer

**Sprint 3 — Polish**
- [ ] Daily summary export (markdown)
- [ ] Cross-exchange funding spread
- [ ] Telegram alert bot (optional)

**Tahap 2 (setelah dashboard matang 3-4 minggu)**
- Claude sebagai advisor (manual panggil untuk analisa)
- Claude Managed Agent mode read-only (cloud, task budget capped)

**Tahap 3+ (deferred, butuh bukti profit manual dulu)**
- Semi-auto execution
- Full auto (dengan kill-switch hard-coded)

## Disclaimer

Proyek ini untuk **riset dan pendidikan**, bukan nasihat finansial. Dashboard
bersifat read-only: tidak mengeksekusi order, tidak menyimpan API key, dan
tidak menghitung sizing. Semua angka berasal dari data publik (ccxt + DuckDB
lokal) dan bisa salah atau tertunda. Keputusan trading dan seluruh risikonya
milik pengguna. Live execution sengaja belum ada — lihat roadmap Tahap 3+.

## Lisensi

MIT — lihat [LICENSE](LICENSE).
