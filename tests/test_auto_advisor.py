"""Tests untuk run_advisor threshold parameter — Step 7 sweep enabler.

Memastikan:
  - Default threshold tetap REGIME_T_THRESHOLD (8.0) → production behavior
    UNCHANGED.
  - Custom threshold parameter mempengaruhi candidate filter (signal di bawah
    threshold di-skip → WAIT, di atas threshold lolos → ENTRY proposal).
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.auto_advisor import (  # noqa: E402
    REGIME_T_THRESHOLD,
    run_advisor_cycle,
    run_auto_advisor,
)
from data_pipe.storage import Storage  # noqa: E402


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT"],
    "tier2": [],
    "portfolio": {"starting_capital": 10000.0},
}

# Multi-pair config for run_advisor_cycle tests (Step 8)
_CFG_MULTI = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
    "tier2": [],
    "portfolio": {
        "starting_capital": 10000.0,
        "max_concurrent_trades": 3,
    },
}


def _seed_btc_with_signal(s: Storage, t_val: float) -> None:
    """Insert 30+ bars (untuk sizing) + 1 fresh REGIME_CHANGE signal t=t_val."""
    base = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(hours=40)
    rows = []
    for i in range(40):
        rows.append({
            "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
            "ts": base + pd.Timedelta(hours=i),
            "open": 50000, "high": 50100, "low": 49900, "close": 50000,
            "volume": 1.0,
        })
    s.upsert_ohlcv(rows)

    sig_ts = base + pd.Timedelta(hours=39)  # < 4h ago = within window
    payload = f"from=ranging;to=trending_up;t={t_val:.2f}"
    raw = f"REGIME_CHANGE|BTC/USDT|{sig_ts.isoformat()}|{payload}".encode()
    s.upsert_signals([{
        "id": hashlib.sha256(raw).hexdigest()[:16],
        "ts": sig_ts.to_pydatetime(),
        "event_type": "REGIME_CHANGE",
        "symbol": "BTC/USDT",
        "severity": "info",
        "payload": payload,
    }])


def test_run_auto_advisor_default_threshold_value():
    """Default threshold module-level = 8.0 (production daemon behavior)."""
    assert REGIME_T_THRESHOLD == 8.0


def test_run_auto_advisor_default_blocks_below_8():
    """Signal t=5 < default threshold 8.0 → WAIT (no ENTRY)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_signal(s, t_val=5.0)
            verdict = run_auto_advisor(s, _CFG)  # default threshold
            assert verdict["verdict"] == "WAIT"


def test_run_auto_advisor_custom_threshold_lets_lower_signal_through():
    """Sama signal t=5, tapi threshold=4 → ENTRY."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_signal(s, t_val=5.0)
            verdict = run_auto_advisor(s, _CFG, threshold=4.0)
            assert verdict["verdict"] == "ENTRY"
            assert verdict["proposed"]["pair"] == "BTC/USDT"
            assert verdict["proposed"]["direction"] == "LONG"


# ─── Step 8: run_advisor_cycle multi-trade orchestrator ──────────────────

def _seed_multi_pair_ohlcv(s: Storage, pairs: list[str]) -> pd.Timestamp:
    """Seed 40 bars × N pairs supaya semua punya entry_price + sizing valid."""
    base = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(hours=40)
    rows = []
    for pair in pairs:
        for i in range(40):
            rows.append({
                "exchange": "bybit", "symbol": pair, "timeframe": "1h",
                "ts": base + pd.Timedelta(hours=i),
                "open": 100, "high": 101, "low": 99, "close": 100,
                "volume": 1.0,
            })
    s.upsert_ohlcv(rows)
    # Funding + news supaya health=OK
    from datetime import datetime, timedelta as _td, timezone as _tz
    now_dt = datetime.now(_tz.utc)
    s.upsert_funding([
        {"exchange": "bybit", "symbol": p, "ts": base + pd.Timedelta(hours=39),
         "funding": 0.0001, "mark_price": 100}
        for p in pairs
    ])
    from data_pipe.ingest_news import FEEDS
    s.upsert_news([
        {"id": f"n-{i}", "source": name, "title": f"h{i}", "url": f"u{i}",
         "published_at": now_dt - _td(hours=1),
         "fetched_at": now_dt, "symbols": "BTC,ETH,SOL,XRP"}
        for i, (name, _) in enumerate(FEEDS)
    ])
    return base


def _inject_regime_signal(
    s: Storage, sym: str, t_val: float, *, label: str = "x",
    age_hours: float = 0.5,
) -> None:
    """Inject 1 fresh REGIME_CHANGE LONG signal."""
    sig_ts = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(
        hours=age_hours,
    )
    payload = f"from=ranging;to=trending_up;t={t_val:.2f}"
    raw = f"REGIME_CHANGE|{sym}|{sig_ts.isoformat()}|{payload}|{label}".encode()
    s.upsert_signals([{
        "id": hashlib.sha256(raw).hexdigest()[:16],
        "ts": sig_ts.to_pydatetime(),
        "event_type": "REGIME_CHANGE",
        "symbol": sym,
        "severity": "info",
        "payload": payload,
    }])


def test_advisor_cycle_opens_multiple_trades_in_one_call():
    """0 OPEN + 3 fresh high-conf signals di 3 pair berbeda → 3 ENTRY."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
                _inject_regime_signal(s, sym, t_val=12.0, label=sym)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            assert result["n_entries"] == 3, (
                f"expected 3 entries, got {result['n_entries']}: "
                f"{[r.get('reason') for r in result['entries']]}"
            )
            assert result["n_exits"] == 0
            open_pairs = set(s.get_trades(status="OPEN")["pair"].astype(str))
            assert open_pairs == {"BTC/USDT", "ETH/USDT", "SOL/USDT"}


