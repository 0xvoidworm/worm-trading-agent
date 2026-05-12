"""Tests for paper_broker.PaperBroker — fee math, lifecycle, equity."""
from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from data_pipe.paper_broker import DEFAULT_FEE_PCT, PaperBroker


def _broker_with_conn(starting=10000.0, fee_pct=DEFAULT_FEE_PCT):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SEQUENCE seq_trade_journal_id START 1")
    conn.execute(
        "CREATE TABLE trade_journal ("
        "  id INTEGER NOT NULL DEFAULT nextval('seq_trade_journal_id'),"
        "  ts TIMESTAMP NOT NULL, pair VARCHAR NOT NULL,"
        "  direction VARCHAR NOT NULL, entry_price DOUBLE, size DOUBLE,"
        "  thesis VARCHAR, status VARCHAR NOT NULL DEFAULT 'OPEN',"
        "  exit_ts TIMESTAMP, exit_price DOUBLE, pnl DOUBLE, notes VARCHAR,"
        "  signal_id INTEGER, PRIMARY KEY (id))"
    )
    b = PaperBroker(starting_capital=starting, fee_pct=fee_pct)
    b.attach_conn(conn)
    return b, conn


def test_attach_required_before_open():
    b = PaperBroker(10000.0)
    with pytest.raises(RuntimeError, match="attach_conn"):
        b.open_position("BTC/USDT", "LONG", 0.1, 50000.0)


def test_open_position_returns_id_and_persists():
    b, _ = _broker_with_conn()
    tid = b.open_position("BTC/USDT", "LONG", size=0.1,
                          entry_price=50000.0,
                          ts=datetime(2026, 3, 1, 10))
    assert isinstance(tid, int) and tid >= 1
    df = b.get_open_positions()
    assert len(df) == 1
    assert df.iloc[0]["pair"] == "BTC/USDT"
    assert float(df.iloc[0]["entry_price"]) == 50000.0


def test_invalid_inputs_rejected():
    b, _ = _broker_with_conn()
    with pytest.raises(ValueError):
        b.open_position("BTC/USDT", "INVALID", 0.1, 50000.0)
    with pytest.raises(ValueError):
        b.open_position("BTC/USDT", "LONG", 0, 50000.0)
    with pytest.raises(ValueError):
        b.open_position("BTC/USDT", "LONG", 0.1, 0)


def test_long_close_pnl_with_fee():
    """LONG: entry 100, exit 110, size 1 → gross +10. Fee 0.11% × (100+110) = 0.231.
    Net PnL = 10 - 0.231 = 9.769."""
    b, _ = _broker_with_conn()
    tid = b.open_position("X/USDT", "LONG", size=1.0, entry_price=100.0,
                          ts=datetime(2026, 3, 1))
    res = b.close_position(tid, exit_price=110.0, exit_reason="TP",
                           ts=datetime(2026, 3, 2))
    assert res["pnl"] == pytest.approx(9.769, abs=1e-6)


def test_short_close_pnl_with_fee():
    """SHORT: entry 100, exit 90, size 1 → gross +10. Fee 0.11% × (100+90) = 0.209."""
    b, _ = _broker_with_conn()
    tid = b.open_position("X/USDT", "SHORT", size=1.0, entry_price=100.0,
                          ts=datetime(2026, 3, 1))
    res = b.close_position(tid, exit_price=90.0, exit_reason="TP",
                           ts=datetime(2026, 3, 2))
    assert res["pnl"] == pytest.approx(9.791, abs=1e-6)


def test_close_unknown_or_double_close_raises():
    b, _ = _broker_with_conn()
    with pytest.raises(ValueError, match="not found"):
        b.close_position(999, exit_price=100.0, exit_reason="x")
    tid = b.open_position("X/USDT", "LONG", 1.0, 100.0,
                          ts=datetime(2026, 3, 1))
    b.close_position(tid, exit_price=110.0, exit_reason="TP")
    with pytest.raises(ValueError, match="already CLOSED"):
        b.close_position(tid, exit_price=110.0, exit_reason="TP")


def test_equity_now_combines_realized():
    b, _ = _broker_with_conn(starting=10000.0)
    assert b.equity_now() == 10000.0
    t1 = b.open_position("X/USDT", "LONG", 1.0, 100.0,
                         ts=datetime(2026, 3, 1))
    b.close_position(t1, exit_price=110.0, exit_reason="TP")  # +9.769
    t2 = b.open_position("Y/USDT", "LONG", 1.0, 200.0,
                         ts=datetime(2026, 3, 2))
    b.close_position(t2, exit_price=180.0, exit_reason="SL")  # gross -20, fee 0.418
    eq = b.equity_now()
    # +9.769 + (-20.418) = -10.649
    assert eq == pytest.approx(10000.0 + 9.769 - 20.418, abs=1e-6)


