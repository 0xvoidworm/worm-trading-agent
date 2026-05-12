"""Look-ahead bias prevention tests — NON-NEGOTIABLE.

Goals:
  1. A signal emitted in the future (ts > as_of) MUST NOT be visible to
     advisor at as_of.
  2. A future price drop (e.g. crash 24h ahead) MUST NOT influence current
     latest_close used by sizing/MTM.
  3. News published after as_of must be hidden from build_context's news
     selector.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from data_pipe.advisor import build_context
from data_pipe.auto_advisor import run_auto_advisor
from data_pipe.paper_broker import PaperBroker
from data_pipe.storage import Storage
from data_pipe.storage_clamped import StorageClamped


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT"],
    "tier2": [],
    "portfolio": {
        "starting_capital": 10000.0,
        "risk_pct_per_trade": 1.0,
        "max_position_pct": 25.0,
        "max_gross_exposure_pct": 100.0,
        "max_concurrent_trades": 3,
    },
}


def _seed(tmp_path):
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    base = datetime(2026, 3, 1)
    # 72h of OHLCV. Price stable at 50000, then crash to 40000 at h60.
    rows = []
    for i in range(72):
        close = 50000.0 if i < 60 else 40000.0
        rows.append({"exchange": "bybit", "symbol": "BTC/USDT",
                     "timeframe": "1h",
                     "ts": base + timedelta(hours=i),
                     "open": close, "high": close * 1.001, "low": close * 0.999,
                     "close": close, "volume": 100.0})
    s.upsert_ohlcv(rows)
    # Two signals: one at h12 (past), one at h48 (future relative to as_of=h24)
    s.upsert_signals([
        {"id": "sig_past", "ts": base + timedelta(hours=12),
         "event_type": "REGIME_CHANGE", "symbol": "BTC/USDT",
         "severity": "warn",
         "payload": "to=trending_up t=10.5"},
        {"id": "sig_future", "ts": base + timedelta(hours=48),
         "event_type": "REGIME_CHANGE", "symbol": "BTC/USDT",
         "severity": "warn",
         "payload": "to=trending_up t=20.0"},
    ])
    # News at h12 (past) and h36 (future relative to as_of=h24)
    s.upsert_news([
        {"id": "n_past", "source": "coindesk", "title": "old btc news",
         "url": "http://x/1", "published_at": base + timedelta(hours=12),
         "fetched_at": base + timedelta(hours=12), "symbols": "BTC"},
        {"id": "n_future", "source": "coindesk", "title": "future btc news",
         "url": "http://x/2", "published_at": base + timedelta(hours=36),
         "fetched_at": base + timedelta(hours=36), "symbols": "BTC"},
    ])
    return s, base


def test_future_signal_invisible_to_advisor(tmp_path):
    src, base = _seed(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=24))

    sigs = c.get_signals(limit=100, event_type="REGIME_CHANGE")
    ids_seen = list(sigs["sig_num"]) if not sigs.empty else []
    # Past signal seen, future signal hidden.
    assert len(ids_seen) == 1
    # And run_auto_advisor's view is also bounded.
    v = run_auto_advisor(c, _CFG, threshold=8.0, window_hours=72)
    # It should consider only the past signal — confidence 10.5 (not 20.0)
    if v["verdict"] == "ENTRY":
        assert v["proposed"]["signal_id"] == int(sigs.iloc[0]["sig_num"])
    src.close()
    c.close()


def test_future_price_invisible_to_latest_close(tmp_path):
    """as_of = h24 (close=50000). Future crash to 40000 at h60 must NOT
    leak into latest_close query."""
    src, base = _seed(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    c.set_as_of(base + timedelta(hours=24))
    row = c._conn.execute(
        "SELECT close FROM ohlcv WHERE exchange='bybit' AND symbol='BTC/USDT' "
        "AND timeframe='1h' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert float(row[0]) == 50000.0  # stable era
    # Advance to h70 (post-crash visible)
    c.set_as_of(base + timedelta(hours=70))
    row = c._conn.execute(
        "SELECT close FROM ohlcv WHERE exchange='bybit' AND symbol='BTC/USDT' "
        "AND timeframe='1h' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    assert float(row[0]) == 40000.0
    src.close()
    c.close()


def test_future_news_invisible_to_build_context(tmp_path):
    src, base = _seed(tmp_path)
    broker = PaperBroker(10000.0)
    c = StorageClamped(source=src, broker=broker)
    cycle_ts = base + timedelta(hours=24)
    c.set_as_of(cycle_ts)
    ctx = build_context(c, _CFG, now_utc=cycle_ts)
    titles = [n.get("title") for n in ctx["news"]]
    assert "old btc news" in titles
    assert "future btc news" not in titles
    src.close()
    c.close()
