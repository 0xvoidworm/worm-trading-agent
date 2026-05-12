"""Tests for StorageClamped — view-based clamping + broker delegation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data_pipe.paper_broker import PaperBroker
from data_pipe.storage import Storage
from data_pipe.storage_clamped import StorageClamped


def _seed_source(tmp_path):
    """Build a small production-like Storage with hourly OHLCV + signals + news + funding."""
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    base = datetime(2026, 3, 1)
    rows = []
    for i in range(48):  # 48 hourly bars
        rows.append({
            "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
            "ts": base + timedelta(hours=i),
            "open": 50000 + i, "high": 50050 + i, "low": 49950 + i,
            "close": 50000 + i * 10, "volume": 100.0,
        })
    s.upsert_ohlcv(rows)
    # Funding every 8h
    fund = []
    for i in range(0, 48, 8):
        fund.append({"exchange": "bybit", "symbol": "BTC/USDT",
                     "ts": base + timedelta(hours=i), "funding": 0.0001,
                     "mark_price": 50000.0})
    s.upsert_funding(fund)
    # News across the window
    news = []
    for i in range(0, 48, 6):
        news.append({"id": f"n_{i}", "source": "coindesk",
                     "title": f"news{i}", "url": f"http://x/{i}",
                     "published_at": base + timedelta(hours=i),
                     "fetched_at": base + timedelta(hours=i),
                     "symbols": "BTC,ETH"})
    s.upsert_news(news)
    # Signals (one per 12h)
    sigs = []
    for i in range(0, 48, 12):
        sigs.append({"id": f"sig_{i}", "ts": base + timedelta(hours=i),
                     "event_type": "REGIME_CHANGE", "symbol": "BTC/USDT",
                     "severity": "warn",
                     "payload": "to=trending_up t=10.5"})
    s.upsert_signals(sigs)
    return s, base


def test_default_as_of_returns_all_history(tmp_path):
    src, _ = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    df = c.get_ohlcv("bybit", "BTC/USDT", "1h")
    assert len(df) == 48  # all 48 bars visible (sentinel as_of)
    src.close()
    c.close()


def test_set_as_of_clamps_ohlcv(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    # Cut at hour 24 → only bars 0..24 visible (inclusive)
    c.set_as_of(base + timedelta(hours=24))
    df = c.get_ohlcv("bybit", "BTC/USDT", "1h")
    assert len(df) == 25
    assert df["ts"].max() == base + timedelta(hours=24)
    src.close()
    c.close()


def test_set_as_of_clamps_signals(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=15))
    df = c.get_signals(limit=100, event_type="REGIME_CHANGE")
    # Sigs at h0, h12 visible. h24, h36 hidden.
    assert len(df) == 2
    src.close()
    c.close()


def test_set_as_of_clamps_news_view(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=10))
    # Direct view query (mimics health.check_feeds)
    df = c._conn.execute("SELECT COUNT(*) FROM news").fetchdf()
    # h0, h6 visible (h12 hidden).
    assert int(df.iloc[0, 0]) == 2
    src.close()
    c.close()


def test_set_as_of_clamps_funding(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=20))
    last = c.latest_funding_ts("bybit", "BTC/USDT")
    # funding at h0,h8,h16 visible (h24 hidden)
    assert last == base + timedelta(hours=16)
    src.close()
    c.close()


def test_since_hours_relative_to_as_of(tmp_path):
    """get_signals(since_hours=12) anchored to as_of, bukan wall clock."""
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=36))
    # Window [as_of-12, as_of] = [h24, h36]; sigs at h24 visible (h36 boundary).
    df = c.get_signals(limit=100, event_type="REGIME_CHANGE", since_hours=12)
    # h24 dan h36 sig (kalau ada). Berdasarkan seed, sig at h24 dan h36 ada.
    assert len(df) == 2
    src.close()
    c.close()


def test_add_trade_delegates_to_broker(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=24))
    tid = c.add_trade(pair="BTC/USDT", direction="LONG",
                      entry_price=50100.0, size=0.1,
                      thesis="test")
    open_df = broker.get_open_positions()
    assert len(open_df) == 1
    assert int(open_df.iloc[0]["id"]) == tid


def test_close_trade_delegates_with_fee(tmp_path):
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=24))
    tid = c.add_trade(pair="BTC/USDT", direction="LONG",
                      entry_price=100.0, size=1.0)
    c.set_as_of(base + timedelta(hours=30))
    res = c.close_trade(tid, exit_price=110.0, notes="TP")
    # fee 0.11% × 210 = 0.231 ; pnl = 10 - 0.231 = 9.769
    assert res["pnl"] == pytest.approx(9.769, abs=1e-6)


def test_upsert_ohlcv_is_noop_in_backtest(tmp_path):
    src, _ = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    n_before = int(c._conn.execute("SELECT COUNT(*) FROM _ohlcv_raw").fetchone()[0])
    n = c.upsert_ohlcv([{"exchange": "bybit", "symbol": "BTC/USDT",
                         "timeframe": "1h", "ts": datetime(2027, 1, 1),
                         "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1}])
    n_after = int(c._conn.execute("SELECT COUNT(*) FROM _ohlcv_raw").fetchone()[0])
    assert n == 0
    assert n_before == n_after
    src.close()
    c.close()


def test_upsert_signals_writes_through_for_risk_emit(tmp_path):
    """risk_guard emits RISK_KILLSWITCH via upsert_signals — must persist."""
    src, base = _seed_source(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=24))
    n_before = int(c._conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0])
    c.upsert_signals([{
        "id": "risk_run_1", "ts": base + timedelta(hours=24),
        "event_type": "RISK_KILLSWITCH", "symbol": None,
        "severity": "critical", "payload": "test",
    }])
    n_after = int(c._conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0])
    assert n_after == n_before + 1
    src.close()
    c.close()
