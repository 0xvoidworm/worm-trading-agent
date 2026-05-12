#!/usr/bin/env python3
"""Auto-refresh daemon — writer process.

Arsitektur:
  - Long-running loop, tidak hold DB connection antar-cycle
  - Tiap cycle: buka writer, incremental update, tutup
  - Koneksi hidup <5 detik per cycle → window bentrok dengan dashboard minimal
  - Heartbeat file ditulis setelah tiap cycle sukses
  - Signal handler: SIGTERM → graceful shutdown
  - Resilience: network call di-retry 3x (exp backoff 2/4/8s) untuk DNS/timeout.
    FEED_DEAD signal emitted sekali kalau RSS feed dead >=3 cycle berturut.
    CYCLE_ABNORMAL signal emitted sekali kalau 3 cycle berturut empty+unhealthy
    (pipeline zombie — daemon jalan tapi data nggak masuk).

Run:
    python scripts/daemon.py                      # default: cycle 300s (5 min)
    python scripts/daemon.py --cycle 900          # cycle 15 min
    python scripts/daemon.py --once               # single cycle, exit
    python scripts/daemon.py --symbols BTC/USDT   # subset override

Daemon control via wrapper:
    ./scripts/daemonctl.sh {start|stop|status|log}
"""
from __future__ import annotations

import argparse
import hashlib
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.auto_advisor import run_advisor_cycle
from data_pipe.health import append_log as append_health_log, run_health_check
from data_pipe.risk_guard import check_and_engage as risk_check_and_engage
from data_pipe.storage import Storage, StorageLocked
from data_pipe.ingest_ohlcv import backfill_symbol
from data_pipe.ingest_funding import backfill_funding
from data_pipe.ingest_news import FEEDS, ingest_all_feeds
from dashboard.signal_log import run_detectors


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
HEARTBEAT_PATH = DATA_DIR / "daemon.heartbeat"
HEALTH_LOG_PATH = DATA_DIR / "health.log"
STATE_PATH = DATA_DIR / "daemon.state.yaml"

FEED_DEAD_THRESHOLD = 3       # cycle berturut
ABNORMAL_CYCLE_THRESHOLD = 3  # cycle berturut


_stop_requested = False


def _handle_signal(signum, frame):
    """Graceful shutdown — kita tidak mau stop di tengah write."""
    global _stop_requested
    print(f"\n[daemon] signal {signum} received, finishing current cycle then stop")
    _stop_requested = True


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def _load_state() -> dict:
    """Muat state dari YAML, safe-default kalau korup/missing."""
    if not STATE_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(STATE_PATH.read_text()) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(yaml.safe_dump(state, sort_keys=True))


def _emit_signal(
    storage: Storage,
    event_type: str,
    symbol: str | None,
    payload: str,
    severity: str = "warn",
    ts: datetime | None = None,
) -> None:
    """Insert satu baris signal_events. ID deterministik dari (type|symbol|payload|ts)
    sehingga dedup otomatis kalau sempat double-call.
    """
    if ts is None:
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
    seed = f"{event_type}|{symbol}|{payload}|{ts.isoformat(timespec='seconds')}"
    sig_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    storage.upsert_signals([{
        "id": sig_id,
        "ts": ts,
        "event_type": event_type,
        "symbol": symbol,
        "severity": severity,
        "payload": payload,
    }])


def _update_feed_state(
    storage: Storage,
    state: dict,
    news_stats: dict,
) -> list[str]:
    """Update per-feed consecutive_failures. Emit FEED_DEAD (sekali) kalau
    melewati threshold. Reset kalau recover.

    Return list feed yang baru di-emit sebagai FEED_DEAD di cycle ini.
    """
    feeds_state = state.setdefault("feeds", {})
    failed = set(news_stats.get("_failed", []))
    ok = set(news_stats.get("_ok", []))
    newly_dead: list[str] = []

    for source, _ in FEEDS:
        fs = feeds_state.setdefault(source, {
            "consecutive_failures": 0,
            "dead_signal_emitted": False,
            "first_failure_ts": None,
        })
        if source in failed:
            fs["consecutive_failures"] = int(fs.get("consecutive_failures", 0)) + 1
            if fs.get("first_failure_ts") is None:
                fs["first_failure_ts"] = datetime.now(timezone.utc).isoformat()
            if (
                fs["consecutive_failures"] >= FEED_DEAD_THRESHOLD
                and not fs.get("dead_signal_emitted")
            ):
                _emit_signal(
                    storage,
                    event_type="FEED_DEAD",
                    symbol=source,
                    payload=(
                        f"source={source} "
                        f"consecutive_failures={fs['consecutive_failures']} "
                        f"since={fs['first_failure_ts']}"
                    ),
                )
                fs["dead_signal_emitted"] = True
                newly_dead.append(source)
        elif source in ok:
            if fs.get("consecutive_failures", 0) > 0 or fs.get("dead_signal_emitted"):
                fs["consecutive_failures"] = 0
                fs["dead_signal_emitted"] = False
                fs["first_failure_ts"] = None
    return newly_dead


