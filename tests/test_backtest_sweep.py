"""Tests untuk scripts/backtest_sweep.py — Step 7 walk-forward consistency.

Cover:
  - Window splitting: 4 quarters non-overlapping, day counts correct.
  - Per-quarter stats: trade allocation by exit_ts, sign labels (NO_DATA,
    SPARSE, POSITIVE, NEGATIVE).
  - Classifier: STABLE_POSITIVE/NEGATIVE, INCONSISTENT, INSUFFICIENT_SAMPLE,
    INSUFFICIENT_SAMPLE_DISTRIBUTION.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import backtest_sweep as bs  # noqa: E402


def test_split_into_quarters_returns_n_segments():
    start = datetime(2026, 1, 1)
    end = datetime(2026, 4, 1)  # 90 days
    qs = bs.split_into_quarters(start, end, 4)
    assert len(qs) == 4
    # Sequential, non-overlapping
    for i in range(1, len(qs)):
        assert qs[i][0] == qs[i - 1][1]
    assert qs[0][0] == start
    assert qs[-1][1] == end


def test_split_into_quarters_rejects_short_window():
    import pytest
    with pytest.raises(ValueError):
        bs.split_into_quarters(datetime(2026, 1, 1),
                                datetime(2026, 1, 3), 4)


def test_per_quarter_stats_assigns_trades_by_exit_ts():
    quarters = [
        (datetime(2026, 1, 1), datetime(2026, 1, 15)),
        (datetime(2026, 1, 15), datetime(2026, 1, 30)),
    ]
    closed = [
        {"exit_ts": datetime(2026, 1, 5), "pnl": 100.0},
        {"exit_ts": datetime(2026, 1, 10), "pnl": 50.0},
        {"exit_ts": datetime(2026, 1, 20), "pnl": -75.0},
    ]
    stats = bs.per_quarter_stats(closed, quarters, starting_capital=10000.0)
    assert len(stats) == 2
    assert stats[0]["n_trades_closed"] == 2
    assert abs(stats[0]["realized_pnl"] - 150.0) < 1e-9
    assert abs(stats[0]["realized_return_pct"] - 1.5) < 1e-9
    assert stats[1]["n_trades_closed"] == 1
    assert abs(stats[1]["realized_pnl"] - (-75.0)) < 1e-9


def test_quarter_sign_label_no_data():
    """Empty quarter → NO_DATA (bukan FLAT/NEGATIVE/POSITIVE)."""
    stats = bs.per_quarter_stats(
        closed_trades=[],
        quarters=[(datetime(2026, 1, 1), datetime(2026, 1, 10))],
        starting_capital=10000.0,
    )
    assert stats[0]["sign"] == "NO_DATA"
    assert stats[0]["n_trades_closed"] == 0
    assert stats[0]["realized_pnl"] == 0.0


def test_quarter_sign_label_sparse_when_below_min():
    """1..<MIN_QUARTER_TRADES → SPARSE (data ada tapi tidak reliable)."""
    closed = [
        {"exit_ts": datetime(2026, 1, 5), "pnl": 10.0},
    ]
    stats = bs.per_quarter_stats(
        closed_trades=closed,
        quarters=[(datetime(2026, 1, 1), datetime(2026, 1, 10))],
        starting_capital=10000.0,
    )
    assert stats[0]["sign"] == "SPARSE"


def test_quarter_sign_label_positive_when_dense_and_profitable():
    closed = [
        {"exit_ts": datetime(2026, 1, 5), "pnl": 10.0}
        for _ in range(bs.MIN_QUARTER_TRADES)
    ]
    stats = bs.per_quarter_stats(
        closed_trades=closed,
        quarters=[(datetime(2026, 1, 1), datetime(2026, 1, 10))],
        starting_capital=10000.0,
    )
    assert stats[0]["sign"] == "POSITIVE"
    assert stats[0]["n_trades_closed"] == bs.MIN_QUARTER_TRADES


def _stub_quarter(n_trades: int, ret_pct: float, qid: int = 1) -> dict:
    """Hand-craft per_q dict for classify() tests, bypassing time arithmetic."""
    sign = bs._quarter_sign_label(n_trades, ret_pct, bs.MIN_QUARTER_TRADES)
    return {
        "quarter": qid, "start": "2026-01-01T00:00:00",
        "end": "2026-01-10T00:00:00",
        "n_trades_closed": n_trades,
        "realized_pnl": ret_pct * 100.0,
        "realized_return_pct": ret_pct,
        "sign": sign,
    }


def test_classify_insufficient_sample_when_total_low():
    per_q = [_stub_quarter(5, 1.0, i) for i in range(1, 5)]
    cls = bs.classify(per_q, n_total_closed=20)
    assert cls["verdict"] == "INSUFFICIENT_SAMPLE"


def test_classify_insufficient_distribution_when_few_quarters_have_data():
    """3/4 quarter NO_DATA → INSUFFICIENT_SAMPLE_DISTRIBUTION (regardless
    of total)."""
    per_q = [
        _stub_quarter(40, 1.0, 1),  # POSITIVE, ≥8
        _stub_quarter(0, 0.0, 2),   # NO_DATA
        _stub_quarter(0, 0.0, 3),   # NO_DATA
        _stub_quarter(0, 0.0, 4),   # NO_DATA
    ]
    cls = bs.classify(per_q, n_total_closed=40)
    assert cls["verdict"] == "INSUFFICIENT_SAMPLE_DISTRIBUTION"
    assert cls["quarters_with_data"] == 1
    assert cls["quarters_no_data"] == 3


def test_classify_stable_positive_with_no_data_excluded():
    """3 POS + 1 NO_DATA, total ≥30 → STABLE_POSITIVE. Empty quarter
    di-exclude dari sign tally."""
    per_q = [
        _stub_quarter(10, 2.0, 1),
        _stub_quarter(0, 0.0, 2),     # NO_DATA — excluded
        _stub_quarter(15, 1.5, 3),
        _stub_quarter(12, 0.5, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=37)
    assert cls["verdict"] == "STABLE_POSITIVE"
    assert cls["quarters_with_data"] == 3
    assert cls["sign_consistency"] == "3/3"


def test_classify_inconsistent_when_sparse_quarter_present():
    """1 quarter dengan 1..<8 trades → INCONSISTENT (tidak reliable)."""
    per_q = [
        _stub_quarter(15, 1.0, 1),
        _stub_quarter(3, 0.5, 2),     # SPARSE
        _stub_quarter(15, 1.0, 3),
        _stub_quarter(15, 0.8, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=48)
    assert cls["verdict"] == "INCONSISTENT"
    assert "SPARSE" in str(per_q[1]["sign"]) or per_q[1]["sign"] == "SPARSE"


def test_classify_inconsistent_when_signs_mixed():
    """Mixed signs across quarters_with_data → INCONSISTENT."""
    per_q = [
        _stub_quarter(10, +1.0, 1),
        _stub_quarter(10, +1.0, 2),
        _stub_quarter(10, -1.0, 3),
        _stub_quarter(10, -1.0, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=40)
    assert cls["verdict"] == "INCONSISTENT"


# ─── Step 17.A4: sample-bias guard (OUTLIER_DRIVEN) tests ────────────────────


def test_max_quarter_concentration_zero_when_no_data():
    per_q = [
        {"quarter": i, "sign": "NO_DATA", "realized_pnl": 0.0}
        for i in range(1, 5)
    ]
    pct, qid = bs._max_quarter_concentration(per_q)
    assert pct == 0.0 and qid == 0


def test_max_quarter_concentration_even_distribution():
    """4 quarters each 25% → 25.0% max."""
    per_q = [
        _stub_quarter(10, 1.0, 1),
        _stub_quarter(10, 1.0, 2),
        _stub_quarter(10, 1.0, 3),
        _stub_quarter(10, 1.0, 4),
    ]
    pct, qid = bs._max_quarter_concentration(per_q)
    assert pct == pytest.approx(25.0, abs=1e-6)


def test_classify_outlier_driven_when_single_quarter_dominates():
    """Q4 contributes ≥60% of |total| → OUTLIER_DRIVEN (Step 14B Q11 pattern).

    pnls: +50, +50, +50, +500 → total abs 650, Q4 = 500/650 = 76.9% > 60%.
    Tanpa guard ini akan jadi STABLE_POSITIVE (4 quarters all positive).
    """
    per_q = [
        _stub_quarter(10, 0.5, 1),    # pnl 50
        _stub_quarter(10, 0.5, 2),    # pnl 50
        _stub_quarter(10, 0.5, 3),    # pnl 50
        _stub_quarter(10, 5.0, 4),    # pnl 500 ← outlier
    ]
    cls = bs.classify(per_q, n_total_closed=40)
    assert cls["verdict"] == "OUTLIER_DRIVEN", f"got {cls}"
    assert cls["max_quarter"] == 4
    assert cls["max_quarter_concentration_pct"] >= 60.0


def test_classify_stable_when_below_outlier_threshold():
    """Q1 at 50% concentration → still STABLE (threshold 60%).

    pnls: +200, NO_DATA, +150, +50 → total abs 400, Q1 = 50% < 60%.
    """
    per_q = [
        _stub_quarter(10, 2.0, 1),    # pnl 200, 50%
        _stub_quarter(0, 0.0, 2),     # NO_DATA
        _stub_quarter(15, 1.5, 3),    # pnl 150, 37.5%
        _stub_quarter(12, 0.5, 4),    # pnl 50, 12.5%
    ]
    cls = bs.classify(per_q, n_total_closed=37)
    assert cls["verdict"] == "STABLE_POSITIVE"


def test_classify_outlier_takes_priority_over_inconsistent():
    """OUTLIER_DRIVEN detected before INCONSISTENT (sparse quarter or mixed
    signs) — sample-bias is the more honest verdict.
    """
    per_q = [
        _stub_quarter(10, -0.3, 1),   # pnl -30
        _stub_quarter(10, +5.0, 2),   # pnl +500 (outlier, ~80%)
        _stub_quarter(10, -0.4, 3),   # pnl -40
        _stub_quarter(10, -0.5, 4),   # pnl -50
    ]
    cls = bs.classify(per_q, n_total_closed=40)
    assert cls["verdict"] == "OUTLIER_DRIVEN"
    assert cls["max_quarter"] == 2


def test_classify_outlier_blocked_by_insufficient_sample():
    """Total closed < 30 → INSUFFICIENT_SAMPLE wins over OUTLIER_DRIVEN."""
    per_q = [
        _stub_quarter(2, 5.0, 1),     # outlier-shaped but tiny sample
        _stub_quarter(2, 0.1, 2),
        _stub_quarter(2, 0.1, 3),
        _stub_quarter(2, 0.1, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=8)
    assert cls["verdict"] == "INSUFFICIENT_SAMPLE"


def test_classify_majority_negative_when_3pos_vs_9neg():
    """Step 18 bug fix: n_pos=3, n_neg=9 should be STABLE_NEGATIVE, not POSITIVE.

    Pre-fix: classifier checked n_pos≥3 first → false STABLE_POSITIVE.
    Post-fix: requires majority (n_pos > n_neg or n_neg > n_pos).
    """
    per_q = (
        [_stub_quarter(10, +0.5, i) for i in range(1, 4)]  # 3 small POS
        + [_stub_quarter(10, -1.0, i) for i in range(4, 13)]  # 9 NEG
    )
    cls = bs.classify(per_q, n_total_closed=120)
    # n_pos=3, n_neg=9 → STABLE_NEGATIVE (majority); was bug pre-fix
    assert cls["verdict"] == "STABLE_NEGATIVE", f"got {cls}"


def test_classify_tie_returns_inconsistent():
    """n_pos == n_neg → INCONSISTENT (no clear majority)."""
    per_q = [
        _stub_quarter(10, +0.5, 1),
        _stub_quarter(10, +0.5, 2),
        _stub_quarter(10, +0.5, 3),
        _stub_quarter(10, -0.5, 4),
        _stub_quarter(10, -0.5, 5),
        _stub_quarter(10, -0.5, 6),
    ]
    cls = bs.classify(per_q, n_total_closed=60)
    # n_pos=3, n_neg=3, no majority → INCONSISTENT
    assert cls["verdict"] == "INCONSISTENT"


def test_classify_stable_negative_three_of_four():
    per_q = [
        _stub_quarter(10, -1.0, 1),
        _stub_quarter(10, -2.0, 2),
        _stub_quarter(10, -0.5, 3),
        _stub_quarter(10, +0.3, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=40)
    assert cls["verdict"] == "STABLE_NEGATIVE"
    assert cls["sign_consistency"] == "1/4"  # n_pos / qwd


def test_classify_no_data_does_not_skew_negative():
    """Worst-case test pengguna explicit: [+, NO_DATA, NO_DATA, +] dengan
    cumulative n_trades ≥ 30 — quarters_with_data = 2 < 3 → BUKAN
    STABLE_POSITIVE, melainkan INSUFFICIENT_SAMPLE_DISTRIBUTION.
    """
    per_q = [
        _stub_quarter(20, +1.0, 1),
        _stub_quarter(0, 0.0, 2),
        _stub_quarter(0, 0.0, 3),
        _stub_quarter(15, +1.0, 4),
    ]
    cls = bs.classify(per_q, n_total_closed=35)
    assert cls["verdict"] == "INSUFFICIENT_SAMPLE_DISTRIBUTION"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
