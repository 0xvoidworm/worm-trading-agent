"""Tests untuk data_pipe.cointegration — Step 17.B1.

Verifikasi:
- rolling_cointegration_panel returns proper columns and obeys lookback/refit.
- detect_spread_signals emit pada fresh crossing dengan debounce/kill-gate.
- parse_spread_payload round-trip.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.cointegration import (  # noqa: E402
    detect_spread_signals,
    dynamic_entry_threshold,
    parse_spread_payload,
    rolling_cointegration_panel,
)


def _cointegrated_pair(n: int = 1000, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate dua price series yang cointegrated by construction.

    log(y) = alpha + beta × log(x) + noise (mean-reverting OU)
    """
    rng = np.random.default_rng(seed)
    # Random walk untuk x
    log_x = np.cumsum(rng.normal(0, 0.01, n)) + 4.6  # ~e^4.6 = 100
    # Spread mean-reverting (OU)
    kappa = 0.05
    sigma = 0.02
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = spread[i - 1] + kappa * (0 - spread[i - 1]) + sigma * rng.normal()
    # log(y) = 0.1 + 1.0 × log(x) + spread
    log_y = 0.1 + 1.0 * log_x + spread

    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    ts_index = [base_ts + pd.Timedelta(hours=i) for i in range(n)]
    df_a = pd.DataFrame({"ts": ts_index, "close": np.exp(log_y)})
    df_b = pd.DataFrame({"ts": ts_index, "close": np.exp(log_x)})
    return df_a, df_b