def _update_abnormal_state(
    storage: Storage,
    state: dict,
    oh_new: int,
    fu_new: int,
    nw_new: int,
    health_status: str,
) -> bool:
    """Track cycle yang empty+unhealthy. Emit CYCLE_ABNORMAL sekali saat threshold.
    Return True kalau signal baru di-emit.
    """
    empty = (oh_new + fu_new + nw_new) == 0
    unhealthy = health_status in ("GAP", "STALE")
    abnormal = empty and unhealthy
    emitted_now = False

    if abnormal:
        state["consecutive_empty_cycles"] = int(state.get("consecutive_empty_cycles", 0)) + 1
        if state.get("abnormal_first_ts") is None:
            state["abnormal_first_ts"] = datetime.now(timezone.utc).isoformat()
        if (
            state["consecutive_empty_cycles"] >= ABNORMAL_CYCLE_THRESHOLD
            and not state.get("abnormal_signal_emitted")
        ):
            _emit_signal(
                storage,
                event_type="CYCLE_ABNORMAL",
                symbol=None,
                payload=(
                    f"consecutive_empty_cycles={state['consecutive_empty_cycles']} "
                    f"health={health_status} "
                    f"since={state['abnormal_first_ts']}"
                ),
            )
            state["abnormal_signal_emitted"] = True
            emitted_now = True
    else:
        state["consecutive_empty_cycles"] = 0
        state["abnormal_signal_emitted"] = False
        state["abnormal_first_ts"] = None

    return emitted_now


def _write_heartbeat(
    cycle_n: int, cycle_duration: float, stats: dict, state: dict,
    *, n_entries: int = 0, n_exits: int = 0,
):
    """Tulis status ke file agar dashboard bisa tampil 'last refresh' dari daemon.

    Step 8: include n_entries/n_exits dari run_advisor_cycle supaya
    operator/TUI bisa lihat throughput multi-trade per cycle.
    """
    payload = {
        "last_cycle_ts": datetime.now(timezone.utc).isoformat(),
        "cycle_number": cycle_n,
        "cycle_duration_sec": round(cycle_duration, 2),
        "ohlcv_rows": stats.get("ohlcv_rows"),
        "funding_rows": stats.get("funding_rows"),
        "news_rows": stats.get("news_rows"),
        "signal_rows": stats.get("signal_rows"),
        "consecutive_empty_cycles": int(state.get("consecutive_empty_cycles", 0)),
        "n_entries": int(n_entries),
        "n_exits": int(n_exits),
    }
    HEARTBEAT_PATH.write_text(yaml.safe_dump(payload))