def test_pair_direction_skips_pnl_calc():
    b, _ = _broker_with_conn()
    tid = b.open_position("A|B", "PAIR", size=1.0, entry_price=1.5,
                          ts=datetime(2026, 3, 1))
    res = b.close_position(tid, exit_price=2.0, exit_reason="manual")
    assert res["pnl"] is None  # skip auto-calc for PAIR


def test_reset_state_clears_trades():
    b, _ = _broker_with_conn()
    b.open_position("X/USDT", "LONG", 1.0, 100.0, ts=datetime(2026, 3, 1))
    assert len(b.get_open_positions()) == 1
    b.reset_state()
    assert len(b.get_open_positions()) == 0
    assert b.get_killswitch_state() is None


def test_killswitch_cache_accessor():
    b, _ = _broker_with_conn()
    assert b.get_killswitch_state() is None
    b.set_killswitch_state({"reason": "daily_loss", "pct": -2.5})
    assert b.get_killswitch_state()["reason"] == "daily_loss"


def test_apply_fee_static():
    assert PaperBroker.apply_fee(1000.0) == pytest.approx(1.1, abs=1e-9)
    assert PaperBroker.apply_fee(1000.0, fee_pct=0.05) == pytest.approx(0.5, abs=1e-9)
    assert PaperBroker.apply_fee(-500.0) == pytest.approx(0.55, abs=1e-9)


# ─── Step 17.A3: slippage tests ──────────────────────────────────────────────


def test_slippage_default_zero_preserves_legacy_pnl():
    """Default slippage_bps_per_side=0 → PnL identik pre-A3."""
    b = PaperBroker(starting_capital=10000.0)
    assert b.slippage_bps_per_side == 0.0


def test_long_close_with_slippage_10bps():
    """LONG entry 100, exit 110, size 1, fee 0.11%, slippage 10bps/side.

    gross = 10.0
    open_notional = 100, close_notional = 110, sum = 210
    fee = 210 × 0.0011 = 0.231
    slippage = 210 × 0.001 = 0.21 (10bps = 0.10%)
    pnl = 10 - 0.231 - 0.21 = 9.559
    """
    b, _ = _broker_with_conn()
    b.slippage_bps_per_side = 10.0
    tid = b.open_position("X/USDT", "LONG", size=1.0, entry_price=100.0,
                          ts=datetime(2026, 3, 1))
    res = b.close_position(tid, exit_price=110.0, exit_reason="TP",
                           ts=datetime(2026, 3, 2))
    assert res["pnl"] == pytest.approx(9.559, abs=1e-6)


def test_short_close_with_slippage_20bps():
    """SHORT entry 100, exit 90, size 1, fee 0.11%, slippage 20bps/side.

    gross = 10.0
    open_notional = 100, close_notional = 90, sum = 190
    fee = 190 × 0.0011 = 0.209
    slippage = 190 × 0.002 = 0.38 (20bps = 0.20%)
    pnl = 10 - 0.209 - 0.38 = 9.411
    """
    b, _ = _broker_with_conn()
    b.slippage_bps_per_side = 20.0
    tid = b.open_position("X/USDT", "SHORT", size=1.0, entry_price=100.0,
                          ts=datetime(2026, 3, 1))
    res = b.close_position(tid, exit_price=90.0, exit_reason="TP",
                           ts=datetime(2026, 3, 2))
    assert res["pnl"] == pytest.approx(9.411, abs=1e-6)


def test_apply_slippage_static():
    """Static helper: apply_slippage(notional, bps_per_side)."""
    assert PaperBroker.apply_slippage(1000.0, bps_per_side=10.0) == pytest.approx(1.0, abs=1e-9)
    assert PaperBroker.apply_slippage(1000.0, bps_per_side=20.0) == pytest.approx(2.0, abs=1e-9)
    assert PaperBroker.apply_slippage(0.0, bps_per_side=10.0) == 0.0
    assert PaperBroker.apply_slippage(-500.0, bps_per_side=10.0) == pytest.approx(0.5, abs=1e-9)


def test_slippage_via_constructor():
    """Konstruktor parameter slippage_bps_per_side → instance attribute."""
    b = PaperBroker(starting_capital=10000.0, slippage_bps_per_side=15.0)
    assert b.slippage_bps_per_side == 15.0