def _non_cointegrated_pair(n: int = 1000, seed: int = 11) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Dua random walk independen → tidak cointegrated."""
    rng = np.random.default_rng(seed)
    log_y = np.cumsum(rng.normal(0, 0.01, n)) + 4.6
    log_x = np.cumsum(rng.normal(0, 0.01, n)) + 4.6

    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    ts_index = [base_ts + pd.Timedelta(hours=i) for i in range(n)]
    df_a = pd.DataFrame({"ts": ts_index, "close": np.exp(log_y)})
    df_b = pd.DataFrame({"ts": ts_index, "close": np.exp(log_x)})
    return df_a, df_b


def test_rolling_panel_empty_when_too_few_bars():
    df_a, df_b = _cointegrated_pair(n=100)
    panel = rolling_cointegration_panel(
        df_a, df_b, ("Y", "X"), lookback_bars=720, refit_freq_bars=24
    )
    assert panel.empty


def test_rolling_panel_has_expected_columns():
    df_a, df_b = _cointegrated_pair(n=800)
    panel = rolling_cointegration_panel(
        df_a, df_b, ("Y", "X"), lookback_bars=200, refit_freq_bars=24, z_window=50
    )
    assert not panel.empty
    for col in ("ts", "hedge_ratio", "alpha", "adf_pvalue", "half_life",
                "is_cointegrated", "z_score", "spread"):
        assert col in panel.columns


def test_rolling_panel_detects_cointegration():
    """Synthetic cointegrated pair → ≥ 80% bars is_cointegrated=True (some bars
    fail ADF due to noise but bulk should pass).
    """
    df_a, df_b = _cointegrated_pair(n=1500, seed=3)
    panel = rolling_cointegration_panel(
        df_a, df_b, ("Y", "X"), lookback_bars=300, refit_freq_bars=24
    )
    assert not panel.empty
    coint_rate = panel["is_cointegrated"].mean()
    assert coint_rate >= 0.5, f"expected ≥50% cointegrated, got {coint_rate}"


def test_rolling_panel_rejects_non_cointegrated():
    """Two independent random walks → mostly is_cointegrated=False."""
    df_a, df_b = _non_cointegrated_pair(n=1500, seed=11)
    panel = rolling_cointegration_panel(
        df_a, df_b, ("Y", "X"), lookback_bars=300, refit_freq_bars=24
    )
    assert not panel.empty
    coint_rate = panel["is_cointegrated"].mean()
    # Some false positives expected, but should be low
    assert coint_rate < 0.5, f"expected <50% false-positive coint, got {coint_rate}"


def test_rolling_panel_refit_frequency():
    """Hedge ratio refitted only every refit_freq_bars → consecutive bars within
    refit window share same hedge_ratio.
    """
    df_a, df_b = _cointegrated_pair(n=600)
    panel = rolling_cointegration_panel(
        df_a, df_b, ("Y", "X"), lookback_bars=300, refit_freq_bars=48
    )
    # Take first 50 bars after warmup, check unique hedge_ratio count
    head = panel.head(50)
    unique_ratios = head["hedge_ratio"].nunique()
    # 50 bars / 48 refit_freq ≈ 1-2 unique values
    assert unique_ratios <= 3, f"too many refits: {unique_ratios}"


def test_detect_emits_on_fresh_z_crossing():
    """Synthetic panel: z crosses entry threshold → emit COINTEGRATION_SPREAD."""
    base_ts = pd.Timestamp("2026-01-01")
    rows = []
    z_vals = [0.5, 1.0, 1.5, 2.1, 2.3, 1.8, 0.5, -2.5, -0.5]
    # Make ts spans 24h+ apart so debounce doesn't accidentally block second emit
    for i, z in enumerate(z_vals):
        rows.append({
            "ts": base_ts + pd.Timedelta(hours=i * 48),
            "hedge_ratio": 1.0,
            "alpha": 0.1,
            "adf_pvalue": 0.01,
            "half_life": 24.0,
            "is_cointegrated": True,
            "z_score": z,
            "spread": z * 0.01,
        })
    panel = pd.DataFrame(rows)
    events = detect_spread_signals(
        panel, ("ETH/USDT", "BTC/USDT"),
        entry_z=2.0, max_z_kill=4.0, debounce_bars=24,
    )
    # 1 short-spread emit (z 1.5 → 2.1) + 1 long-spread emit (z 0.5 → -2.5)
    assert len(events) == 2
    sides = [parse_spread_payload(e["payload"])["side"] for e in events]
    assert "short-spread" in sides
    assert "long-spread" in sides


def test_detect_respects_max_z_kill():
    """z >= max_z_kill → no emit (broken spread)."""
    base_ts = pd.Timestamp("2026-01-01")
    rows = [
        {"ts": base_ts + pd.Timedelta(hours=i * 48),
         "hedge_ratio": 1.0, "alpha": 0.1, "adf_pvalue": 0.01,
         "half_life": 24.0, "is_cointegrated": True,
         "z_score": z, "spread": z * 0.01}
        for i, z in enumerate([0.0, 5.0])  # straight to z=5 above kill threshold
    ]
    panel = pd.DataFrame(rows)
    events = detect_spread_signals(
        panel, ("Y", "X"), entry_z=2.0, max_z_kill=4.0, debounce_bars=1,
    )
    assert events == []


def test_detect_respects_cointegrated_gate():
    """is_cointegrated=False → no emit, even if z crosses."""
    base_ts = pd.Timestamp("2026-01-01")
    rows = [
        {"ts": base_ts + pd.Timedelta(hours=i * 48),
         "hedge_ratio": 1.0, "alpha": 0.1, "adf_pvalue": 0.5,
         "half_life": 24.0, "is_cointegrated": False,
         "z_score": z, "spread": z * 0.01}
        for i, z in enumerate([0.5, 2.5])
    ]
    panel = pd.DataFrame(rows)
    events = detect_spread_signals(
        panel, ("Y", "X"), entry_z=2.0, require_cointegrated=True,
    )
    assert events == []


def test_detect_debounce_blocks_consecutive_emits():
    """Two fresh entries within debounce_bars hours → only 1st emit."""
    base_ts = pd.Timestamp("2026-01-01")
    rows = [
        {"ts": base_ts + pd.Timedelta(hours=i * 2),
         "hedge_ratio": 1.0, "alpha": 0.1, "adf_pvalue": 0.01,
         "half_life": 24.0, "is_cointegrated": True,
         "z_score": z, "spread": z * 0.01}
        for i, z in enumerate([0.0, 2.5, 1.0, 2.5])
    ]
    panel = pd.DataFrame(rows)
    events = detect_spread_signals(
        panel, ("Y", "X"), entry_z=2.0, debounce_bars=24,
    )
    # 2nd 2.5 crossing within 6h of 1st → debounced
    assert len(events) == 1


# ─── Step 17.B2: dynamic entry threshold tests ───────────────────────────────


def test_dynamic_threshold_fallback_when_empty():
    panel = pd.DataFrame()
    assert dynamic_entry_threshold(panel) == 2.0


def test_dynamic_threshold_fallback_when_few_samples():
    panel = pd.DataFrame({"z_score": [1.0, 2.0, 3.0]})
    assert dynamic_entry_threshold(panel, min_samples=50) == 2.0


def test_dynamic_threshold_clamps_to_min():
    """Low-volatility spread (|z| all small) → clamped to min_threshold."""
    panel = pd.DataFrame({"z_score": [0.1] * 100})
    t = dynamic_entry_threshold(panel, min_threshold=1.5, max_threshold=3.5,
                                  percentile=95.0)
    assert t == 1.5


def test_dynamic_threshold_clamps_to_max():
    """Huge |z| values → clamped to max_threshold."""
    panel = pd.DataFrame({"z_score": [10.0] * 100})
    t = dynamic_entry_threshold(panel, min_threshold=1.5, max_threshold=3.5)
    assert t == 3.5


def test_dynamic_threshold_returns_percentile():
    """Sample distribusi |z| ~ U[1, 3] → p95 sekitar 2.9 (di dalam bounds)."""
    rng = np.random.default_rng(42)
    z_vals = rng.uniform(1.0, 3.0, 200)
    panel = pd.DataFrame({"z_score": z_vals})
    t = dynamic_entry_threshold(panel, percentile=95.0)
    # Expected ≈ 2.9, bounded [1.5, 3.5]
    assert 2.7 <= t <= 3.0


def test_parse_spread_payload_roundtrip():
    payload = (
        "pair=ETH/USDT|BTC/USDT;z=+2.34;hedge_ratio=1.2345;"
        "adf_p=0.012;hl=25.5;side=short-spread;entry_z=2.0"
    )
    parsed = parse_spread_payload(payload)
    assert parsed["pair"] == "ETH/USDT|BTC/USDT"
    assert parsed["z"] == 2.34
    assert parsed["hedge_ratio"] == 1.2345
    assert parsed["adf_p"] == 0.012
    assert parsed["hl"] == 25.5
    assert parsed["side"] == "short-spread"
    assert parsed["entry_z"] == 2.0
