"""Live log-spread (β-weighted) signal computation.

Parallel to spot_pairs.compute_pair_zsignal (raw ratio), but using:
  spread = log(A) - β · log(B)
where β is the cached hedge ratio from Engle-Granger audit.

Used by ensemble_signals to compute signals from log-spread strategy.
Critical for pairs validated under LOG_SPREAD_VERDICT (DOGE/BTC, TRX/ETH,
POL/ARB) where raw-ratio gives wrong answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .spot_pairs import PAIR_COINT_STATUS


DEFAULT_LOOKBACK_HOURS = 24 * 30
DEFAULT_Z_THRESHOLD = 2.0


@dataclass
class LogSpreadSignal:
    pair_a: str
    pair_b: str
    beta: float
    spread_now: float
    spread_mean: float
    spread_std: float
    z: float
    direction: str         # "BUY_A" / "BUY_B" / "NEUTRAL"
    n_bars: int
    strategy: str = "LOG_SPREAD"


def _get_cached_beta(pair_a: str, pair_b: str) -> Optional[float]:
    """Lookup β from cointegration audit cache, fall back to None."""
    status = PAIR_COINT_STATUS.get((pair_a, pair_b))
    if status:
        return float(status.get("beta", 1.0))
    return None


def compute_log_spread_signal(
    storage,
    pair_a: str,
    pair_b: str,
    exchange: str = "bybit",
    timeframe: str = "1h",
    lookback_h: int = DEFAULT_LOOKBACK_HOURS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
    fit_beta_if_missing: bool = True,
) -> Optional[LogSpreadSignal]:
    """Compute current log-spread z-score signal for a pair.

    Returns None if insufficient data or no β cached + fit_beta_if_missing=False.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_h)
    df_a = storage._conn.execute(
        "SELECT ts, close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? AND ts >= ? ORDER BY ts",
        [exchange, pair_a, timeframe, cutoff],
    ).fetchdf()
    df_b = storage._conn.execute(
        "SELECT ts, close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? AND ts >= ? ORDER BY ts",
        [exchange, pair_b, timeframe, cutoff],
    ).fetchdf()
    if df_a.empty or df_b.empty:
        return None

    merged = pd.merge(
        df_a.rename(columns={"close": "a"}),
        df_b.rename(columns={"close": "b"}),
        on="ts", how="inner",
    )
    if len(merged) < 50:
        return None

    # Get β: cached first, fit if missing
    beta = _get_cached_beta(pair_a, pair_b)
    if beta is None:
        if not fit_beta_if_missing:
            return None
        # Fit β from current window (no look-ahead since we use it from past to now)
        log_a_full = np.log(merged["a"].values)
        log_b_full = np.log(merged["b"].values)
        beta = float(np.polyfit(log_b_full, log_a_full, 1)[0])

    # Compute spread
    log_a = np.log(merged["a"].values)
    log_b = np.log(merged["b"].values)
    spread = log_a - beta * log_b
    mean = float(np.mean(spread))
    std = float(np.std(spread))
    if std <= 0:
        return None
    now = float(spread[-1])
    z = (now - mean) / std

    direction = "NEUTRAL"
    if z <= -z_threshold:
        direction = "BUY_A"   # A relatively cheap
    elif z >= z_threshold:
        direction = "BUY_B"   # B relatively cheap (A overvalued)

    return LogSpreadSignal(
        pair_a=pair_a, pair_b=pair_b,
        beta=beta, spread_now=now, spread_mean=mean, spread_std=std,
        z=z, direction=direction, n_bars=len(merged),
    )
