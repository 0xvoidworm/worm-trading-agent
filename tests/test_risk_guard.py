"""Tests untuk data_pipe.risk_guard (Step 4)."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.risk_guard import (
    KILLSWITCH_FILE,
    check_and_engage,
    get_daily_pnl_state,
    get_day_start_utc,
    get_equity_start_of_day,
    get_realized_pnl_today,
    get_unrealized_pnl_now,
    is_killswitch_active,
    read_killswitch_reason,
    remove_killswitch,
)
from data_pipe.storage import Storage


_CFG = {
    "exchange": "bybit", "timeframe": "1h",
    "portfolio": {
        "starting_capital": 10000.0,
        "risk_pct_per_trade": 1.0,
        "max_position_pct": 25.0,
        "max_gross_exposure_pct": 100.0,
        "max_concurrent_trades": 3,
        "daily_loss_killswitch_pct": 2.0,
        "daily_loss_warn_pct": 1.0,
    },
}


def _now_today_at(hour: int, minute: int = 0) -> datetime:
    """Helper buat datetime UTC at given hour today (naive)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _open_close(
    s: Storage, pair: str, direction: str,
    entry: float, exit_p: float, size: float = 1.0,
    thesis: str | None = None,
    open_ts: datetime | None = None,
    close_ts: datetime | None = None,
) -> int:
    tid = s.add_trade(pair=pair, direction=direction,
                      entry_price=entry, size=size, thesis=thesis, ts=open_ts)
    s.close_trade(trade_id=tid, exit_price=exit_p, exit_ts=close_ts)
    return tid


def _seed_ohlcv(s: Storage, pair: str, close: float, ts: datetime | None = None):
    """Insert single OHLCV row supaya unrealized PnL bisa dihitung."""
    ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
    s._conn.execute(
        "INSERT OR REPLACE INTO ohlcv "
        "(exchange, symbol, timeframe, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
        ["bybit", pair, "1h", ts, close, close, close, close],
    )


# ─── get_day_start_utc ───────────────────────────────────────────────────

def test_day_start_floors_to_midnight_utc():
    now = datetime(2026, 4, 26, 14, 30, 45, tzinfo=timezone.utc)
    assert get_day_start_utc(now) == datetime(2026, 4, 26, 0, 0, 0)


def test_day_start_at_exactly_midnight_returns_same():
    now = datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc)
    assert get_day_start_utc(now) == datetime(2026, 4, 26, 0, 0, 0)


def test_day_start_at_2359_returns_same_day_midnight():
    now = datetime(2026, 4, 26, 23, 59, 59, tzinfo=timezone.utc)
    assert get_day_start_utc(now) == datetime(2026, 4, 26, 0, 0, 0)


def test_day_start_accepts_naive_datetime():
    now = datetime(2026, 4, 26, 12, 0, 0)
    assert get_day_start_utc(now) == datetime(2026, 4, 26, 0, 0, 0)


# ─── get_equity_start_of_day ─────────────────────────────────────────────

def test_equity_start_no_trades_returns_starting_capital():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            assert get_equity_start_of_day(s, _CFG) == 10000.0


def test_equity_start_includes_pre_day_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
            _open_close(s, "SOL/USDT", "LONG", 100, 110,
                        open_ts=yesterday, close_ts=yesterday + timedelta(hours=1))
            assert get_equity_start_of_day(s, _CFG) == pytest.approx(10010.0)


