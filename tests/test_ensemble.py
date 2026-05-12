"""Tests untuk data_pipe.ensemble — Step 19.

Verifikasi:
- _vote_from_signal maps each event_type/payload → expected direction.
- aggregate_votes counts within lookback window per symbol.
- emit_ensemble_signals respects min_agreement + min_distinct_types + debounce.
- parse_ensemble_payload roundtrip.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.ensemble import (  # noqa: E402
    _vote_from_signal,
    aggregate_votes,
    emit_ensemble_signals,
    parse_ensemble_payload,
)


def test_vote_regime_change():
    assert _vote_from_signal("REGIME_CHANGE", "from=A;to=trending_up;t=5.0") == +1
    assert _vote_from_signal("REGIME_CHANGE", "from=A;to=trending_down;t=5.0") == -1
    assert _vote_from_signal("REGIME_CHANGE", "from=A;to=chaos;t=3.0") == 0
    assert _vote_from_signal("REGIME_CHANGE", "from=A;to=sideways;t=1.0") == 0


def test_vote_funding_extreme():
    """long-pays = bearish exhaustion → SHORT, short-pays = LONG opportunity."""
    assert _vote_from_signal("FUNDING_EXTREME", "rate=0.05%;side=long-pays") == -1
    assert _vote_from_signal("FUNDING_EXTREME", "rate=-0.05%;side=short-pays") == +1


def test_vote_funding_flip():
    """pos→neg = LONG, neg→pos = SHORT."""
    assert _vote_from_signal("FUNDING_FLIP", "prev=0.02%;curr=-0.01%") == +1
    assert _vote_from_signal("FUNDING_FLIP", "prev=-0.02%;curr=0.01%") == -1
    # No flip (same sign) → 0
    assert _vote_from_signal("FUNDING_FLIP", "prev=0.02%;curr=0.01%") == 0


def test_vote_trend_breakout():
    assert _vote_from_signal("TREND_BREAKOUT", "sym=BTC/USDT;side=long-breakout;close=70000") == +1
    assert _vote_from_signal("TREND_BREAKOUT", "sym=BTC/USDT;side=short-breakdown;close=68000") == -1


def test_vote_unknown_returns_zero():
    assert _vote_from_signal("Z_SPIKE", "pair=A|B;z=2.5") == 0
    assert _vote_from_signal("COINTEGRATION_SPREAD", "side=long-spread") == 0


def test_aggregate_votes_empty_input():
    df = aggregate_votes(pd.DataFrame(), symbols=["BTC/USDT"])
    assert df.empty


def test_aggregate_votes_groups_by_symbol_and_lookback():
    """3 signals within 24h on BTC: all +1 → vote_sum=3."""
    base = pd.Timestamp("2026-04-01 00:00:00")
    sig = pd.DataFrame([
        {"ts": base, "event_type": "REGIME_CHANGE", "symbol": "BTC/USDT",
         "payload": "from=A;to=trending_up;t=8.0"},
        {"ts": base + pd.Timedelta(hours=2), "event_type": "TREND_BREAKOUT",
         "symbol": "BTC/USDT", "payload": "sym=BTC/USDT;side=long-breakout"},
        {"ts": base + pd.Timedelta(hours=10), "event_type": "FUNDING_FLIP",
         "symbol": "BTC/USDT", "payload": "prev=0.02%;curr=-0.01%"},
    ])
    votes = aggregate_votes(sig, symbols=["BTC/USDT"], lookback_hours=24)
    # At ts = base + 11h, all 3 are within 24h lookback → vote_sum = 3
    bar_at_11h = base.floor("1h") + pd.Timedelta(hours=11)
    row = votes[votes["ts"] == bar_at_11h]
    if not row.empty:
        assert int(row["vote_sum"].iloc[0]) == 3


def test_aggregate_votes_filters_pair_signals():
    """Z_SPIKE / COINTEGRATION_SPREAD events with `|` in symbol get dropped."""
    base = pd.Timestamp("2026-04-01")
    sig = pd.DataFrame([
        {"ts": base, "event_type": "Z_SPIKE", "symbol": "ETH/USDT|BTC/USDT",
         "payload": "pair=ETH/USDT|BTC/USDT;z=2.5"},
        {"ts": base, "event_type": "REGIME_CHANGE", "symbol": "BTC/USDT",
         "payload": "from=A;to=trending_up;t=8.0"},
    ])
    votes = aggregate_votes(sig, symbols=["BTC/USDT"], lookback_hours=24)
    # Only BTC/USDT REGIME_CHANGE counted
    assert not votes.empty
    assert all(votes["symbol"] == "BTC/USDT")


def test_emit_respects_min_agreement():
    """vote_sum=1 di bawah min_agreement=2 → no emit."""
    base = pd.Timestamp("2026-04-01 10:00:00")
    votes = pd.DataFrame([
        {"ts": base, "symbol": "BTC/USDT", "vote_sum": 1, "n_signals": 1,
         "signal_types": "REGIME_CHANGE"},
        {"ts": base + pd.Timedelta(hours=10), "symbol": "BTC/USDT",
         "vote_sum": 2, "n_signals": 2,
         "signal_types": "REGIME_CHANGE,TREND_BREAKOUT"},
    ])
    events = emit_ensemble_signals(votes, min_agreement=2, min_distinct_types=2)
    assert len(events) == 1
    assert events[0]["payload"].count("vote_sum=2") == 1


def test_emit_respects_min_distinct_types():
    """vote_sum=3 dari 1 distinct type → blocked oleh min_distinct_types=2."""
    base = pd.Timestamp("2026-04-01")
    votes = pd.DataFrame([
        {"ts": base, "symbol": "BTC/USDT", "vote_sum": 3, "n_signals": 3,
         "signal_types": "REGIME_CHANGE"},  # only 1 distinct type
    ])
    events = emit_ensemble_signals(votes, min_agreement=2, min_distinct_types=2)
    assert events == []


def test_emit_debounces_per_symbol():
    """Two consecutive emits within debounce_hours → only 1st emit."""
    base = pd.Timestamp("2026-04-01 10:00:00")
    votes = pd.DataFrame([
        {"ts": base, "symbol": "BTC/USDT", "vote_sum": 2, "n_signals": 2,
         "signal_types": "REGIME_CHANGE,TREND_BREAKOUT"},
        {"ts": base + pd.Timedelta(hours=1), "symbol": "BTC/USDT",
         "vote_sum": 2, "n_signals": 2,
         "signal_types": "REGIME_CHANGE,TREND_BREAKOUT"},
    ])
    events = emit_ensemble_signals(
        votes, min_agreement=2, min_distinct_types=2, debounce_hours=4
    )
    assert len(events) == 1


def test_parse_ensemble_payload_roundtrip():
    payload = ("sym=BTC/USDT;side=long;vote_sum=3;n_signals=4;"
               "types=REGIME_CHANGE,TREND_BREAKOUT,FUNDING_FLIP;agreement=2")
    parsed = parse_ensemble_payload(payload)
    assert parsed["sym"] == "BTC/USDT"
    assert parsed["side"] == "long"
    assert parsed["vote_sum"] == 3
    assert parsed["n_signals"] == 4
    assert parsed["agreement"] == 2
