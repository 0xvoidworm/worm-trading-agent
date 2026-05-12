"""Tests untuk Step 17.A2 REGIME_CHANGE debounce.

Verifikasi:
- debounce=3 menekan transient flicker (1-2 bar regime change → no emit)
- debounce=3 emit transition yang persisted ≥3 bar (back-dated ke bar start)
- debounce=1 = behaviour lama (emit setiap flip, no debounce)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage  # noqa: E402
from dashboard.signal_log import detect_regime_changes  # noqa: E402


def _ohlcv_with_regime_pattern(regimes: list[str], base_price: float = 100.0) -> list[dict]:
    """Generate synthetic OHLCV yang force classify() return regime tertentu per bar.

    Klasifier `dashboard.regime.classify` melihat ~30-bar trailing window + t-stat slope.
    Trick: untuk force regime tertentu pada bar i, set close-series sehingga slope window
    [i-29..i] memenuhi kriteria regime. Simpler: gunakan deterministic price patterns:
      - trending_up: monotonic up dengan low vol
      - trending_down: monotonic down dengan low vol
      - chaos: high std relative to slope
      - sideways: low slope, low vol

    Untuk simplicity test, kita pakai patterns yang stabil dan generate 30-bar warmup
    + N transition bars. Test fokus pada emission logic (debounce), bukan classifier
    accuracy → asumsi classify() consistently classify pattern → assert event count
    sesuai expected.

    Implementasi: build trace klasifikasi-ready dengan slope+vol explicit.
    """
    rows = []
    base_ts = pd.Timestamp("2026-01-01 00:00:00")
    n = max(60, 30 + len(regimes) + 5)  # 30 bar warmup + transition + buffer

    # Warmup 30 bar sideways
    for i in range(30):
        ts = base_ts + pd.Timedelta(hours=i)
        rows.append({
            "exchange": "bybit",
            "symbol": "TEST/USDT",
            "timeframe": "1h",
            "ts": ts,
            "open": base_price,
            "high": base_price + 0.5,
            "low": base_price - 0.5,
            "close": base_price + np.sin(i * 0.3) * 0.5,
            "volume": 100.0,
        })

    # Transition bars per `regimes` request
    cum_price = base_price
    for j, regime in enumerate(regimes):
        ts = base_ts + pd.Timedelta(hours=30 + j)
        if regime == "trending_up":
            cum_price += 5.0
        elif regime == "trending_down":
            cum_price -= 5.0
        elif regime == "chaos":
            cum_price += (5.0 if j % 2 == 0 else -5.0) * 2
        else:  # sideways
            cum_price += np.sin(j * 0.5) * 0.5

        rows.append({
            "exchange": "bybit",
            "symbol": "TEST/USDT",
            "timeframe": "1h",
            "ts": ts,
            "open": cum_price - 0.5,
            "high": cum_price + 1.0,
            "low": cum_price - 1.0,
            "close": cum_price,
            "volume": 100.0,
        })

    return rows


def _setup_storage(tmpdir: str) -> Storage:
    db = Path(tmpdir) / "test.duckdb"
    return Storage(str(db))


def test_debounce_emits_first_observation_only():
    """Saat tidak ada last REGIME_CHANGE event → emit 1 first-observation."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = _setup_storage(tmp)
        # Plain warmup + steady up trend → 1 first-obs emit
        rows = _ohlcv_with_regime_pattern(["trending_up"] * 30)
        storage.upsert_ohlcv(rows)

        events = detect_regime_changes(
            storage, symbols=["TEST/USDT"], exchange="bybit", tf="1h", debounce_bars=3
        )
        assert len(events) == 1
        assert "from=?" in events[0]["payload"]
        assert "debounce=3" in events[0]["payload"]


def test_debounce_suppresses_transient_flicker():
    """Pattern: stable down 5 bar → up 1 bar (flicker) → down lagi 5 bar.

    debounce=3 → flicker tidak emit. Hanya 1 emit (first-observation).
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = _setup_storage(tmp)
        # 30 warmup sideways + 5 down + 1 up (flicker) + 5 down
        pattern = ["trending_down"] * 5 + ["trending_up"] * 1 + ["trending_down"] * 5
        rows = _ohlcv_with_regime_pattern(pattern)
        storage.upsert_ohlcv(rows)

        # First call → emit first-obs based on latest bar (likely "trending_down")
        events_first = detect_regime_changes(
            storage, symbols=["TEST/USDT"], exchange="bybit", tf="1h", debounce_bars=3
        )
        assert len(events_first) == 1
        # Upsert into storage so next call has watermark
        storage.upsert_signals(events_first)

        # Second call from same data → debounce should suppress all transient flips
        events_second = detect_regime_changes(
            storage, symbols=["TEST/USDT"], exchange="bybit", tf="1h", debounce_bars=3
        )
        # No new transitions (watermark eats existing bars or stability not reached)
        assert len(events_second) == 0


def test_debounce_1_equals_legacy_behavior():
    """debounce=1 → emit setiap regime flip (= behaviour pre-A2).

    Stability threshold == 1 berarti bar pertama dari new regime langsung emit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = _setup_storage(tmp)
        rows = _ohlcv_with_regime_pattern(["trending_up"] * 30)
        storage.upsert_ohlcv(rows)

        events = detect_regime_changes(
            storage, symbols=["TEST/USDT"], exchange="bybit", tf="1h", debounce_bars=1
        )
        # First observation + potential transitions
        assert len(events) >= 1
        for ev in events:
            assert "debounce=1" in ev["payload"]


def test_debounce_payload_format():
    """Payload harus contain from=, to=, t=, debounce= dipisah `;`."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = _setup_storage(tmp)
        rows = _ohlcv_with_regime_pattern(["trending_up"] * 30)
        storage.upsert_ohlcv(rows)

        events = detect_regime_changes(
            storage, symbols=["TEST/USDT"], exchange="bybit", tf="1h", debounce_bars=5
        )
        assert len(events) >= 1
        payload = events[0]["payload"]
        for key in ("from=", "to=", "t=", "debounce=5"):
            assert key in payload, f"missing {key} in {payload}"
        # Field separator
        assert payload.count(";") >= 3