def test_advisor_cycle_single_signal_then_wait_breaks_loop():
    """0 OPEN + 1 signal → 1 ENTRY, second iter advisor returns WAIT, loop break."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            _inject_regime_signal(s, "BTC/USDT", t_val=12.0)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            assert result["n_entries"] == 1
            assert result["n_exits"] == 0


def test_advisor_cycle_concurrent_cap_blocks_extra_entries():
    """3 OPEN trades sudah ada (concurrent cap penuh) → meski signal fresh,
    sizing returns None (cap A/cap C exceeded) → ENTRY pass = 0."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            # Pre-fill 3 OPEN trades to exhaust concurrent cap
            for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
                s.add_trade(pair=sym, direction="LONG",
                            entry_price=100, size=1)
            # Inject signal for 4th pair (different pair → per-symbol cap OK)
            _inject_regime_signal(s, "XRP/USDT", t_val=12.0)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            # No new entries — sizing concurrent cap rejects
            assert result["n_entries"] == 0
            # 3 OPEN unchanged (XRP not added)
            assert len(s.get_trades(status="OPEN")) == 3


def test_advisor_cycle_processes_exits_before_entries():
    """1 OPEN trade hit SL + 1 fresh ENTRY signal → cycle returns 1 exit + 1 entry,
    EXIT processed FIRST (slot freed before ENTRY pass tries cap)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            # OPEN BTC at high entry → -50% loss → SL fire (latest close=100)
            s.add_trade(pair="BTC/USDT", direction="LONG",
                        entry_price=200.0, size=1.0)
            # Fresh signal on ETH (different pair)
            _inject_regime_signal(s, "ETH/USDT", t_val=12.0)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            assert result["n_exits"] == 1
            assert result["n_entries"] == 1
            # BTC closed, ETH opened
            open_pairs = set(s.get_trades(status="OPEN")["pair"].astype(str))
            assert "BTC/USDT" not in open_pairs
            assert "ETH/USDT" in open_pairs


def test_advisor_cycle_signals_below_threshold_zero_entries():
    """Signal t=3 < threshold 8 → advisor WAIT immediate → 0 entries."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            _inject_regime_signal(s, "BTC/USDT", t_val=3.0)  # below default 8
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            assert result["n_entries"] == 0
            assert result["n_exits"] == 0


