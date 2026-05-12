"""Tests untuk data_pipe.auto_exit (Step 2)."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.auto_exit import check_exits
from data_pipe.storage import Storage


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["SOL/USDT", "BTC/USDT", "ETH/USDT"],
    "tier2": [],
    "exits": {
        "stop_loss_pct": 5.0,
        "take_profit_pct": 5.0,
        "max_hold_hours": 72,
        "regime_flip_t_threshold": 6.0,
        "window_hours": 4,
    },
}


# ─── helpers ──────────────────────────────────────────────────────────────

def _seed_ohlcv_close(s: Storage, symbol: str, close: float):
    """Seed satu bar OHLCV dengan close price tertentu pada hour-floor terkini."""
    now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
    s.upsert_ohlcv([{
        "exchange": "bybit", "symbol": symbol, "timeframe": "1h",
        "ts": now, "open": close, "high": close, "low": close,
        "close": close, "volume": 1.0,
    }])
    return now


def _open_trade(
    s: Storage, pair: str, direction: str, entry_price: float,
    size: float = 1.0, age_hours: float = 1.0,
) -> int:
    """Buka trade OPEN dengan ts = sekarang - age_hours."""
    ts = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(hours=age_hours)
    )
    return s.add_trade(
        pair=pair, direction=direction, entry_price=entry_price,
        size=size, ts=ts,
    )


def _inject_regime_signal(
    s: Storage, sig_id: str, symbol: str, direction: str,
    t_stat: float = 7.0, age_hours: float = 0.5,
):
    """Inject REGIME_CHANGE signal ke arah direction (LONG/SHORT)."""
    ts = pd.Timestamp.now("UTC").tz_localize(None) - pd.Timedelta(hours=age_hours)
    label = "trending_up" if direction == "LONG" else "trending_down"
    s.upsert_signals([{
        "id": sig_id, "ts": ts, "event_type": "REGIME_CHANGE",
        "symbol": symbol, "severity": "warn",
        "payload": f"from=ranging;to={label};t={t_stat:.2f}",
    }])


# ─── empty / no-op cases ──────────────────────────────────────────────────

def test_check_exits_returns_none_when_no_open_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            assert check_exits(s, _CFG) is None


def test_check_exits_returns_none_when_all_healthy():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 100.0)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0, age_hours=1.0)
            assert check_exits(s, _CFG) is None


def test_pair_direction_is_skipped():
    """PAIR trade tidak punya semantic exit auto."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 50.0)
            # PAIR trade dengan entry None (sah utk PAIR)
            s.add_trade(pair="SOL/USDT|ETH/USDT", direction="PAIR",
                        entry_price=None, size=100)
            assert check_exits(s, _CFG) is None


def test_trade_without_entry_price_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 50.0)
            s.add_trade(pair="SOL/USDT", direction="LONG",
                        entry_price=None, size=1)
            assert check_exits(s, _CFG) is None


# ─── trigger A: SL ────────────────────────────────────────────────────────

def test_sl_fires_on_long_drawdown():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 90.0)  # -10% from 100
            tid = _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["verdict"] == "EXIT"
            assert v["trade_id"] == tid
            assert "SL hit" in v["thesis"]
            assert v["summary"] == "Bearish"


def test_sl_fires_on_short_pump():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 110.0)  # +10% (bad for SHORT)
            tid = _open_trade(s, "SOL/USDT", "SHORT", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "SL hit" in v["thesis"]
            assert v["summary"] == "Bullish"


def test_sl_does_not_fire_when_drawdown_within_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 96.0)  # -4% (above -5% SL)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            assert check_exits(s, _CFG) is None


# ─── trigger B: regime flip ───────────────────────────────────────────────

def test_regime_flip_fires_on_long_when_signal_turns_down():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 99.0)  # -1% only, no SL
            tid = _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            _inject_regime_signal(
                s, "flip", "SOL/USDT", direction="SHORT", t_stat=7.5,
            )
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "regime flipped to trending_down" in v["thesis"]
            assert "t=7.50" in v["thesis"]
            assert v["summary"] == "Bearish"


def test_regime_flip_fires_on_short_when_signal_turns_up():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 101.0)
            tid = _open_trade(s, "SOL/USDT", "SHORT", entry_price=100.0)
            _inject_regime_signal(
                s, "flip", "SOL/USDT", direction="LONG", t_stat=8.0,
            )
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "trending_up" in v["thesis"]
            assert v["summary"] == "Bullish"


