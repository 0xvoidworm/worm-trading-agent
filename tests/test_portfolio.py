"""Tests untuk data_pipe.portfolio (Step 3)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.portfolio import (
    DEFAULT_STARTING_CAPITAL,
    get_account_state,
    get_equity,
    get_open_exposure,
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
    },
}


def _open_close(s: Storage, pair: str, direction: str,
                entry: float, exit_p: float, size: float = 1.0,
                thesis: str | None = None) -> int:
    tid = s.add_trade(pair=pair, direction=direction,
                      entry_price=entry, size=size, thesis=thesis)
    s.close_trade(trade_id=tid, exit_price=exit_p)
    return tid


# ─── get_equity ──────────────────────────────────────────────────────────

def test_equity_equals_starting_when_no_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            assert get_equity(s, _CFG) == 10000.0


def test_equity_default_starting_when_no_cfg_block():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            cfg_no_portfolio = {"exchange": "bybit", "timeframe": "1h"}
            assert get_equity(s, cfg_no_portfolio) == DEFAULT_STARTING_CAPITAL


def test_equity_includes_realized_pnl():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _open_close(s, "SOL/USDT", "LONG", 100, 110)  # +10
            _open_close(s, "BTC/USDT", "SHORT", 1000, 950)  # +50
            assert get_equity(s, _CFG) == pytest.approx(10060.0)


def test_equity_subtracts_losses():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _open_close(s, "SOL/USDT", "LONG", 100, 80)  # -20
            assert get_equity(s, _CFG) == pytest.approx(9980.0)


def test_equity_excludes_test_artifacts():
    """Trade dengan thesis prefix [TEST_ARTIFACT* tidak dihitung."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            # real trade -100
            _open_close(s, "SOL/USDT", "LONG", 100, 90,
                        thesis="real signal entry")
            # test artifact +500 → tidak boleh masuk hitungan
            _open_close(s, "ETH/USDT", "LONG", 1000, 1500,
                        thesis="[TEST_ARTIFACT_SL] live verification")
            # capital = 10000 + (-10) [exclude +500] = 9990
            assert get_equity(s, _CFG) == pytest.approx(9990.0)


# ─── get_open_exposure ──────────────────────────────────────────────────

def test_exposure_zero_when_no_open():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            ex = get_open_exposure(s)
            assert ex["gross_notional"] == 0.0
            assert ex["count"] == 0
            assert ex["by_pair"] == {}


def test_exposure_long_trade():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            s.add_trade(pair="ETH/USDT", direction="LONG",
                        entry_price=2500, size=0.8)
            ex = get_open_exposure(s)
            assert ex["gross_notional"] == pytest.approx(2000.0)
            assert ex["count"] == 1
            assert ex["by_pair"] == {"ETH/USDT": pytest.approx(2000.0)}


def test_exposure_short_trade_uses_abs_notional():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            s.add_trade(pair="BTC/USDT", direction="SHORT",
                        entry_price=80000, size=0.05)
            ex = get_open_exposure(s)
            assert ex["gross_notional"] == pytest.approx(4000.0)


def test_exposure_mixed_long_short():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            s.add_trade(pair="SOL/USDT", direction="LONG",
                        entry_price=100, size=10)   # 1000
            s.add_trade(pair="ETH/USDT", direction="SHORT",
                        entry_price=2500, size=0.4)  # 1000
            ex = get_open_exposure(s)
            assert ex["gross_notional"] == pytest.approx(2000.0)
            assert ex["count"] == 2
            assert ex["by_pair"]["SOL/USDT"] == pytest.approx(1000.0)
            assert ex["by_pair"]["ETH/USDT"] == pytest.approx(1000.0)


def test_exposure_skips_pair_direction():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            s.add_trade(pair="SOL/USDT|ETH/USDT", direction="PAIR",
                        entry_price=None, size=100)
            s.add_trade(pair="SOL/USDT", direction="LONG",
                        entry_price=100, size=5)  # 500
            ex = get_open_exposure(s)
            assert ex["count"] == 1
            assert ex["gross_notional"] == pytest.approx(500.0)


def test_exposure_excludes_closed_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            tid = s.add_trade(pair="SOL/USDT", direction="LONG",
                              entry_price=100, size=1)
            s.close_trade(trade_id=tid, exit_price=110)
            assert get_open_exposure(s)["count"] == 0


# ─── get_account_state ──────────────────────────────────────────────────

def test_account_state_composition_no_trades():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            st = get_account_state(s, _CFG)
            assert st["equity"] == 10000.0
            assert st["gross_notional"] == 0.0
            assert st["gross_pct"] == 0.0
            assert st["n_open"] == 0
            assert st["max_concurrent"] == 3
            assert st["headroom"] == 10000.0  # 100% gross cap
            assert st["headroom_pct"] == 100.0
            assert st["max_position_pct"] == 25.0
            assert st["max_gross_pct"] == 100.0


def test_account_state_with_open_position():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            s.add_trade(pair="ETH/USDT", direction="LONG",
                        entry_price=2500, size=1.0)  # notional 2500
            st = get_account_state(s, _CFG)
            assert st["gross_notional"] == pytest.approx(2500.0)
            assert st["gross_pct"] == pytest.approx(25.0)
            assert st["n_open"] == 1
            assert st["headroom"] == pytest.approx(7500.0)


def test_account_state_uses_defaults_when_cfg_block_missing():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            cfg = {"exchange": "bybit", "timeframe": "1h"}
            st = get_account_state(s, cfg)
            assert st["equity"] == DEFAULT_STARTING_CAPITAL
            assert st["max_concurrent"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
