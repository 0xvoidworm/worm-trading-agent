"""Tests untuk data_pipe.cross_exchange — Step 20."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.cross_exchange import (  # noqa: E402
    compute_funding_diff_panel,
    detect_funding_diff_signals,
    dynamic_diff_threshold,
    parse_diff_payload,
)


def _funding_df(rates_8h: list[float], base_ts="2026-01-01"):
    base = pd.Timestamp(base_ts)
    return pd.DataFrame([
        {"ts": base + pd.Timedelta(hours=i * 8), "funding": r}
        for i, r in enumerate(rates_8h)
    ])


def test_panel_empty_when_no_data():
    panel = compute_funding_diff_panel(pd.DataFrame(), pd.DataFrame(), "BTC/USDT")
    assert panel.empty


def test_panel_aligns_and_computes_diff():
    bybit = _funding_df([0.0001, 0.0002, 0.0003])
    binance = _funding_df([0.00005, 0.00015, 0.00025])
    panel = compute_funding_diff_panel(bybit, binance, "BTC/USDT")
    assert len(panel) == 3
    # diff = bybit - binance = 0.00005 each
    assert all(abs(panel["diff"] - 0.00005) < 1e-9)


def test_panel_diff_can_be_negative():
    bybit = _funding_df([0.00005])
    binance = _funding_df([0.0001])
    panel = compute_funding_diff_panel(bybit, binance, "X/USDT")
    assert panel["diff"].iloc[0] < 0


def test_detect_emits_when_threshold_crossed():
    bybit = _funding_df([0.0001, 0.0005, 0.0001])  # diff = +0.0001, +0.0003, +0.0001
    binance = _funding_df([0.0000, 0.0002, 0.0000])
    panel = compute_funding_diff_panel(bybit, binance, "BTC/USDT")
    events = detect_funding_diff_signals(
        panel, "BTC/USDT", threshold=0.00025, debounce_hours=1,
    )
    # 2nd row diff=+0.0003 ≥ 0.00025 → emit
    # 3rd row diff=+0.0001 below threshold → no emit
    assert len(events) == 1
    parsed = parse_diff_payload(events[0]["payload"])
    assert parsed["sym"] == "BTC/USDT"
    assert parsed["side"] == "bybit-crowded"


def test_detect_respects_debounce():
    """Multiple consecutive emits → debounce blocks until interval passes.

    4 rows at 8h apart, debounce 24h. Emits at row 0 and row 3 (= 24h apart, ≥ debounce).
    """
    bybit = _funding_df([0.0005, 0.0005, 0.0005, 0.0005])
    binance = _funding_df([0.0, 0.0, 0.0, 0.0])
    panel = compute_funding_diff_panel(bybit, binance, "X")
    events = detect_funding_diff_signals(
        panel, "X", threshold=0.0002, debounce_hours=24,
    )
    # Row 0 emits, rows 1-2 (<24h) skipped, row 3 (24h gap) emits → 2 total
    assert len(events) == 2


def test_dynamic_threshold_clamps():
    """Very high |diff| → clamped to max_threshold."""
    rng = np.random.default_rng(1)
    panel = pd.DataFrame({"diff": rng.uniform(0.01, 0.02, 100)})
    t = dynamic_diff_threshold(panel, percentile=90.0, max_threshold=0.001)
    assert t == 0.001


def test_dynamic_threshold_fallback_when_sparse():
    panel = pd.DataFrame({"diff": [0.0001, 0.0002]})
    t = dynamic_diff_threshold(panel, min_samples=50)
    # Note: min_samples is hardcoded default; just verify it falls back
    # The function uses min_samples=50 internally — sparse returns fallback
    # Actually module hardcodes 50, so sparse 2-row dataset → fallback
    assert t == 0.0002  # default fallback


def test_parse_payload_roundtrip():
    payload = ("sym=BTC/USDT;side=bybit-crowded;diff=0.0300%;"
               "bybit_f=0.0400%;binance_f=0.0100%;threshold=0.0200%")
    parsed = parse_diff_payload(payload)
    assert parsed["sym"] == "BTC/USDT"
    assert parsed["side"] == "bybit-crowded"
    assert abs(parsed["diff"] - 0.0003) < 1e-9
    assert abs(parsed["bybit_f"] - 0.0004) < 1e-9