def test_regime_flip_skipped_when_t_below_exit_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 99.0)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            # opposite direction tapi t=5.0 < threshold 6.0 → no fire
            _inject_regime_signal(
                s, "weak", "SOL/USDT", direction="SHORT", t_stat=5.0,
            )
            assert check_exits(s, _CFG) is None


def test_regime_flip_skipped_when_signal_aligns_with_trade():
    """Signal arahnya sama dengan trade direction → bukan flip, no exit."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 99.0)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            _inject_regime_signal(
                s, "ok", "SOL/USDT", direction="LONG", t_stat=10.0,
            )
            assert check_exits(s, _CFG) is None


def test_regime_flip_skipped_when_signal_outside_window():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 99.0)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            # signal 5h lalu, window 4h → di luar
            _inject_regime_signal(
                s, "old", "SOL/USDT", direction="SHORT",
                t_stat=10.0, age_hours=5.0,
            )
            assert check_exits(s, _CFG) is None


# ─── trigger C: TP ────────────────────────────────────────────────────────

def test_tp_fires_on_long_profit():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 110.0)  # +10%
            tid = _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "TP hit" in v["thesis"]
            assert v["summary"] == "Bullish"


def test_tp_fires_on_short_profit():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 90.0)  # -10% price = +10% for SHORT
            tid = _open_trade(s, "SOL/USDT", "SHORT", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "TP hit" in v["thesis"]
            assert v["summary"] == "Bearish"


# ─── trigger D: time-stop ─────────────────────────────────────────────────

def test_time_stop_fires_on_old_trade():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 100.5)  # ~+0.5% — no SL/TP
            tid = _open_trade(
                s, "SOL/USDT", "LONG", entry_price=100.0, age_hours=80.0,
            )
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == tid
            assert "time-stop" in v["thesis"]
            assert v["summary"] == "Chaos"


# ─── priority resolution ──────────────────────────────────────────────────

def test_sl_beats_regime_flip_when_both_fire_same_trade():
    """Trade hits both SL AND regime flip → SL wins (higher priority)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 90.0)  # -10% (SL)
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            _inject_regime_signal(
                s, "flip", "SOL/USDT", direction="SHORT", t_stat=10.0,
            )
            v = check_exits(s, _CFG)
            assert v is not None
            assert "SL hit" in v["thesis"]
            assert "regime" not in v["thesis"]


def test_priority_across_trades_sl_wins_over_tp():
    """Trade A hits TP, Trade B hits SL → SL trade wins."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "BTC/USDT", 110.0)  # TP for BTC LONG
            _seed_ohlcv_close(s, "ETH/USDT", 90.0)   # SL for ETH LONG
            tp_id = _open_trade(s, "BTC/USDT", "LONG", entry_price=100.0)
            sl_id = _open_trade(s, "ETH/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == sl_id
            assert "SL hit" in v["thesis"]
            assert v["trade_id"] != tp_id


def test_tiebreak_by_largest_pnl_pct():
    """2 trade hit SL → trade dengan |pnl_pct| terbesar menang."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "BTC/USDT", 92.0)   # -8%
            _seed_ohlcv_close(s, "ETH/USDT", 80.0)   # -20%
            _open_trade(s, "BTC/USDT", "LONG", entry_price=100.0)
            big_sl = _open_trade(s, "ETH/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None
            assert v["trade_id"] == big_sl


# ─── direction math ──────────────────────────────────────────────────────

def test_pnl_pct_long_vs_short_correct():
    """LONG -5% from 100 = trigger SL; SHORT +5% = same."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 95.0)  # -5%
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, _CFG)
            # exact -5% → kondisi <= -SL_PCT, fire
            assert v is not None and "SL hit" in v["thesis"]

    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 105.0)  # +5% bad for short
            _open_trade(s, "SOL/USDT", "SHORT", entry_price=100.0)
            v = check_exits(s, _CFG)
            assert v is not None and "SL hit" in v["thesis"]


# ─── config override ──────────────────────────────────────────────────────

def test_custom_sl_threshold_via_cfg():
    cfg2 = {**_CFG, "exits": {**_CFG["exits"], "stop_loss_pct": 2.0}}
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_ohlcv_close(s, "SOL/USDT", 97.0)  # -3% → fires when SL=2%
            _open_trade(s, "SOL/USDT", "LONG", entry_price=100.0)
            v = check_exits(s, cfg2)
            assert v is not None and "SL hit" in v["thesis"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