def test_equity_start_excludes_today_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
            today_close = _now_today_at(8, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 110,
                        open_ts=yesterday, close_ts=yesterday + timedelta(hours=1))
            _open_close(s, "ETH/USDT", "LONG", 1000, 800,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            # Hanya trade kemarin (-200) → exclude. equity_start = 10000 + 10
            assert get_equity_start_of_day(s, _CFG) == pytest.approx(10010.0)


def test_equity_start_excludes_test_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
            _open_close(s, "SOL/USDT", "LONG", 100, 110,
                        open_ts=yesterday, close_ts=yesterday + timedelta(hours=1))
            _open_close(s, "BTC/USDT", "LONG", 1000, 2000,
                        thesis="[TEST_ARTIFACT_X] big fake win",
                        open_ts=yesterday, close_ts=yesterday + timedelta(hours=2))
            # Hanya non-test trade dihitung: 10000 + 10
            assert get_equity_start_of_day(s, _CFG) == pytest.approx(10010.0)


# ─── get_realized_pnl_today ──────────────────────────────────────────────

def test_realized_today_zero_when_no_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            assert get_realized_pnl_today(s) == 0.0


def test_realized_today_excludes_pre_midnight():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            yesterday = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
            _open_close(s, "SOL/USDT", "LONG", 100, 50,
                        open_ts=yesterday, close_ts=yesterday)
            # Closed yesterday → not in today's bucket
            assert get_realized_pnl_today(s) == 0.0


def test_realized_today_includes_today_closes():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            today_close = _now_today_at(10, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 50,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            assert get_realized_pnl_today(s) == pytest.approx(-50.0)


def test_realized_today_excludes_test_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            today_close = _now_today_at(10, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 50,
                        thesis="[TEST_ARTIFACT_LOSS] seeded",
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            assert get_realized_pnl_today(s) == 0.0


# ─── get_unrealized_pnl_now ──────────────────────────────────────────────

def test_unrealized_zero_when_no_open():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            assert get_unrealized_pnl_now(s, _CFG) == 0.0


def test_unrealized_long_profit():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "SOL/USDT", close=110.0)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=2)
            # (110-100) * 2 * +1 = +20
            assert get_unrealized_pnl_now(s, _CFG) == pytest.approx(20.0)


def test_unrealized_long_loss():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "SOL/USDT", close=80.0)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=2)
            # (80-100) * 2 * +1 = -40
            assert get_unrealized_pnl_now(s, _CFG) == pytest.approx(-40.0)


def test_unrealized_short_profit():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "BTC/USDT", close=900.0)
            s.add_trade(pair="BTC/USDT", direction="SHORT", entry_price=1000, size=1)
            # (900-1000) * 1 * -1 = +100
            assert get_unrealized_pnl_now(s, _CFG) == pytest.approx(100.0)


def test_unrealized_short_loss():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "BTC/USDT", close=1100.0)
            s.add_trade(pair="BTC/USDT", direction="SHORT", entry_price=1000, size=1)
            # (1100-1000) * 1 * -1 = -100
            assert get_unrealized_pnl_now(s, _CFG) == pytest.approx(-100.0)


def test_unrealized_mixed_long_short():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "SOL/USDT", close=110.0)
            _seed_ohlcv(s, "BTC/USDT", close=1100.0)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=2)
            s.add_trade(pair="BTC/USDT", direction="SHORT", entry_price=1000, size=1)
            # +20 + (-100) = -80
            assert get_unrealized_pnl_now(s, _CFG) == pytest.approx(-80.0)


def test_unrealized_excludes_test_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "SOL/USDT", close=200.0)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=10,
                        thesis="[TEST_ARTIFACT_X] phantom")
            assert get_unrealized_pnl_now(s, _CFG) == 0.0


def test_unrealized_skips_when_no_ohlcv():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            # No OHLCV row → trade di-skip dari sum
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=1)
            assert get_unrealized_pnl_now(s, _CFG) == 0.0


# ─── get_daily_pnl_state ─────────────────────────────────────────────────

def test_state_status_ok_when_flat():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            st = get_daily_pnl_state(s, _CFG)
            assert st["status"] == "OK"
            assert st["daily_pnl"] == 0.0
            assert st["equity_start"] == 10000.0


