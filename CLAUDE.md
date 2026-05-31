# CLAUDE.md — trading_agent

Quick-start untuk AI agent (Claude / Cursor / etc).

**Status (2026-05-31):** PATH α LIVE Binance ($47 capital), AUTO-TRADER **CURRENTLY DISABLED** at systemd level (need explicit verification before any claim of "active trading"). 5 systematic thesis class **REJECTED** historically. Lane sekarang = **discretionary AI trading** (rules: ≤5% risk/trade, 2-3x leverage, SL+TP wajib, no revenge). E1 experiment last activity 2026-05-28 (SAFETY_MAX_LEVERAGE bumped 5→10).

## Memory references (READ FIRST)

- [project_trading_agent_24may2026_resume_state.md](~/.claude/projects/-home-owner/memory/project_trading_agent_24may2026_resume_state.md) — **READ FIRST** di session baru: live state snapshot + how-to-resume + topology + verdicts
- [project_trading_agent_25may2026_dex_futures_start.md](~/.claude/projects/-home-owner/memory/project_trading_agent_25may2026_dex_futures_start.md) — Futures arsitektur + thesis history (carry FAIL + pairs FAIL + scalp REJECTED)
- [project_trading_agent_28may2026_experiment_e1.md](~/.claude/projects/-home-owner/memory/project_trading_agent_28may2026_experiment_e1.md) — E1 back-test live experiment + leverage bump
- [project_trading_agent_planning.md](~/.claude/projects/-home-owner/memory/project_trading_agent_planning.md) — Historical thesis arc

## Repo structure

```
~/trading_agent/
├── scripts/                          (entry points)
│   ├── tx.py                         (656 LOC operational CLI, wrapped at ~/.local/bin/tx)
│   ├── dashboard.py                  (Rich TUI)
│   ├── auto_trader.py monitor_*.py   (background workers)
│   ├── daily_summary_telegram.py     (cron 01:00 UTC)
│   └── exec_e1.py                    (E1 experiment driver)
├── data_pipe/
│   ├── futures_broker.py             (BinanceFuturesBroker — ALSO imported by MAGOTShtora)
│   ├── brokers.py                    (spot BinanceSpotBroker)
│   └── ... (signal pipeline)
├── data/
│   ├── market.duckdb                 (289MB OHLCV cache)
│   ├── *.latest.json                 (runtime state, gitignored)
│   └── backtest_results/             (35 audit reports)
├── tests/                            (26 test files, 330+ tests)
└── docs/futures/                     (RESEARCH_NOTES, ARCHITECTURE, SCOPE markdown)
```

## Entry points

```bash
# tx CLI (operational trading)
tx status                              # current position + wallet
tx price BTC/USDT:USDT                 # mark + funding
tx open SHORT BTC/USDT:USDT 0.001 --sl 76800 --tp 74400 --leverage 5
tx close BTC/USDT:USDT
tx log --tail 20                       # audit ~/.txec/session.jsonl

# Dashboard (Rich TUI)
ALFABLUE                               # alias: cd + venv activate + python scripts/dashboard.py

# Tests
pytest tests/ -q

# Daily summary (manual run)
.venv/bin/python scripts/daily_summary_telegram.py
```

## Systemd services (USER scope, autostart via linger)

**Enabled + active (verified 2026-05-31):**
- `worm-trading-daemon.service`
- `worm-trading-dashboard.service`
- `worm-trading-monitor-positions.service`
- `worm-trading-telegram-control.service` (bot @wantrax665_bot)

**File exists but DISABLED:**
- `worm-trading-monitor-futures.service`
- `worm-trading-monitor-signals.service`
- `worm-trading-watch-wif.service`

```bash
# Manage (set XDG_RUNTIME_DIR if from non-tty session)
XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status 'worm-trading-*'
journalctl --user -fu worm-trading-daemon.service
```

## Don't

1. **JANGAN launch worker scripts manually** (auto_trader.py, monitor_*.py) — systemd sudah jalanin, manual = duplicate → double-order risk.
2. **JANGAN ubah `SAFETY_MAX_LEVERAGE`** di `data_pipe/futures_broker.py` tanpa explicit Worm consent (current = 10, was 5).
3. **JANGAN trust "active trading" claim** dari memory tanpa verify systemd `is-active` per service — topology drift terjadi.
4. **JANGAN bypass `risk_guard`** layer — strategy cap (2x default) di sini, broker hard cap (10x) cuma backstop.
5. **JANGAN backfill ulang `market.duckdb`** kecuali corrupt (289MB, 1095 days OHLCV, ~10 menit re-backfill).
6. **JANGAN grind thesis baru** — meta verdict Worm: "systematic alpha retail crypto largely arbitraged/decaying. STOP grinding thesis baru."

## Gotchas

- **Multi-Assets mode ON → ISOLATED N/A.** `disable_multi_assets_mode()` di broker handles, tapi cek `get_multi_assets_mode()` before assuming.
- **SL/TP = conditional/algo order.** Cancel pakai `params={'stop': True}`. ReduceOnly leg ga auto-cancel pas position closes → cleanup manual.
- **Market+bracket reliable; limit+monitor fragile** (per E1 lessons).
- **5 thesis class REJECTED out-of-sample:** FUNDING_FLIP (alpha -174 to -182%), COINTEGRATION (8 runs INCONSISTENT), TREND_FOLLOW, ENSEMBLE, CROSS_EXCHANGE. Scalp mean_reversion P1-P3 mati semua TF.

**Last refresh:** 2026-05-31
