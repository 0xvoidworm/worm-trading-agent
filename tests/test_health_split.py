"""Tests untuk run_health_check_split — Step 6 two-dimensional health.

Memastikan:
  - Trading dimension murni dari ohlcv freshness/gap.
  - Operational dimension murni dari funding + RSS feed liveness.
  - combined_status tetap mengikuti priority STALE > GAP > OK (backward compat).
  - run_health_check (legacy) shape unchanged.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.health import run_health_check, run_health_check_split  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT"],
    "tier2": [],
}


def _fresh_ohlcv(symbol: str, n: int, end_ts: pd.Timestamp) -> list[dict]:
    return [
        {
            "exchange": "bybit", "symbol": symbol, "timeframe": "1h",
            "ts": end_ts - pd.Timedelta(hours=(n - 1 - i)),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1.0,
        }
        for i in range(n)
    ]


def _seed_all_feeds_live(s: Storage, now_dt: datetime) -> None:
    from data_pipe.ingest_news import FEEDS
    s.upsert_news([
        {
            "id": f"n-{i}", "source": name, "title": f"t{i}", "url": f"u{i}",
            "published_at": now_dt - timedelta(hours=1),
            "fetched_at": now_dt, "symbols": "BTC",
        }
        for i, (name, _url) in enumerate(FEEDS)
    ])


def test_split_returns_two_dimensions_and_combined():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = pd.Timestamp.utcnow().tz_localize(None).floor("h")
            now_dt = datetime.now(timezone.utc)
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 24, now))
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT",
                "ts": now - pd.Timedelta(hours=1),
                "funding": 0.0001, "mark_price": 70000,
            }])
            _seed_all_feeds_live(s, now_dt)

            rep = run_health_check_split(s, _CFG)

            assert "trading" in rep and "operational" in rep
            assert "combined_status" in rep
            assert rep["trading"]["status"] == "OK"
            assert rep["operational"]["status"] == "OK"
            assert rep["combined_status"] == "OK"
            assert rep["trading"]["ohlcv_stale"] == []
            assert rep["trading"]["ohlcv_gaps"] == []
            assert rep["operational"]["funding_stale"] == []
            assert all(f["ok"] for f in rep["operational"]["feeds"])


def test_trading_ok_independent_of_dead_feeds():
    """Trading=OK kalau ohlcv segar, walau semua feed mati.

    Inilah motivasi utama Step 6: jangan gate ENTRY karena RSS feed dead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = pd.Timestamp.utcnow().tz_localize(None).floor("h")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 24, now))
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT",
                "ts": now - pd.Timedelta(hours=1),
                "funding": 0.0001, "mark_price": 70000,
            }])
            # TIDAK insert news → semua feed mati

            rep = run_health_check_split(s, _CFG)
            assert rep["trading"]["status"] == "OK"
            assert rep["operational"]["status"] == "GAP"
            assert rep["combined_status"] == "GAP"
            assert rep["operational"]["dead_feeds"]


def test_operational_gap_when_feeds_dead_but_trading_ok():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = pd.Timestamp.utcnow().tz_localize(None).floor("h")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 24, now))
            # funding stale (>12h) → operational GAP
            s.upsert_funding([{
                "exchange": "bybit", "symbol": "BTC/USDT",
                "ts": now - pd.Timedelta(hours=24),
                "funding": 0.0001, "mark_price": 70000,
            }])
            now_dt = datetime.now(timezone.utc)
            _seed_all_feeds_live(s, now_dt)

            rep = run_health_check_split(s, _CFG)
            assert rep["trading"]["status"] == "OK"
            assert rep["operational"]["status"] == "GAP"
            assert rep["operational"]["funding_stale"]


def test_trading_stale_propagates_to_combined():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            # ohlcv > 2h old → trading STALE
            end = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(hours=5)
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 10, end))

            rep = run_health_check_split(s, _CFG)
            assert rep["trading"]["status"] == "STALE"
            assert rep["combined_status"] == "STALE"


def test_legacy_run_health_check_shape_unchanged():
    """Backward-compat: run_health_check tetap return original key set.

    Caller existing (daemon, TUI, scripts/health_check.py) tidak break.
    """
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = pd.Timestamp.utcnow().tz_localize(None).floor("h")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 10, now))
            rep = run_health_check(s, _CFG)
            for k in ("status", "generated_at", "exchange", "timeframe",
                     "symbols_checked", "ohlcv_gaps", "ohlcv_stale",
                     "funding_stale", "feeds", "summary"):
                assert k in rep, f"missing key {k}"
            # Tidak boleh ada keys baru yang muncul (avoid silent contract drift)
            assert "trading" not in rep
            assert "operational" not in rep
            assert "combined_status" not in rep


def test_split_now_utc_override_applied():
    """now_utc override → freshness check anchored ke arg, bukan wall clock."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            anchor = pd.Timestamp("2026-03-15 12:00:00")
            s.upsert_ohlcv(_fresh_ohlcv("BTC/USDT", 5, anchor))
            now_utc = anchor.to_pydatetime().replace(tzinfo=timezone.utc)
            rep = run_health_check_split(s, _CFG, now_utc=now_utc)
            assert rep["trading"]["status"] == "OK"

            # Maju 5 jam → seharusnya stale
            future = now_utc + timedelta(hours=5)
            rep2 = run_health_check_split(s, _CFG, now_utc=future)
            assert rep2["trading"]["status"] == "STALE"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