def test_state_status_warn_at_minus_1pct():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            # -100 → -1.0% pas threshold WARN
            _open_close(s, "SOL/USDT", "LONG", 100, 50, size=2,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            st = get_daily_pnl_state(s, _CFG)
            assert st["status"] == "WARN"
            assert st["daily_pnl_pct"] == pytest.approx(-1.0)


def test_state_status_trigger_at_minus_2pct():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            # -200 → -2.0% pas threshold TRIGGER
            _open_close(s, "SOL/USDT", "LONG", 100, 50, size=4,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            st = get_daily_pnl_state(s, _CFG)
            assert st["status"] == "TRIGGER"
            assert st["daily_pnl_pct"] == pytest.approx(-2.0)


def test_state_status_ok_just_above_warn():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            # -50 → -0.5% (di atas WARN -1%)
            _open_close(s, "SOL/USDT", "LONG", 100, 50, size=1,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            st = get_daily_pnl_state(s, _CFG)
            assert st["status"] == "OK"


def test_state_includes_unrealized():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv(s, "SOL/USDT", close=80.0)
            s.add_trade(pair="SOL/USDT", direction="LONG", entry_price=100, size=2)
            st = get_daily_pnl_state(s, _CFG)
            # unrealized = -40, daily = -40, pct = -0.4%
            assert st["unrealized_now"] == pytest.approx(-40.0)
            assert st["daily_pnl_pct"] == pytest.approx(-0.4)
            assert st["status"] == "OK"


# ─── check_and_engage ────────────────────────────────────────────────────

def test_engage_writes_killswitch_file_on_trigger():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with Storage(tmp_p / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 25, size=4,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            res = check_and_engage(s, _CFG, tmp_p)
            assert res["status"] == "TRIGGER"
            assert res["engaged"] is True
            assert is_killswitch_active(tmp_p) is True
            payload = json.loads((tmp_p / KILLSWITCH_FILE).read_text())
            assert payload["reason"] == "daily_loss"
            assert payload["daily_pnl_pct"] < -2.0


def test_engage_idempotent_no_double_write():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with Storage(tmp_p / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 25, size=4,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            check_and_engage(s, _CFG, tmp_p)
            first_mtime = (tmp_p / KILLSWITCH_FILE).stat().st_mtime
            # Second call: file already there, engaged should be False
            res2 = check_and_engage(s, _CFG, tmp_p)
            assert res2["engaged"] is False
            assert (tmp_p / KILLSWITCH_FILE).stat().st_mtime == first_mtime


def test_engage_no_file_when_warn_only():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with Storage(tmp_p / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            # -1.5% → WARN but not TRIGGER
            _open_close(s, "SOL/USDT", "LONG", 100, 25, size=2,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            res = check_and_engage(s, _CFG, tmp_p)
            assert res["status"] == "WARN"
            assert is_killswitch_active(tmp_p) is False


def test_engage_emits_warn_signal_once_per_day():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with Storage(tmp_p / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 25, size=2,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            res1 = check_and_engage(s, _CFG, tmp_p)
            res2 = check_and_engage(s, _CFG, tmp_p)
            assert res1["signal_emitted"] is True
            assert res2["signal_emitted"] is False  # dedup
            count = s._conn.execute(
                "SELECT COUNT(*) FROM signal_events WHERE event_type='RISK_WARN'"
            ).fetchone()[0]
            assert count == 1


def test_engage_emits_killswitch_signal_once_per_day():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with Storage(tmp_p / "t.duckdb") as s:
            today_close = _now_today_at(8, 0)
            _open_close(s, "SOL/USDT", "LONG", 100, 25, size=4,
                        open_ts=today_close - timedelta(hours=1),
                        close_ts=today_close)
            check_and_engage(s, _CFG, tmp_p)
            check_and_engage(s, _CFG, tmp_p)
            count = s._conn.execute(
                "SELECT COUNT(*) FROM signal_events WHERE event_type='RISK_KILLSWITCH'"
            ).fetchone()[0]
            assert count == 1


# ─── file helpers ────────────────────────────────────────────────────────

def test_is_killswitch_active_false_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert is_killswitch_active(Path(tmp)) is False


def test_is_killswitch_active_true_when_file_exists():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / KILLSWITCH_FILE).write_text("{}")
        assert is_killswitch_active(Path(tmp)) is True


def test_read_killswitch_reason_returns_payload():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / KILLSWITCH_FILE).write_text(json.dumps({"reason": "x"}))
        assert read_killswitch_reason(Path(tmp)) == {"reason": "x"}


def test_read_killswitch_reason_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        assert read_killswitch_reason(Path(tmp)) is None


def test_remove_killswitch_returns_prev_payload():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / KILLSWITCH_FILE).write_text(json.dumps({"reason": "test"}))
        prev = remove_killswitch(Path(tmp))
        assert prev == {"reason": "test"}
        assert is_killswitch_active(Path(tmp)) is False


def test_remove_killswitch_returns_none_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert remove_killswitch(Path(tmp)) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
