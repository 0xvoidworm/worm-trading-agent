"""Tests untuk data_pipe.advisor — context builder + verdict persistence."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.advisor import (
    _extract_tickers,
    apply_verdict,
    build_context,
    read_latest_verdict,
    validate_verdict,
)
from data_pipe.storage import Storage


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT", "SOL/USDT"],
    "tier2": [],
}


def _seed_healthy(s: Storage) -> pd.Timestamp:
    """Populate DB supaya health=OK. Return timestamp 'now' yang dipakai."""
    now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
    # Fresh OHLCV dengan bar berakhir di `now`
    rows = []
    for sym in _CFG["tier1"]:
        rows.extend([
            {
                "exchange": "bybit", "symbol": sym, "timeframe": "1h",
                "ts": now - pd.Timedelta(hours=(9 - i)),
                "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
                "volume": 1.0,
            }
            for i in range(10)
        ])
    s.upsert_ohlcv(rows)
    # Funding fresh untuk kedua symbol
    s.upsert_funding([
        {"exchange": "bybit", "symbol": sym, "ts": now,
         "funding": 0.0001, "mark_price": 100}
        for sym in _CFG["tier1"]
    ])
    # Satu berita per feed supaya feed counter hidup
    from data_pipe.ingest_news import FEEDS
    now_dt = datetime.now(timezone.utc)
    s.upsert_news([
        {
            "id": f"n-{i}", "source": name, "title": f"headline {i}", "url": f"u{i}",
            "published_at": now_dt - timedelta(hours=1), "fetched_at": now_dt,
            "symbols": "BTC,SOL",
        }
        for i, (name, _) in enumerate(FEEDS)
    ])
    return now


def test_extract_tickers_handles_pair_and_none():
    assert _extract_tickers(None) == []
    assert _extract_tickers("SOL/USDT") == ["SOL"]
    assert _extract_tickers("SOL/USDT|ETH/USDT") == ["SOL", "ETH"]
    assert _extract_tickers("BTC/USDT") == ["BTC"]


def test_extract_tickers_handles_nan_and_non_string():
    """Regression: signals dengan symbol=NULL (e.g. RISK_KILLSWITCH pasca
    Step 4) → pandas DataFrame return float('nan'). Sebelum fix:
    `not float('nan')` == False, jadi guard miss, lalu `.split('|')` crash
    AttributeError. Setelah fix: silent degrade ke [].
    """
    import math

    import numpy as np

    assert _extract_tickers(math.nan) == []
    assert _extract_tickers(np.nan) == []
    assert _extract_tickers(float("nan")) == []
    assert _extract_tickers(123.45) == []      # any float
    assert _extract_tickers(42) == []          # any int
    assert _extract_tickers(pd.NA) == []       # pandas NA sentinel
    assert _extract_tickers("   ") == []       # whitespace-only string
    assert _extract_tickers("") == []          # empty string


def test_build_context_survives_signal_with_null_symbol():
    """End-to-end regression: insert RISK_KILLSWITCH signal (symbol=NULL),
    pastikan build_context tidak crash dan return signal dengan
    symbols_of_interest yang TIDAK include garbage entry.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            now = _seed_healthy(s)
            base_ts = pd.Timestamp(now).tz_localize(None)
            sigs = [
                {"id": "risk-1", "ts": base_ts,
                 "event_type": "RISK_KILLSWITCH", "symbol": None,
                 "severity": "critical", "payload": "daily_pnl_pct=-2.5"},
                {"id": "regime-1", "ts": base_ts - pd.Timedelta(hours=1),
                 "event_type": "REGIME_CHANGE", "symbol": "SOL/USDT",
                 "severity": "info", "payload": "t=12.0"},
            ]
            s.upsert_signals(sigs)

            ctx = build_context(s, _CFG)
            assert ctx["health"]["status"] == "OK"
            assert len(ctx["signals"]) == 2
            # SOL ada (dari REGIME_CHANGE), tidak ada NaN/garbage entry
            assert "SOL" in ctx["symbols_of_interest"]
            assert all(isinstance(t, str) and t for t in ctx["symbols_of_interest"])


