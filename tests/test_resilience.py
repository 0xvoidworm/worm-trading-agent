"""Tests untuk daemon state layer:
  - FEED_DEAD emit sekali saat >=3 cycle berturut satu feed gagal
  - FEED_DEAD tidak re-emit selama belum recover
  - FEED_DEAD state reset saat feed recover
  - CYCLE_ABNORMAL emit saat 3 cycle empty+unhealthy
  - Retry logic integrated: fetch_feed pulang None kalau DNS gagal
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage
from scripts import daemon as daemon_mod


# ---------- helpers ----------

def _empty_news_stats(*, failed: list[str], ok: list[str]) -> dict:
    return {
        "_total": 0,
        "_duration_sec": 0.0,
        "_failed": failed,
        "_ok": ok,
    }


def _count_signals(s: Storage, event_type: str, symbol: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM signal_events WHERE event_type=?"
    params: list = [event_type]
    if symbol is not None:
        sql += " AND symbol=?"
        params.append(symbol)
    return int(s._conn.execute(sql, params).fetchone()[0])


# ---------- FEED_DEAD ----------

def test_feed_dead_emits_after_threshold(tmp_path, monkeypatch):
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        # Cycle 1 & 2: coindesk fails, masih di bawah threshold
        daemon_mod._update_feed_state(s, state, _empty_news_stats(
            failed=["coindesk"], ok=["cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD") == 0
        daemon_mod._update_feed_state(s, state, _empty_news_stats(
            failed=["coindesk"], ok=["cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD") == 0

        # Cycle 3: threshold tembus → FEED_DEAD emit
        daemon_mod._update_feed_state(s, state, _empty_news_stats(
            failed=["coindesk"], ok=["cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD", symbol="coindesk") == 1

        # Cycle 4: masih dead, tapi TIDAK re-emit
        daemon_mod._update_feed_state(s, state, _empty_news_stats(
            failed=["coindesk"], ok=["cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD", symbol="coindesk") == 1

    fs = state["feeds"]["coindesk"]
    assert fs["consecutive_failures"] == 4
    assert fs["dead_signal_emitted"] is True


def test_feed_dead_resets_on_recovery(tmp_path):
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        # 3 cycle fail → emit
        for _ in range(3):
            daemon_mod._update_feed_state(s, state, _empty_news_stats(
                failed=["decrypt"], ok=["coindesk", "cointelegraph", "theblock", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD", symbol="decrypt") == 1

        # Cycle 4: recover → state reset
        daemon_mod._update_feed_state(s, state, _empty_news_stats(
            failed=[], ok=["coindesk", "cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        fs = state["feeds"]["decrypt"]
        assert fs["consecutive_failures"] == 0
        assert fs["dead_signal_emitted"] is False

        # Kalau gagal lagi 3x → emit BARU (id beda karena ts beda)
        for _ in range(3):
            daemon_mod._update_feed_state(s, state, _empty_news_stats(
                failed=["decrypt"], ok=["coindesk", "cointelegraph", "theblock", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD", symbol="decrypt") == 2


def test_feed_dead_independent_per_source(tmp_path):
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        # 3 cycle: coindesk fail tapi decrypt ok
        for _ in range(3):
            daemon_mod._update_feed_state(s, state, _empty_news_stats(
                failed=["coindesk"], ok=["cointelegraph", "theblock", "decrypt", "bitcoinmag"]))
        assert _count_signals(s, "FEED_DEAD", symbol="coindesk") == 1
        assert _count_signals(s, "FEED_DEAD", symbol="decrypt") == 0


# ---------- CYCLE_ABNORMAL ----------

def test_cycle_abnormal_emits_after_3_empty_unhealthy(tmp_path):
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        # Cycle 1-2: empty+STALE, belum threshold
        for _ in range(2):
            emitted = daemon_mod._update_abnormal_state(
                s, state, oh_new=0, fu_new=0, nw_new=0, health_status="STALE"
            )
            assert emitted is False
        assert _count_signals(s, "CYCLE_ABNORMAL") == 0

        # Cycle 3: threshold tembus
        emitted = daemon_mod._update_abnormal_state(
            s, state, oh_new=0, fu_new=0, nw_new=0, health_status="STALE"
        )
        assert emitted is True
        assert _count_signals(s, "CYCLE_ABNORMAL") == 1

        # Cycle 4: masih empty+STALE → TIDAK re-emit
        emitted = daemon_mod._update_abnormal_state(
            s, state, oh_new=0, fu_new=0, nw_new=0, health_status="STALE"
        )
        assert emitted is False
        assert _count_signals(s, "CYCLE_ABNORMAL") == 1


def test_cycle_abnormal_resets_on_healthy_cycle(tmp_path):
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        for _ in range(3):
            daemon_mod._update_abnormal_state(s, state, 0, 0, 0, "GAP")
        assert _count_signals(s, "CYCLE_ABNORMAL") == 1

        # Cycle healthy (data masuk) → reset
        daemon_mod._update_abnormal_state(s, state, oh_new=5, fu_new=0, nw_new=0, health_status="OK")
        assert state["consecutive_empty_cycles"] == 0
        assert state["abnormal_signal_emitted"] is False


def test_cycle_abnormal_not_triggered_if_health_ok(tmp_path):
    """Empty cycle dengan health OK bukan zombie — kadang memang tidak ada data baru."""
    db = tmp_path / "db.duckdb"
    state: dict = {}
    with Storage(str(db)) as s:
        for _ in range(5):
            daemon_mod._update_abnormal_state(s, state, 0, 0, 0, "OK")
        assert _count_signals(s, "CYCLE_ABNORMAL") == 0
        assert state["consecutive_empty_cycles"] == 0


# ---------- heartbeat includes consecutive_empty_cycles ----------

def test_heartbeat_includes_consecutive_empty_cycles(tmp_path, monkeypatch):
    import yaml
    hb = tmp_path / "daemon.heartbeat"
    monkeypatch.setattr(daemon_mod, "HEARTBEAT_PATH", hb)
    stats = {"ohlcv_rows": 100, "funding_rows": 50, "news_rows": 10, "signal_rows": 5}
    state = {"consecutive_empty_cycles": 2}
    daemon_mod._write_heartbeat(42, 12.3, stats, state)
    data = yaml.safe_load(hb.read_text())
    assert data["cycle_number"] == 42
    assert data["consecutive_empty_cycles"] == 2


# ---------- ingest_news integration: failure → None ----------

def test_fetch_feed_returns_none_on_dns_failure(monkeypatch):
    from data_pipe import ingest_news

    def boom(*a, **kw):
        raise socket.gaierror("Temporary failure in name resolution")

    # Patch requests.get — retry_network akan retry 3x lalu raise; fetch_feed
    # catch dan return None.
    monkeypatch.setattr(ingest_news.requests, "get", boom)
    monkeypatch.setattr(ingest_news.time, "sleep", lambda _s: None)  # skip backoff
    # Juga patch di retry module-level sleep via kwarg path: retry_network pakai
    # time.sleep default, kita monkeypatch di retry
    from data_pipe import retry as retry_mod
    monkeypatch.setattr(retry_mod.time, "sleep", lambda _s: None)

    result = ingest_news.fetch_feed("coindesk", "https://fake.invalid/rss")
    assert result is None


def test_ingest_all_feeds_aggregates_failed_and_ok(monkeypatch, tmp_path):
    from data_pipe import ingest_news

    def fake_fetch(source, url, timeout=10):
        if source in ("coindesk", "decrypt"):
            return None  # unreachable
        return []  # reachable, no new entries

    monkeypatch.setattr(ingest_news, "fetch_feed", fake_fetch)

    with Storage(str(tmp_path / "db.duckdb")) as s:
        stats = ingest_news.ingest_all_feeds(s)

    assert set(stats["_failed"]) == {"coindesk", "decrypt"}
    assert set(stats["_ok"]) == {"cointelegraph", "theblock", "bitcoinmag"}
    assert stats["_total"] == 0
