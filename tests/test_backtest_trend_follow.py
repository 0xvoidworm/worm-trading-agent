"""Smoke tests untuk scripts/backtest_trend_follow.py — Step 18.B.

Verifikasi:
- simulate_trend_trades menghasilkan trades dengan pnl signed.
- Empty events → 0 trades.
- Stop loss fires saat price hit stop level.
- SMA cross exit fires saat close re-crosses SMA.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.paper_broker import PaperBroker  # noqa: E402
from scripts.backtest_trend_follow import simulate_trend_trades  # noqa: E402


def _build_broker(starting=10000.0, slippage_bps=0.0) -> PaperBroker:
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SEQUENCE seq_tj START 1")
    conn.execute(
        "CREATE TABLE trade_journal ("
        "  id INTEGER NOT NULL DEFAULT nextval('seq_tj'),"
        "  ts TIMESTAMP NOT NULL, pair VARCHAR NOT NULL,"
        "  direction VARCHAR NOT NULL, entry_price DOUBLE, size DOUBLE,"
        "  thesis VARCHAR, status VARCHAR NOT NULL DEFAULT 'OPEN',"
        "  exit_ts TIMESTAMP, exit_price DOUBLE, pnl DOUBLE, notes VARCHAR,"
        "  signal_id INTEGER, PRIMARY KEY (id))"
    )
    b = PaperBroker(starting_capital=starting, slippage_bps_per_side=slippage_bps)
    b.attach_conn(conn)
    return b


def _make_panel(closes, base_ts="2026-01-01"):
    """Synthetic panel: ts, close, high=close*1.005, low=close*0.995, sma=close, atr=close*0.01."""
    base = pd.Timestamp(base_ts)
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            "ts": base + pd.Timedelta(hours=i),
            "open": c,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 100.0,
            "sma": c,  # SMA = close = no SMA-cross trigger by default
            "bb_std": c * 0.01,
            "upper": c * 1.02,
            "lower": c * 0.98,
            "atr": c * 0.01,
        })
    return pd.DataFrame(rows)


def test_simulate_zero_events():
    broker = _build_broker()
    panel = _make_panel([100.0] * 30)
    trades = simulate_trend_trades(
        panel, events=[], symbol="X/USDT", broker=broker,
    )
    assert trades == []


def test_simulate_long_breakout_stop_loss():
    """Long entry at 100, price drops to 95 → hits stop = 100 - 2×1 = 98."""
    broker = _build_broker(slippage_bps=0.0)
    closes = [100.0] * 5 + [99.0, 97.0, 95.0, 96.0]
    panel = _make_panel(closes)
    # SMA = closes themselves (so SMA cross never triggers from above)
    # But we want stop_loss to fire. Override SMA so it equals 100 throughout
    # (close < SMA in down move → would trigger SMA cross before stop).
    # Set SMA to lower value to avoid SMA cross trigger
    panel["sma"] = 90.0  # constant low SMA so close never crosses below it
    events = [{
        "ts": panel["ts"].iloc[5].to_pydatetime(),
        "payload": (
            "sym=X/USDT;side=long-breakout;close=100.0000;upper=102.0000;"
            "lower=98.0000;sma=100.0000;atr=1.0000;atr_pct=1.000"
        ),
    }]
    # ATR=1, stop_atr_mult=2 → stop = 100-2 = 98. Close at idx 7 = 95 → stop fires.
    trades = simulate_trend_trades(
        panel, events=events, symbol="X/USDT", broker=broker,
        stop_atr_mult=2.0, trail_atr_mult=10.0,
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "stop_loss"
    assert trades[0]["pnl"] < 0  # LONG dropped → negative pnl


def test_simulate_long_breakout_sma_cross_exit():
    """Long entry at 100, SMA = 100, price drops below SMA → SMA cross exit."""
    broker = _build_broker(slippage_bps=0.0)
    closes = [100.0] * 5 + [99.5, 99.0, 98.0]
    panel = _make_panel(closes)
    panel["sma"] = 100.0  # SMA stays at 100; close drops below
    panel["atr"] = 5.0    # Wide ATR → stop loss far away, SMA cross fires first
    events = [{
        "ts": panel["ts"].iloc[5].to_pydatetime(),
        "payload": (
            "sym=X/USDT;side=long-breakout;close=100.0000;upper=102.0000;"
            "lower=98.0000;sma=100.0000;atr=5.0000;atr_pct=5.000"
        ),
    }]
    trades = simulate_trend_trades(
        panel, events=events, symbol="X/USDT", broker=broker,
        stop_atr_mult=2.0,  # stop = 100 - 2*5 = 90 (far)
    )
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "sma_cross"


def test_simulate_short_breakdown_pnl():
    """SHORT entry at 100, price drops to 95 → SHORT pnl positive."""
    broker = _build_broker(slippage_bps=0.0)
    closes = [100.0] * 5 + [99.0, 97.0, 95.0, 93.0]
    panel = _make_panel(closes)
    panel["sma"] = 110.0  # SMA above all → SHORT trades won't SMA-cross
    panel["atr"] = 10.0   # Wide ATR → no stop trigger
    events = [{
        "ts": panel["ts"].iloc[5].to_pydatetime(),
        "payload": (
            "sym=X/USDT;side=short-breakdown;close=100.0000;upper=102.0000;"
            "lower=98.0000;sma=100.0000;atr=10.0000;atr_pct=10.000"
        ),
    }]
    trades = simulate_trend_trades(
        panel, events=events, symbol="X/USDT", broker=broker,
        stop_atr_mult=5.0, trail_atr_mult=10.0, max_hold_hours=10,
    )
    assert len(trades) == 1
    assert trades[0]["direction"] == "SHORT"
    # SHORT: entry 100, exit < 100 → positive PnL
    assert trades[0]["pnl"] > 0


def test_simulate_serial_skips_overlapping():
    """2nd event ts <= 1st trade exit → skipped."""
    broker = _build_broker()
    closes = [100.0] * 30
    panel = _make_panel(closes)
    base = panel["ts"].iloc[5].to_pydatetime()
    events = [
        {"ts": base, "payload": (
            "sym=X/USDT;side=long-breakout;close=100.0000;upper=102.0000;"
            "lower=98.0000;sma=100.0000;atr=1.0000;atr_pct=1.000"
        )},
        {"ts": panel["ts"].iloc[6].to_pydatetime(), "payload": (
            "sym=X/USDT;side=long-breakout;close=100.0000;upper=102.0000;"
            "lower=98.0000;sma=100.0000;atr=1.0000;atr_pct=1.000"
        )},
    ]
    trades = simulate_trend_trades(
        panel, events=events, symbol="X/USDT", broker=broker,
        max_hold_hours=20,
    )
    # Only 1 trade — 2nd event within hold window of 1st
    assert len(trades) == 1
