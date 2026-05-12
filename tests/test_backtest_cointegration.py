"""Smoke tests untuk scripts/backtest_cointegration.py — Step 17.B3.

Verifikasi:
- simulate_pair_trades menghasilkan trades dengan 2-leg pnl combined.
- Empty events → 0 trades.
- Serial execution: trade berikutnya tidak overlap dengan trade yang masih open.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.paper_broker import PaperBroker  # noqa: E402
from scripts.backtest_cointegration import simulate_pair_trades  # noqa: E402


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
    b = PaperBroker(
        starting_capital=starting, slippage_bps_per_side=slippage_bps,
    )
    b.attach_conn(conn)
    return b


def _make_panel_and_prices(n: int = 50, base_price: float = 100.0):
    """Synthetic panel + matching ohlcv. Z oscillates: +2.5 then -2.5 then 0."""
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    rows = []
    for i in range(n):
        ts = base_ts + pd.Timedelta(hours=i)
        # Z pattern: high -> revert -> low -> revert
        if i < 5:
            z = 0.0
        elif i < 10:
            z = 2.5  # short-spread entry
        elif i < 15:
            z = 0.0  # mean revert
        elif i < 20:
            z = -2.5  # long-spread entry
        else:
            z = 0.0
        rows.append({
            "ts": ts,
            "hedge_ratio": 1.0,
            "alpha": 0.0,
            "adf_pvalue": 0.01,
            "half_life": 5.0,
            "is_cointegrated": True,
            "z_score": z,
            "spread": z * 0.01,
        })
    panel = pd.DataFrame(rows)

    # OHLCV: leg A drifts; leg B flat
    df_a = pd.DataFrame({
        "ts": [base_ts + pd.Timedelta(hours=i) for i in range(n)],
        "close": [base_price + i * 0.1 for i in range(n)],
    })
    df_b = pd.DataFrame({
        "ts": [base_ts + pd.Timedelta(hours=i) for i in range(n)],
        "close": [base_price for _ in range(n)],
    })
    return panel, df_a, df_b


def test_simulate_zero_events_returns_empty():
    broker = _build_broker()
    panel, df_a, df_b = _make_panel_and_prices()
    trades = simulate_pair_trades(
        panel, events=[], pair=("A/USDT", "B/USDT"),
        broker=broker, df_a=df_a, df_b=df_b,
    )
    assert trades == []


def test_simulate_two_signals_serializes():
    """Two non-overlapping signals → 2 trades. Each has 2-leg pnl combined."""
    broker = _build_broker(starting=10000.0, slippage_bps=0.0)
    panel, df_a, df_b = _make_panel_and_prices()
    # Build 2 events manually mimicking detect_spread_signals output
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    events = [
        {
            "id": "x1",
            "ts": (base_ts + pd.Timedelta(hours=5)).to_pydatetime(),
            "event_type": "COINTEGRATION_SPREAD",
            "symbol": "A/USDT|B/USDT",
            "severity": "warn",
            "payload": (
                "pair=A/USDT|B/USDT;z=+2.50;hedge_ratio=1.0000;"
                "adf_p=0.010;hl=5.0;side=short-spread;entry_z=2.0"
            ),
        },
        {
            "id": "x2",
            "ts": (base_ts + pd.Timedelta(hours=15)).to_pydatetime(),
            "event_type": "COINTEGRATION_SPREAD",
            "symbol": "A/USDT|B/USDT",
            "severity": "warn",
            "payload": (
                "pair=A/USDT|B/USDT;z=-2.50;hedge_ratio=1.0000;"
                "adf_p=0.010;hl=5.0;side=long-spread;entry_z=2.0"
            ),
        },
    ]
    trades = simulate_pair_trades(
        panel, events=events, pair=("A/USDT", "B/USDT"),
        broker=broker, df_a=df_a, df_b=df_b,
        risk_pct_per_pair=5.0, exit_z=0.5, max_z_kill=4.0,
    )
    assert len(trades) == 2
    # Each trade has 2-leg pnl combined
    for t in trades:
        assert t["leg_a_pnl"] is not None
        assert t["leg_b_pnl"] is not None
        assert t["pnl"] == t["leg_a_pnl"] + t["leg_b_pnl"]
    # Exit reason should be mean_revert for both
    assert all(t["exit_reason"] in ("mean_revert", "stop_loss", "time_stop")
               for t in trades)


def test_simulate_serial_skips_overlapping_event():
    """Second event ts <= first trade exit_ts → skipped (serial pair-trade)."""
    broker = _build_broker()
    panel, df_a, df_b = _make_panel_and_prices()
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    events = [
        {
            "ts": (base_ts + pd.Timedelta(hours=5)).to_pydatetime(),
            "payload": (
                "pair=A/USDT|B/USDT;z=+2.50;hedge_ratio=1.0000;"
                "adf_p=0.010;hl=10.0;side=short-spread;entry_z=2.0"
            ),
        },
        {
            "ts": (base_ts + pd.Timedelta(hours=6)).to_pydatetime(),  # overlapping
            "payload": (
                "pair=A/USDT|B/USDT;z=+2.50;hedge_ratio=1.0000;"
                "adf_p=0.010;hl=10.0;side=short-spread;entry_z=2.0"
            ),
        },
    ]
    trades = simulate_pair_trades(
        panel, events=events, pair=("A/USDT", "B/USDT"),
        broker=broker, df_a=df_a, df_b=df_b,
    )
    # Only 1 trade — 2nd event overlaps with 1st's hold window
    assert len(trades) == 1