def test_advisor_cycle_per_pair_dedup_within_same_cycle():
    """2 fresh signals di SAME pair → cycle hanya buka 1 trade (per-pair cap),
    iter ke-2 advisor skip pair yg sudah OPEN dari iter 1 → WAIT, loop break."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_multi_pair_ohlcv(s, _CFG_MULTI["tier1"])
            _inject_regime_signal(s, "BTC/USDT", t_val=12.0, label="a",
                                   age_hours=0.5)
            _inject_regime_signal(s, "BTC/USDT", t_val=10.0, label="b",
                                   age_hours=1.0)
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            result = run_advisor_cycle(s, _CFG_MULTI, data_dir)
            assert result["n_entries"] == 1
            # Hanya 1 OPEN BTC trade (bukan 2)
            assert len(s.get_trades(status="OPEN")) == 1


# ─── Step 14B: FUNDING_FLIP detector_whitelist mode ──────────────────────

def _seed_btc_with_funding_flip(
    s: Storage, *, curr_pct: float, prev_pct: float = 0.005,
    age_hours: float = 0.5,
) -> None:
    """Insert OHLCV + 1 fresh FUNDING_FLIP signal.

    `curr_pct` and `prev_pct` are in PERCENT (already × 100), matching
    signal_log.detect_funding_events payload format `prev=X%;curr=Y%`.
    """
    base = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(hours=40)
    rows = []
    for i in range(40):
        rows.append({
            "exchange": "bybit", "symbol": "BTC/USDT", "timeframe": "1h",
            "ts": base + pd.Timedelta(hours=i),
            "open": 50000, "high": 50100, "low": 49900, "close": 50000,
            "volume": 1.0,
        })
    s.upsert_ohlcv(rows)

    sig_ts = pd.Timestamp.utcnow().tz_localize(None).floor("h") - pd.Timedelta(
        hours=age_hours,
    )
    payload = f"prev={prev_pct:.4f}%;curr={curr_pct:.4f}%"
    raw = f"FUNDING_FLIP|BTC/USDT|{sig_ts.isoformat()}|{payload}".encode()
    s.upsert_signals([{
        "id": hashlib.sha256(raw).hexdigest()[:16],
        "ts": sig_ts.to_pydatetime(),
        "event_type": "FUNDING_FLIP",
        "symbol": "BTC/USDT",
        "severity": "warn",
        "payload": payload,
    }])


_CFG_FFLIP = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["BTC/USDT"],
    "tier2": [],
    "portfolio": {"starting_capital": 10000.0},
    "advisor": {"detector_whitelist": ["FUNDING_FLIP"]},
}


def test_funding_flip_in_band_negative_curr_emits_short_entry():
    """curr=-0.020% (in [0.01%, 0.03%)) → SHORT ENTRY."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_funding_flip(s, curr_pct=-0.020)
            verdict = run_auto_advisor(s, _CFG_FFLIP)
            assert verdict["verdict"] == "ENTRY"
            assert verdict["proposed"]["pair"] == "BTC/USDT"
            assert verdict["proposed"]["direction"] == "SHORT"


def test_funding_flip_in_band_positive_curr_emits_long_entry():
    """curr=+0.025% → LONG ENTRY."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_funding_flip(
                s, curr_pct=0.025, prev_pct=-0.005,
            )
            verdict = run_auto_advisor(s, _CFG_FFLIP)
            assert verdict["verdict"] == "ENTRY"
            assert verdict["proposed"]["direction"] == "LONG"


def test_funding_flip_out_of_band_too_small_emits_wait():
    """curr=+0.005% (< band_min 0.01%) → WAIT (filtered out)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_funding_flip(s, curr_pct=0.005)
            verdict = run_auto_advisor(s, _CFG_FFLIP)
            assert verdict["verdict"] == "WAIT"


def test_funding_flip_out_of_band_too_large_emits_wait():
    """curr=+0.10% (> band_max 0.03%) → WAIT (filtered out)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_funding_flip(s, curr_pct=0.10, prev_pct=-0.005)
            verdict = run_auto_advisor(s, _CFG_FFLIP)
            assert verdict["verdict"] == "WAIT"


def test_funding_flip_whitelist_ignores_regime_signals():
    """FUNDING_FLIP whitelist mode: hi-conf REGIME signal di-skip → WAIT."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_signal(s, t_val=20.0)  # very hi-conf REGIME
            verdict = run_auto_advisor(s, _CFG_FFLIP)
            # No FUNDING_FLIP signals exist → WAIT
            assert verdict["verdict"] == "WAIT"


def test_default_whitelist_preserves_regime_path():
    """No advisor block in cfg → default REGIME_CHANGE path (Step <14B behavior)."""
    cfg_no_advisor = {
        "exchange": "bybit", "timeframe": "1h",
        "tier1": ["BTC/USDT"], "tier2": [],
        "portfolio": {"starting_capital": 10000.0},
    }
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_signal(s, t_val=12.0)
            verdict = run_auto_advisor(s, cfg_no_advisor)
            assert verdict["verdict"] == "ENTRY"
            assert verdict["proposed"]["direction"] == "LONG"


def test_funding_flip_custom_band_widens_acceptance():
    """band_max=0.001 (0.1%) lets curr=+0.05% through (would normally be filtered)."""
    cfg = {
        **_CFG_FFLIP,
        "advisor": {
            "detector_whitelist": ["FUNDING_FLIP"],
            "funding_flip": {"band_min": 0.0001, "band_max": 0.001},
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_btc_with_funding_flip(s, curr_pct=0.05, prev_pct=-0.005)
            verdict = run_auto_advisor(s, cfg)
            assert verdict["verdict"] == "ENTRY"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
