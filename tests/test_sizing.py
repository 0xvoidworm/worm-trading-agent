"""Tests untuk data_pipe.sizing (Step 3)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.sizing import calc_position_size


_CFG_DEFAULT = {
    "portfolio": {
        "starting_capital": 10000.0,
        "risk_pct_per_trade": 1.0,
        "max_position_pct": 25.0,
        "max_gross_exposure_pct": 100.0,
        "max_concurrent_trades": 3,
    },
}


def _empty_exposure():
    return {"count": 0, "gross_notional": 0.0}


# ─── happy path ──────────────────────────────────────────────────────────

def test_happy_path_eth_2500_sl5_risk1pct():
    """ETH @ 2500, SL 5%, equity 10k, risk 1% → 0.8 ETH, notional 2000."""
    res = calc_position_size(
        entry_price=2500.0, sl_pct=5.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] == pytest.approx(0.8, abs=1e-4)
    assert res["notional"] == pytest.approx(2000.0)
    assert res["risk_amount"] == pytest.approx(100.0)
    assert res["reason_skip"] is None


def test_short_sizing_same_math():
    res = calc_position_size(
        entry_price=2500.0, sl_pct=5.0, direction="SHORT",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] == pytest.approx(0.8, abs=1e-4)
    assert res["notional"] == pytest.approx(2000.0)


def test_unknown_direction_skipped():
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="HODL",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "direction" in res["reason_skip"].lower()


def test_pair_direction_skipped():
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="PAIR",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None


# ─── Cap A — max_position_pct ─────────────────────────────────────────────

def test_cap_a_position_scales_down_when_risk_implies_too_big():
    """SL kecil → risk_per_unit kecil → raw_size besar → notional > max_position."""
    # entry 100, SL 1%, risk 1% → raw_size = 100/1 = 100, raw_notional = 10000
    # max_position 25% × 10000 = 2500 → scale 0.25 → size 25, notional 2500
    res = calc_position_size(
        entry_price=100.0, sl_pct=1.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["notional"] == pytest.approx(2500.0)
    assert res["size"] == pytest.approx(25.0)


# ─── Cap B — max_gross_exposure_pct ──────────────────────────────────────

def test_cap_b_returns_none_when_no_headroom():
    """gross sudah penuh → return None reason gross."""
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="LONG",
        equity=10000.0,
        current_exposure={"count": 1, "gross_notional": 10000.0},
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "gross" in res["reason_skip"].lower()


def test_cap_b_scales_to_remaining_headroom():
    """gross 9500/10000 → headroom 500 → size scaled supaya notional = 500."""
    # raw_size with default = 100/(100*0.05) = 20, raw_notional = 2000
    # cap A: 2000 ≤ 2500 → no scale
    # cap B: headroom 500, 2000 > 500 → scale 0.25 → size 5, notional 500
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="LONG",
        equity=10000.0,
        current_exposure={"count": 1, "gross_notional": 9500.0},
        cfg=_CFG_DEFAULT,
    )
    assert res["notional"] == pytest.approx(500.0)
    assert res["size"] == pytest.approx(5.0, abs=1e-4)


# ─── Cap C — max_concurrent_trades ───────────────────────────────────────

def test_cap_c_blocks_when_at_max_concurrent():
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="LONG",
        equity=10000.0,
        current_exposure={"count": 3, "gross_notional": 0.0},
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "concurrent" in res["reason_skip"].lower()


def test_cap_c_passes_when_below_max():
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="LONG",
        equity=10000.0,
        current_exposure={"count": 2, "gross_notional": 0.0},
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is not None


# ─── input validation ───────────────────────────────────────────────────

def test_invalid_entry_price_skipped():
    res = calc_position_size(
        entry_price=0.0, sl_pct=5.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "entry_price" in res["reason_skip"]


def test_zero_equity_skipped():
    res = calc_position_size(
        entry_price=100.0, sl_pct=5.0, direction="LONG",
        equity=0.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "equity" in res["reason_skip"]


def test_invalid_sl_pct_skipped():
    res = calc_position_size(
        entry_price=100.0, sl_pct=0.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    assert res["size"] is None
    assert "sl_pct" in res["reason_skip"]


# ─── rounding ───────────────────────────────────────────────────────────

def test_size_rounded_to_six_decimals():
    """High-priced asset like BTC → small size, must round to 6dp & remain > 0."""
    res = calc_position_size(
        entry_price=80000.0, sl_pct=5.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    # raw_size = 100 / (80000 * 0.05) = 100/4000 = 0.025
    assert res["size"] == pytest.approx(0.025, abs=1e-6)


def test_size_below_minimum_returns_none():
    """Equity sangat kecil → size sub-microsatoshi → None."""
    res = calc_position_size(
        entry_price=80000.0, sl_pct=5.0, direction="LONG",
        equity=0.001, current_exposure=_empty_exposure(),
        cfg=_CFG_DEFAULT,
    )
    # risk 0.00001, risk_per_unit 4000, raw_size 2.5e-9 → rounds to 0
    assert res["size"] is None
    assert "size below" in res["reason_skip"]


# ─── multi-cap interaction ───────────────────────────────────────────────

def test_smallest_constraint_wins():
    """Cap A (per-position 2500) AND Cap B (headroom 1000) both bind → 1000 wins."""
    res = calc_position_size(
        entry_price=100.0, sl_pct=1.0, direction="LONG",
        equity=10000.0,
        current_exposure={"count": 1, "gross_notional": 9000.0},
        cfg=_CFG_DEFAULT,
    )
    # raw notional 10000 → Cap A scales to 2500 → Cap B scales to 1000
    assert res["notional"] == pytest.approx(1000.0)


# ─── cfg defaults ───────────────────────────────────────────────────────

def test_defaults_used_when_cfg_block_missing():
    res = calc_position_size(
        entry_price=2500.0, sl_pct=5.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg={},
    )
    # same as happy path with defaults
    assert res["size"] == pytest.approx(0.8, abs=1e-4)


def test_custom_risk_pct_changes_size():
    # Bump max_position_pct supaya Cap A tidak bind di 2× risk.
    cfg2 = {"portfolio": {
        **_CFG_DEFAULT["portfolio"],
        "risk_pct_per_trade": 2.0,
        "max_position_pct": 50.0,
    }}
    res = calc_position_size(
        entry_price=2500.0, sl_pct=5.0, direction="LONG",
        equity=10000.0, current_exposure=_empty_exposure(),
        cfg=cfg2,
    )
    # 2× risk → 2× size (now under caps)
    assert res["size"] == pytest.approx(1.6, abs=1e-4)
    assert res["notional"] == pytest.approx(4000.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
