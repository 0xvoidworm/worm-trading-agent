"""Tests untuk data_pipe.health — freshness, gaps, feed liveness."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.health import (
    append_log,
    check_feeds,
    check_ohlcv_gaps,
    check_staleness,
    run_health_check,
)
from data_pipe.storage import Storage


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT"],
    "tier2": [],
}


def _fresh_ohlcv(symbol: str, n: int, end_ts: pd.Timestamp) -> list[dict]:
    """n bar 1h yang diakhiri di end_ts (inklusif)."""
    return [
        {
            "exchange": "bybit",
            "symbol": symbol,
            "timeframe": "1h",
            "ts": end_ts - pd.Timedelta(hours=(n - 1 - i)),
            "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
            "volume": 1.0,
        }
        for i in range(n)
    ]


def _gappy_ohlcv(symbol: str, end_ts: pd.Timestamp) -> list[dict]:
    """10 bar normal lalu skip 5h lalu 5 bar lagi — total 15 bar, 1 gap."""
    rows = []
    base = end_ts - pd.Timedelta(hours=19)
    for i in range(10):
        rows.append({
            "exchange": "bybit", "symbol": symbol, "timeframe": "1h",
            "ts": base + pd.Timedelta(hours=i),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0,
        })
    # gap: skip i=10..14 (5 hours missing)
    for i in range(15, 20):
        rows.append({
            "exchange": "bybit", "symbol": symbol, "timeframe": "1h",
            "ts": base + pd.Timedelta(hours=i),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0,
        })
    return rows


def test_status_ok_when_data_fresh():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 24, now))
            # funding baru (< 12h old)
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT",
                "ts": now - pd.Timedelta(hours=1),
                "funding": 0.0001, "mark_price": 70000,
            }])
            # mock news: 1 item per feed dalam 24h terakhir
            from data_pipe.ingest_news import FEEDS
            now_dt = datetime.now(timezone.utc)
            news_rows = [
                {
                    "id": f"n-{i}",
                    "source": name, "title": f"t{i}", "url": f"u{i}",
                    "published_at": now_dt - timedelta(hours=1),
                    "fetched_at": now_dt, "symbols": "BTC",
                }
                for i, (name, _) in enumerate(FEEDS)
            ]
            s.upsert_news(news_rows)

            rep = run_health_check(s, _CFG)
            assert rep["status"] == "OK", rep
            assert rep["ohlcv_gaps"] == []
            assert rep["ohlcv_stale"] == []
            assert all(f["ok"] for f in rep["feeds"])


def test_status_stale_when_ohlcv_old():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            # data 5 jam lalu → > 2h threshold
            end = pd.Timestamp.now("UTC").tz_localize(None).floor("h") - pd.Timedelta(hours=5)
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 10, end))
            rep = run_health_check(s, _CFG)
            assert rep["status"] == "STALE"
            assert len(rep["ohlcv_stale"]) == 1
            assert rep["ohlcv_stale"][0]["symbol"] == "BTC/USDT"


def test_status_gap_when_ohlcv_has_hole():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            # bar berakhir di now → tidak stale, tapi ada 1 gap 5h di tengah
            now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
            s.upsert_ohlcv(_gappy_ohlcv("BTC/USDT", now))
            # funding fresh → bukan penyebab GAP
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT",
                "ts": now, "funding": 0.0001, "mark_price": 70000,
            }])
            # semua feed live
            from data_pipe.ingest_news import FEEDS
            now_dt = datetime.now(timezone.utc)
            s.upsert_news([
                {
                    "id": f"n-{i}", "source": name, "title": f"t{i}", "url": f"u{i}",
                    "published_at": now_dt - timedelta(hours=1),
                    "fetched_at": now_dt, "symbols": "BTC",
                }
                for i, (name, _) in enumerate(FEEDS)
            ])

            rep = run_health_check(s, _CFG)
            assert rep["status"] == "GAP", rep
            assert len(rep["ohlcv_gaps"]) == 1
            g = rep["ohlcv_gaps"][0]
            assert g["symbol"] == "BTC/USDT"
            assert g["gap_count"] == 1
            assert g["max_gap_hours"] >= 5.0


def test_status_gap_when_feed_dead():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 10, now))
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT", "ts": now,
                "funding": 0.0001, "mark_price": 70000,
            }])
            # TIDAK insert news apa pun → semua feed DEAD
            rep = run_health_check(s, _CFG)
            assert rep["status"] == "GAP"
            assert all(not f["ok"] for f in rep["feeds"])


def test_check_feeds_counts_only_window():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            now = datetime.now(timezone.utc)
            # coindesk: 1 item dalam window, 1 di luar window (48h lalu)
            s.upsert_news([
                {"id": "a", "source": "coindesk", "title": "t", "url": "u1",
                 "published_at": now - timedelta(hours=2),
                 "fetched_at": now, "symbols": None},
                {"id": "b", "source": "coindesk", "title": "t", "url": "u2",
                 "published_at": now - timedelta(hours=48),
                 "fetched_at": now, "symbols": None},
            ])
            feeds = check_feeds(s, window_hours=24)
            by_src = {f["source"]: f for f in feeds}
            assert by_src["coindesk"]["count_24h"] == 1
            assert by_src["coindesk"]["ok"] is True
            assert by_src["cointelegraph"]["ok"] is False


def test_append_log_writes_one_line():
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "health.log"
        report = {
            "status": "OK",
            "generated_at": datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc),
            "summary": "ohlcv 8/8 fresh · 0 gap-symbols · funding 8/8 fresh · feeds 5/5 live",
        }
        append_log(report, log)
        append_log(report, log)
        lines = log.read_text().splitlines()
        assert len(lines) == 2
        assert "OK" in lines[0]
        assert "2026-04-25 12:00:00 UTC" in lines[0]


def test_check_ohlcv_gaps_handles_empty_symbols():
    """Regression: empty `symbols` previously crashed with `IN ()` ParserException.
    Should return [] without hitting DB.
    """
    with tempfile.TemporaryDirectory() as tmp:
        s = Storage(db_path=Path(tmp) / "x.duckdb")
        try:
            assert check_ohlcv_gaps(s, "bybit", [], "1h") == []
        finally:
            s.close()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
