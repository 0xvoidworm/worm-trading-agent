"""Tests untuk scripts/signal_distribution_audit.py — Step 6.

Seed signal_events dengan distribusi terkontrol → verify histogram counting,
percentile cuts, per-pair/direction breakdown.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage  # noqa: E402
from scripts.signal_distribution_audit import (  # noqa: E402
    _parse_direction,
    _parse_t,
    _percentile,
    audit_signals,
    write_outputs,
)


def _sig(t_val: float, symbol: str, ts: datetime,
         direction: str = "trending_up") -> dict:
    payload = f"regime A->trending t={t_val:.2f} to={direction}"
    raw = f"REGIME_CHANGE|{symbol}|{ts.isoformat()}|{payload}".encode()
    return {
        "id": hashlib.sha256(raw).hexdigest(),
        "ts": ts,
        "event_type": "REGIME_CHANGE",
        "symbol": symbol,
        "severity": "info",
        "payload": payload,
    }


def test_parse_t_extracts_absolute_value():
    assert _parse_t("regime t=8.50 to=trending_up") == 8.5
    assert _parse_t("t=-12.3") == 12.3
    assert _parse_t(None) is None
    assert _parse_t("no t-stat here") is None
    assert _parse_t("t=abc") is None


def test_parse_direction():
    assert _parse_direction("foo to=trending_up bar") == "LONG"
    assert _parse_direction("foo to=trending_down bar") == "SHORT"
    assert _parse_direction("no direction") is None
    assert _parse_direction(None) is None


def test_percentile_linear_interp():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 0) == 1.0
    assert _percentile(vals, 100) == 5.0
    assert _percentile(vals, 50) == 3.0
    assert abs(_percentile(vals, 25) - 2.0) < 1e-9
    assert _percentile([], 50) == 0.0
    assert _percentile([7.0], 95) == 7.0


def test_audit_counts_histogram_and_breakdowns(tmp_path):
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    base = datetime(2026, 3, 1, 12, 0, 0)
    rows = []
    # 5 signals BTC LONG: t = 1, 3, 5, 7, 9
    for i, t in enumerate([1.0, 3.0, 5.0, 7.0, 9.0]):
        rows.append(_sig(t, "BTC/USDT",
                         base.replace(day=1 + i), "trending_up"))
    # 3 signals ETH SHORT: t = 2, 4, 6
    for i, t in enumerate([2.0, 4.0, 6.0]):
        rows.append(_sig(t, "ETH/USDT",
                         base.replace(day=1 + i), "trending_down"))
    s.upsert_signals(rows)
    s.close()

    audit = audit_signals(
        db_path=db,
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 31),
        signal_type="REGIME_CHANGE",
        buckets=[0, 2, 4, 6, 8, 10],
    )

    assert audit["total_signals"] == 8
    assert audit["stats"]["n_with_t"] == 8
    assert audit["stats"]["n_unparseable"] == 0
    assert audit["by_pair"] == {"BTC/USDT": 5, "ETH/USDT": 3}
    assert audit["by_direction"]["LONG"] == 5
    assert audit["by_direction"]["SHORT"] == 3
    assert audit["by_direction"]["UNKNOWN"] == 0

    # Histogram buckets [0,2)=1 [2,4)=2 [4,6)=2 [6,8)=2 [8,10)=1 overflow=0
    counts = [h["count"] for h in audit["histogram"]]
    assert counts == [1, 2, 2, 2, 1, 0]

    # 8 sorted values [1,2,3,4,5,6,7,9] → median = (4+5)/2 = 4.5
    assert abs(audit["stats"]["median"] - 4.5) < 1e-9
    assert audit["stats"]["min"] == 1.0
    assert audit["stats"]["max"] == 9.0


def test_audit_recommended_thresholds_match_percentiles(tmp_path):
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    rows = [
        _sig(float(i), "BTC/USDT",
             datetime(2026, 3, 1, i % 24, 0), "trending_up")
        for i in range(1, 21)  # t=1..20, distinct id via different ts
    ]
    s.upsert_signals(rows)
    s.close()

    audit = audit_signals(
        db_path=db,
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),
        signal_type="REGIME_CHANGE",
        buckets=[0, 5, 10, 15, 20],
    )
    assert audit["total_signals"] == 20
    rt = audit["recommended_thresholds"]
    # Linear-interp percentiles via k=(n-1)*pct/100 over sorted [1..20]:
    # p75 = 15.25, p90 = 18.10, p95 = 19.05
    assert abs(rt["top_25pct_cut"] - 15.25) < 0.01
    assert abs(rt["top_10pct_cut"] - 18.10) < 0.01
    assert abs(rt["top_5pct_cut"] - 19.05) < 0.01


def test_audit_sparsity_calculation(tmp_path):
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    # 4 signals across 2 distinct days in a 5-day window
    s.upsert_signals([
        _sig(8.0, "BTC/USDT", datetime(2026, 3, 1, 10, 0)),
        _sig(8.0, "BTC/USDT", datetime(2026, 3, 1, 14, 0)),
        _sig(8.0, "ETH/USDT", datetime(2026, 3, 3, 9, 0)),
        _sig(8.0, "ETH/USDT", datetime(2026, 3, 3, 11, 0)),
    ])
    s.close()

    audit = audit_signals(
        db_path=db,
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 5),
        signal_type="REGIME_CHANGE",
        buckets=[0, 5, 10],
    )
    sp = audit["sparsity"]
    assert sp["n_days_with_signal"] == 2
    assert sp["n_days_total"] == 5
    assert abs(sp["pct_active_days"] - 40.0) < 1e-9
    assert abs(sp["avg_signals_per_day"] - 0.8) < 1e-9


def test_write_outputs_emits_both_files(tmp_path):
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    s.upsert_signals([
        _sig(8.0, "BTC/USDT", datetime(2026, 3, 1, 10, 0)),
    ])
    s.close()
    audit = audit_signals(
        db_path=db,
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),
        signal_type="REGIME_CHANGE",
        buckets=[0, 5, 10],
    )
    out_dir = tmp_path / "out"
    write_outputs(audit, out_dir)
    assert (out_dir / "distribution.json").exists()
    assert (out_dir / "histogram.md").exists()
    md = (out_dir / "histogram.md").read_text()
    assert "Signal Distribution Audit" in md
    assert "Recommended Thresholds" in md


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
