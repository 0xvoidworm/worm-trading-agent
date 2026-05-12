"""Smoke tests untuk storage layer."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage


def _sample_ohlcv(n: int = 10) -> list[dict]:
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    return [
        {
            "exchange": "bybit",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "ts": base_ts + pd.Timedelta(hours=i),
            "open": 70000 + i,
            "high": 70100 + i,
            "low": 69900 + i,
            "close": 70050 + i,
            "volume": 100.0 + i,
        }
        for i in range(n)
    ]


def _sample_funding(n: int = 5) -> list[dict]:
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    return [
        {
            "exchange": "bybit",
            "symbol": "BTC/USDT",
            "ts": base_ts + pd.Timedelta(hours=i * 8),
            "funding": 0.0001 * (i + 1),
            "mark_price": 70000 + i,
        }
        for i in range(n)
    ]


def test_upsert_ohlcv_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        s = Storage(db)
        rows = _sample_ohlcv(10)
        assert s.upsert_ohlcv(rows) == 10
        # re-run: should not duplicate
        assert s.upsert_ohlcv(rows) == 10
        df = s.get_ohlcv("bybit", "BTC/USDT", "1h")
        assert len(df) == 10
        assert df["close"].iloc[0] == 70050.0
        s.close()


def test_upsert_funding_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        s = Storage(db)
        rows = _sample_funding(5)
        assert s.upsert_funding(rows) == 5
        assert s.upsert_funding(rows) == 5
        df = s.get_funding("bybit", "BTC/USDT")
        assert len(df) == 5
        assert abs(df["funding"].iloc[0] - 0.0001) < 1e-9
        s.close()


def test_latest_ts_resume_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        s = Storage(db)
        s.upsert_ohlcv(_sample_ohlcv(10))
        last = s.latest_ohlcv_ts("bybit", "BTC/USDT", "1h")
        assert last is not None
        # expect latest ts = base + 9h
        assert pd.Timestamp(last) == pd.Timestamp("2026-01-01 09:00:00")
        s.close()


def test_stats():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        s = Storage(db)
        s.upsert_ohlcv(_sample_ohlcv(3))
        s.upsert_funding(_sample_funding(2))
        st = s.stats()
        assert st["ohlcv_rows"] == 3
        assert st["funding_rows"] == 2
        assert st["trades_open"] == 0
        assert "BTC/USDT" in st["symbols"]
        s.close()


# --- trade journal ---

def test_add_trade_returns_id_and_persists():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            tid = s.add_trade(
                pair="SOL/USDT",
                direction="LONG",
                entry_price=145.2,
                size=10.0,
                thesis="regime shift",
            )
            assert isinstance(tid, int) and tid >= 1
            df = s.get_trades(status="OPEN")
            assert len(df) == 1
            assert df.iloc[0]["pair"] == "SOL/USDT"
            assert df.iloc[0]["status"] == "OPEN"


def test_add_trade_rejects_invalid_direction():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            with pytest.raises(ValueError):
                s.add_trade(pair="BTC/USDT", direction="INVALID")


def test_close_long_computes_positive_pnl():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            tid = s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100.0, size=5.0)
            res = s.close_trade(tid, exit_price=110.0, notes="tp")
            # (110 - 100) * 5 = 50
            assert res["pnl"] == pytest.approx(50.0)
            df = s.get_trades(status="OPEN")
            assert df.empty
            df_all = s.get_trades(status=None)
            assert len(df_all) == 1
            assert df_all.iloc[0]["status"] == "CLOSED"


def test_close_short_inverts_pnl_sign():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            tid = s.add_trade(pair="ETH/USDT", direction="SHORT", entry_price=3000.0, size=2.0)
            res = s.close_trade(tid, exit_price=2800.0)
            # (3000 - 2800) * 2 = 400 profit for SHORT
            assert res["pnl"] == pytest.approx(400.0)


def test_close_pair_skips_auto_pnl():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            tid = s.add_trade(pair="SOL/USDT|ETH/USDT", direction="PAIR", size=1.0)
            res = s.close_trade(tid, exit_price=0.5, notes="spread converged")
            assert res["pnl"] is None


def test_close_trade_not_found_raises():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            with pytest.raises(ValueError, match="not found"):
                s.close_trade(999, exit_price=100.0)


def test_close_trade_already_closed_raises():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            tid = s.add_trade(pair="BTC/USDT", direction="LONG", entry_price=70000, size=1)
            s.close_trade(tid, exit_price=72000)
            with pytest.raises(ValueError, match="already CLOSED"):
                s.close_trade(tid, exit_price=72000)


def test_get_trades_filters_by_status():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            t1 = s.add_trade(pair="BTC/USDT", direction="LONG", entry_price=70000, size=1)
            t2 = s.add_trade(pair="ETH/USDT", direction="SHORT", entry_price=3000, size=2)
            s.close_trade(t1, exit_price=71000)

            open_df = s.get_trades(status="OPEN")
            closed_df = s.get_trades(status="CLOSED")
            all_df = s.get_trades(status=None)
            assert len(open_df) == 1
            assert len(closed_df) == 1
            assert len(all_df) == 2
            assert int(open_df.iloc[0]["id"]) == t2


# --- signal surrogate id + trade linkage ---

def _sample_signals(n: int = 3) -> list[dict]:
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    return [
        {
            "id": f"hash-{i:04d}",
            "ts": base_ts + pd.Timedelta(hours=i),
            "event_type": "REGIME_CHANGE",
            "symbol": "BTC/USDT",
            "severity": "info",
            "payload": f"from=ranging;to=trending_up;t={i}",
        }
        for i in range(n)
    ]


def test_sig_num_auto_assigned_and_stable_across_upsert():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            sigs = _sample_signals(3)
            assert s.upsert_signals(sigs) == 3
            df1 = s.get_signals(limit=10)
            # pastikan sig_num terisi integer, unique, 1..n
            assert df1["sig_num"].notna().all()
            assert set(df1["sig_num"].astype(int).tolist()) == {1, 2, 3}

            # re-upsert same rows → sig_num tetap (ON CONFLICT DO NOTHING)
            s.upsert_signals(sigs)
            df2 = s.get_signals(limit=10)
            # same id → same sig_num
            m1 = dict(zip(df1["sig_num"].astype(int), df1["ts"]))
            m2 = dict(zip(df2["sig_num"].astype(int), df2["ts"]))
            assert m1 == m2


def test_get_signal_by_num_returns_row_or_none():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            s.upsert_signals(_sample_signals(2))
            sig1 = s.get_signal_by_num(1)
            assert sig1 is not None
            assert sig1["event_type"] == "REGIME_CHANGE"
            assert s.get_signal_by_num(999) is None


def test_add_trade_with_signal_id_validates_exists():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            s.upsert_signals(_sample_signals(2))
            # valid link
            tid = s.add_trade(
                pair="BTC/USDT", direction="LONG", entry_price=70000, size=1,
                signal_id=1,
            )
            df = s.get_trades(status="OPEN")
            assert int(df.iloc[0]["signal_id"]) == 1

            # invalid link raises
            with pytest.raises(ValueError, match="not found"):
                s.add_trade(
                    pair="ETH/USDT", direction="LONG", entry_price=3000, size=1,
                    signal_id=999,
                )


def test_journal_stats_empty_db():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            st = s.journal_stats()
            assert st["open"] == 0
            assert st["closed"] == 0
            assert st["scored"] == 0
            assert st["win_rate"] == 0.0
            assert st["total_pnl"] == 0.0
            assert st["best"] is None
            assert st["worst"] is None


def test_journal_stats_mixed_wins_and_losses():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            # 2 wins, 1 loss, 1 pair (skipped from scored)
            t1 = s.add_trade(pair="BTC/USDT", direction="LONG", entry_price=70000, size=1)
            s.close_trade(t1, exit_price=72000)  # +2000
            t2 = s.add_trade(pair="ETH/USDT", direction="SHORT", entry_price=3000, size=2)
            s.close_trade(t2, exit_price=2900)   # +200
            t3 = s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=150, size=10)
            s.close_trade(t3, exit_price=140)    # -100
            t4 = s.add_trade(pair="SOL|ETH", direction="PAIR", size=1)
            s.close_trade(t4, exit_price=0.5)    # pnl None

            st = s.journal_stats()
            assert st["closed"] == 4
            assert st["scored"] == 3
            assert st["closed_no_pnl"] == 1
            assert st["wins"] == 2
            assert st["losses"] == 1
            assert st["win_rate"] == pytest.approx(2/3)
            assert st["total_pnl"] == pytest.approx(2100.0)
            assert st["best"]["id"] == t1
            assert st["best"]["pnl"] == pytest.approx(2000.0)
            assert st["worst"]["id"] == t3
            assert st["worst"]["pnl"] == pytest.approx(-100.0)


# --- retrospective context helpers ---


def _sample_news(center_ts: pd.Timestamp, offsets_hours: list[float]) -> list[dict]:
    return [
        {
            "id": f"n-{i}",
            "source": "test",
            "title": f"headline {i}",
            "url": f"https://x/{i}",
            "published_at": center_ts + pd.Timedelta(hours=h),
            "fetched_at": center_ts + pd.Timedelta(hours=h),
            "symbols": "BTC",
        }
        for i, h in enumerate(offsets_hours)
    ]


def test_get_ohlcv_around_window_shape():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            s.upsert_ohlcv(_sample_ohlcv(30))
            # center @ bar 15 (2026-01-01 15:00)
            center = pd.Timestamp("2026-01-01 15:00:00")
            ctx = s.get_ohlcv_around(
                "bybit", "BTC/USDT", "1h", center, before=5, after=5,
            )
            assert ctx["signal"] is not None
            assert pd.Timestamp(ctx["signal"]["ts"]) == center
            assert len(ctx["before"]) == 5
            assert len(ctx["after"]) == 5
            # before harus ts ascending & semuanya < center
            assert (ctx["before"]["ts"] < center).all()
            assert (ctx["after"]["ts"] > center).all()


def test_get_ohlcv_around_clips_at_edges():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            s.upsert_ohlcv(_sample_ohlcv(10))
            # center @ bar 0 — no `before` bars available
            center = pd.Timestamp("2026-01-01 00:00:00")
            ctx = s.get_ohlcv_around(
                "bybit", "BTC/USDT", "1h", center, before=5, after=5,
            )
            assert ctx["signal"] is not None
            assert len(ctx["before"]) == 0
            assert len(ctx["after"]) == 5


def test_get_ohlcv_around_returns_empty_when_no_data():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            center = pd.Timestamp("2026-01-01 00:00:00")
            ctx = s.get_ohlcv_around("bybit", "XXX/USDT", "1h", center)
            assert ctx["signal"] is None
            assert ctx["before"].empty
            assert ctx["after"].empty


def test_get_news_in_window_filters_by_time():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            center = pd.Timestamp("2026-01-01 12:00:00")
            # In-window: -2h, 0h, +2h ; out-of-window: -5h, +5h
            s.upsert_news(_sample_news(center, [-5, -2, 0, 2, 5]))
            df = s.get_news_in_window(center, before_hours=3, after_hours=3)
            assert len(df) == 3


def test_get_trades_by_signal_returns_linked_only():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.duckdb"
        with Storage(db) as s:
            s.upsert_signals(_sample_signals(3))
            t1 = s.add_trade(
                pair="BTC/USDT", direction="LONG", entry_price=70000, size=1,
                signal_id=1,
            )
            s.add_trade(
                pair="ETH/USDT", direction="LONG", entry_price=3000, size=1,
                signal_id=2,
            )
            # Unlinked trade (should NOT appear)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=150, size=1)

            df = s.get_trades_by_signal(1)
            assert len(df) == 1
            assert int(df.iloc[0]["id"]) == t1

            assert s.get_trades_by_signal(999).empty


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