def run_cycle(cfg: dict, symbols: list[str], state: dict) -> tuple[int, int, int, int, dict]:
    """Satu siklus refresh. Return (ohlcv_new, funding_new, news_new, signals_new, news_stats).

    State di-mutate in-place untuk feed tracking.
    """
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    ohlcv_new = 0
    funding_new = 0
    news_new = 0
    signals_new = 0
    news_stats: dict = {}

    # Storage dibuka di dalam fungsi — release segera setelah selesai
    with Storage() as s:
        for sym in symbols:
            try:
                # days=2 → ingest_ohlcv/funding otomatis resume dari latest_ts
                ohlcv_new += backfill_symbol(s, exchange, sym, tf, days=2)
            except Exception as e:
                _log(f"  [warn] {sym} ohlcv: {type(e).__name__}: {e}")
            try:
                funding_new += backfill_funding(s, exchange, sym, days=2)
            except Exception as e:
                _log(f"  [warn] {sym} funding: {type(e).__name__}: {e}")
        # Pull RSS news — dedup via PK, per-feed retry di fetch_feed
        try:
            news_stats = ingest_all_feeds(s)
            news_new = news_stats["_total"]
            if news_stats.get("_failed"):
                _log(f"  [warn] feeds unreachable: {','.join(news_stats['_failed'])}")
        except Exception as e:
            _log(f"  [warn] news fetch: {e}")

        # FEED_DEAD bookkeeping (gunakan writer yang sama)
        if news_stats:
            newly_dead = _update_feed_state(s, state, news_stats)
            if newly_dead:
                _log(f"  [warn] FEED_DEAD emitted for: {','.join(newly_dead)}")

        # Run signal detectors on fresh data
        try:
            sig_stats = run_detectors(s, cfg)
            signals_new = sig_stats["total"]
        except Exception as e:
            _log(f"  [warn] signal detect: {e}")

    return ohlcv_new, funding_new, news_new, signals_new, news_stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", type=int, default=300, help="cycle interval seconds")
    parser.add_argument("--once", action="store_true", help="single cycle then exit")
    parser.add_argument("--symbols", type=str, default=None, help="comma-separated override")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = cfg["tier1"] + cfg["tier2"]

    _log(f"daemon starting · cycle={args.cycle}s · symbols={len(symbols)} · once={args.once}")

    state = _load_state()
    cycle_n = 0
    while not _stop_requested:
        cycle_n += 1
        t0 = time.time()
        try:
            oh_new, fu_new, nw_new, sig_new, _news_stats = run_cycle(cfg, symbols, state)
            dur = time.time() - t0
            with Storage(read_only=True) as s:
                stats = s.stats()
                health = run_health_check(s, cfg)

            # CYCLE_ABNORMAL bookkeeping — pakai writer terpisah (brief)
            try:
                with Storage() as s:
                    if _update_abnormal_state(s, state, oh_new, fu_new, nw_new, health["status"]):
                        _log(
                            f"  [warn] CYCLE_ABNORMAL emitted "
                            f"(consecutive_empty_cycles={state['consecutive_empty_cycles']})"
                        )
            except Exception as e:
                _log(f"  [warn] abnormal tracker: {e!r}")

            _save_state(state)
            try:
                append_health_log(health, HEALTH_LOG_PATH)
            except Exception as e:
                _log(f"  [warn] health log write: {e}")

            # Step 4: risk guard (daily-loss killswitch). Jalankan sebelum
            # advisor — supaya state tercermin di cycle log walau executor
            # masih akan SKIP di gate #0.5 nanti.
            risk_status = "OK"
            risk_pct = 0.0
            try:
                with Storage() as s:
                    rs = risk_check_and_engage(s, cfg, DATA_DIR)
                    risk_status = rs["status"]
                    risk_pct = rs["daily_pnl_pct"]
                    if rs.get("engaged"):
                        _log(
                            f"  [crit] RISK_KILLSWITCH engaged "
                            f"daily={risk_pct:.2f}% "
                            f"(realized={rs['realized_today']:.2f} "
                            f"unrealized={rs['unrealized_now']:.2f})"
                        )
            except Exception as e:
                _log(f"  [warn] risk_guard: {e!r}")
                risk_status = "ERR"

            # Phase 2 + Step 8: multi-trade-per-cycle advisor orchestrator.
            n_entries = 0
            n_exits = 0
            exec_label = "SKIP"
            if health["status"] == "OK":
                try:
                    with Storage() as s:
                        cycle_result = run_advisor_cycle(s, cfg, DATA_DIR)
                        n_entries = cycle_result["n_entries"]
                        n_exits = cycle_result["n_exits"]
                        exec_label = (
                            "IDLE" if (n_entries + n_exits) == 0
                            else f"E{n_entries}X{n_exits}"
                        )
                except Exception as e:
                    _log(f"  [warn] advisor/executor: {e!r}")
                    exec_label = "ERR"
            else:
                exec_label = f"GATED({health['status']})"

            # Write heartbeat AFTER advisor cycle so n_entries/n_exits are populated.
            _write_heartbeat(
                cycle_n, dur, stats, state,
                n_entries=n_entries, n_exits=n_exits,
            )

            risk_short = "KILL" if risk_status == "TRIGGER" else risk_status
            _log(
                f"cycle #{cycle_n} done in {dur:.1f}s · "
                f"+{oh_new} ohlcv · +{fu_new} funding · ~{nw_new} news · "
                f"~{sig_new} signals · health={health['status']} · "
                f"risk={risk_short} daily={risk_pct:+.2f}% · "
                f"entries={n_entries} exits={n_exits} ({exec_label}) · "
                f"empty_streak={state.get('consecutive_empty_cycles', 0)}"
            )
        except StorageLocked as e:
            _log(f"cycle #{cycle_n} skipped — storage locked: {e}")
        except Exception as e:
            _log(f"cycle #{cycle_n} FAILED: {e!r}")

        if args.once or _stop_requested:
            break

        # sleep dengan check signal tiap 1 detik → responsive terhadap SIGTERM
        slept = 0
        while slept < args.cycle and not _stop_requested:
            time.sleep(1)
            slept += 1

    _log("daemon stopped")


if __name__ == "__main__":
    main()