def test_build_context_collects_signals_trades_news():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            now = _seed_healthy(s)
            # 3 sinyal terbaru (tambah 1 tua yang tidak masuk)
            base_ts = pd.Timestamp(now).tz_localize(None)
            sigs = [
                {"id": f"sig-{i}", "ts": base_ts - pd.Timedelta(hours=i),
                 "event_type": "REGIME_CHANGE", "symbol": sym,
                 "severity": "info", "payload": f"t={i}"}
                for i, sym in enumerate(["SOL/USDT", "BTC/USDT", "SOL/USDT", "ETH/USDT"])
            ]
            s.upsert_signals(sigs)
            # Open trade linked ke signal
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=1)

            ctx = build_context(s, _CFG)
            assert ctx["health"]["status"] == "OK"
            assert len(ctx["signals"]) == 3
            # Terbaru dulu (ts desc)
            ts_list = [pd.Timestamp(s["ts"]) for s in ctx["signals"]]
            assert ts_list == sorted(ts_list, reverse=True)
            assert len(ctx["open_trades"]) == 1
            assert ctx["open_trades"][0]["pair"] == "SOL/USDT"
            assert "SOL" in ctx["symbols_of_interest"]
            assert "BTC" in ctx["symbols_of_interest"]
            # news max 5
            assert len(ctx["news"]) <= 5


def test_build_context_news_fallback_when_no_signal_tickers():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            _seed_healthy(s)
            # Tidak insert signal apa pun
            ctx = build_context(s, _CFG)
            assert ctx["signals"] == []
            assert ctx["symbols_of_interest"] == []
            # Harus jatuh balik ke 5 news terbaru
            assert len(ctx["news"]) == 5


def test_validate_verdict_rejects_bad_schema():
    with pytest.raises(ValueError):
        validate_verdict({})
    with pytest.raises(ValueError):
        validate_verdict({"summary": "X", "verdict": "WAIT", "thesis": "ok"})
    with pytest.raises(ValueError):
        validate_verdict({"summary": "Bullish", "verdict": "BUY", "thesis": "ok"})
    with pytest.raises(ValueError):
        validate_verdict({"summary": "Bullish", "verdict": "EXIT", "thesis": "x"})
    # happy path
    validate_verdict({"summary": "Bullish", "verdict": "WAIT", "thesis": "ok"})
    validate_verdict({"summary": "Bearish", "verdict": "EXIT", "thesis": "ok", "trade_id": 3})


def test_apply_verdict_persists_latest_and_appends_log():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        ctx = {"health": {"status": "OK"}, "signals": [], "open_trades": [],
               "symbols_of_interest": ["BTC"]}
        v = {"summary": "Bullish", "verdict": "WAIT", "thesis": "gap trend belum confirmed"}
        path = apply_verdict(v, ctx, data_dir)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["verdict"]["verdict"] == "WAIT"
        assert payload["context_snapshot"]["health_status"] == "OK"
        assert payload["context_snapshot"]["symbols_of_interest"] == ["BTC"]

        log = data_dir / "advisor.log"
        assert log.exists()
        line = log.read_text()
        assert "WAIT" in line
        assert "Bullish" in line

        # Apply kedua → log append, latest overwrite
        v2 = {"summary": "Bearish", "verdict": "EXIT", "thesis": "stop loss hit",
              "trade_id": 7}
        apply_verdict(v2, ctx, data_dir)
        payload2 = json.loads(path.read_text())
        assert payload2["verdict"]["verdict"] == "EXIT"
        assert payload2["verdict"]["trade_id"] == 7
        assert len(log.read_text().splitlines()) == 2


def test_read_latest_verdict_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        assert read_latest_verdict(Path(tmp)) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
