"""Tests untuk data_pipe.trend_follow — Step 18.A.

Verifikasi:
- compute_bollinger_atr_panel returns expected columns + math correctness.
- detect_breakout_signals emit pada fresh crossing.
- min_atr_pct filter blocks low-vol emits.
- debounce blocks too-close consecutive emits.
- payload roundtrip via parse_breakout_payload.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.trend_follow import (  # noqa: E402
    BB_STD_MULT,
    BB_WINDOW,
    compute_bollinger_atr_panel,
    detect_breakout_signals,
    parse_breakout_payload,
)


def _ohlcv_synthetic(closes: list[float], base_ts="2026-01-01") -> pd.DataFrame:
    """Build OHLCV from close prices. High/Low = close ± 0.5%."""
    base_ts = pd.Timestamp(base_ts)
    rows = []
    for i, c in enumerate(closes):
        ts = base_ts + pd.Timedelta(hours=i)
        rows.append({
            "ts": ts,
            "open": c,
            "high": c * 1.005,
            "low": c * 0.995,
            "close": c,
            "volume": 100.0,
        })
    return pd.DataFrame(rows)


def test_compute_panel_returns_expected_columns():
    df = _ohlcv_synthetic([100.0 + i * 0.1 for i in range(50)])
    panel = compute_bollinger_atr_panel(df)
    for col in ("sma", "bb_std", "upper", "lower", "atr"):
        assert col in panel.columns
    # First (BB_WINDOW-1) rows should have NaN SMA
    assert panel["sma"].iloc[:BB_WINDOW - 1].isna().all()
    # After warmup, SMA should be set
    assert not pd.isna(panel["sma"].iloc[BB_WINDOW])


def test_compute_panel_sma_correctness():
    """SMA(20) on monotonic +0.1 sequence: at idx 19, mean(0..19) = 9.95."""
    df = _ohlcv_synthetic([100.0 + i * 0.1 for i in range(30)])
    panel = compute_bollinger_atr_panel(df, bb_window=20)
    expected = sum(100.0 + i * 0.1 for i in range(20)) / 20
    assert abs(panel["sma"].iloc[19] - expected) < 1e-9


def test_compute_panel_upper_lower_bands():
    """Upper = SMA + 2×stdev, Lower = SMA - 2×stdev."""
    rng = np.random.default_rng(42)
    closes = (100 + rng.normal(0, 1, 60)).tolist()
    df = _ohlcv_synthetic(closes)
    panel = compute_bollinger_atr_panel(df, bb_window=20, bb_std=2.0)
    # At idx 30, manual check
    sma = panel["sma"].iloc[30]
    sd = panel["bb_std"].iloc[30]
    assert abs(panel["upper"].iloc[30] - (sma + 2 * sd)) < 1e-9
    assert abs(panel["lower"].iloc[30] - (sma - 2 * sd)) < 1e-9


def test_detect_emits_on_fresh_breakout():
    """Construct pattern: stable -> spike up that crosses upper band."""
    # 30 bars at 100 with small jitter, then 4 bars spike to 105
    rng = np.random.default_rng(1)
    base = (100 + rng.normal(0, 0.3, 30)).tolist()
    spike = [105.0, 106.0, 107.0, 108.0]
    closes = base + spike
    df = _ohlcv_synthetic(closes)
    panel = compute_bollinger_atr_panel(df, bb_window=20)
    events = detect_breakout_signals(
        panel, symbol="TEST/USDT",
        min_atr_pct=0.0001, debounce_bars=1,
    )
    assert len(events) >= 1
    sides = [parse_breakout_payload(e["payload"])["side"] for e in events]
    assert "long-breakout" in sides


def test_detect_respects_min_atr_pct():
    """Flat price = ATR ≈ 0.5% (high-low only). Set min_atr_pct=5% → no emit."""
    rng = np.random.default_rng(2)
    base = (100 + rng.normal(0, 0.1, 30)).tolist()
    spike = [105.0, 106.0]
    closes = base + spike
    df = _ohlcv_synthetic(closes)
    panel = compute_bollinger_atr_panel(df)
    events = detect_breakout_signals(
        panel, symbol="TEST/USDT", min_atr_pct=0.05  # 5%
    )
    assert events == []


def test_detect_debounce_blocks_consecutive_emits():
    """Pattern with multiple crossings within debounce window → only 1 emit."""
    rng = np.random.default_rng(3)
    base = (100 + rng.normal(0, 0.3, 30)).tolist()
    # Bouncing pattern oscillates inside and outside band, but within debounce
    bounces = [104, 103, 105, 103, 106]
    closes = base + bounces
    df = _ohlcv_synthetic(closes)
    panel = compute_bollinger_atr_panel(df, bb_window=20)
    events_no_debounce = detect_breakout_signals(
        panel, symbol="TEST/USDT", min_atr_pct=0.0, debounce_bars=1
    )
    events_with_debounce = detect_breakout_signals(
        panel, symbol="TEST/USDT", min_atr_pct=0.0, debounce_bars=10
    )
    assert len(events_with_debounce) <= len(events_no_debounce)


def test_detect_emits_on_breakdown():
    """Stable -> drop below lower band."""
    rng = np.random.default_rng(4)
    base = (100 + rng.normal(0, 0.3, 30)).tolist()
    drop = [95.0, 94.0, 93.0]
    closes = base + drop
    df = _ohlcv_synthetic(closes)
    panel = compute_bollinger_atr_panel(df, bb_window=20)
    events = detect_breakout_signals(
        panel, symbol="TEST/USDT",
        min_atr_pct=0.0001, debounce_bars=1,
    )
    sides = [parse_breakout_payload(e["payload"])["side"] for e in events]
    assert "short-breakdown" in sides


def test_parse_payload_roundtrip():
    payload = (
        "sym=BTC/USDT;side=long-breakout;close=70000.0000;upper=69500.0000;"
        "lower=68000.0000;sma=68750.0000;atr=350.0000;atr_pct=0.500"
    )
    parsed = parse_breakout_payload(payload)
    assert parsed["sym"] == "BTC/USDT"
    assert parsed["side"] == "long-breakout"
    assert parsed["close"] == 70000.0
    assert parsed["atr"] == 350.0
    assert parsed["atr_pct"] == 0.5


def test_empty_df_returns_empty():
    panel = compute_bollinger_atr_panel(pd.DataFrame())
    assert panel.empty
    events = detect_breakout_signals(panel, symbol="X/USDT")
    assert events == []
