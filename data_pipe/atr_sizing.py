"""ATR-based volatility-adjusted position sizing.

Industry standard from quant lit: high-vol coins get SMALLER dollar position
so that DOLLAR RISK per position is constant across coins.

Formula:
  base_risk_usdt = equity × risk_pct_per_trade / 100
  position_usdt = base_risk_usdt / (atr_pct × sl_atr_multiplier)

Where:
  atr_pct = ATR / price (volatility normalized)
  sl_atr_multiplier = e.g. 2.0 (stop loss at 2× ATR below entry)

Example:
  equity = $50, risk_pct = 5% → base_risk = $2.50
  AVAX atr_pct = 0.04 (4% daily move) → position = $2.50 / (0.04 × 2.0) = $31.25
  But capped by max_position_pct (30% × $50 = $15) → $15
  BNB atr_pct = 0.015 (1.5% daily) → position = $2.50 / (0.015 × 2.0) = $83
  But capped by $15 → $15

For LOW-volatility coins, ATR-sizing wants MORE exposure. Capped by max_pos.
For HIGH-volatility coins, ATR-sizing wants LESS. Avoids over-risking.

Falls back to base_per_position if ATR can't be computed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd


DEFAULT_ATR_PERIOD = 14
DEFAULT_SL_ATR_MULT = 2.0
DEFAULT_RISK_PCT = 5.0  # risk_pct_per_trade in cfg.portfolio


def compute_atr_pct(
    storage,
    symbol: str,
    exchange: str = "bybit",
    timeframe: str = "1h",
    atr_period: int = DEFAULT_ATR_PERIOD,
    lookback_bars: int = 24 * 30,  # 30 days
) -> Optional[float]:
    """Compute ATR / price ratio (normalized volatility).

    Returns None if insufficient data.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_bars)
    df = storage._conn.execute(
        "SELECT ts, high, low, close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? AND ts >= ? "
        "ORDER BY ts",
        [exchange, symbol, timeframe, cutoff],
    ).fetchdf()
    if df.empty or len(df) < atr_period + 1:
        return None
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(atr_period).mean().iloc[-1])
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return None
    return atr / last_close


def compute_position_size_atr(
    equity: float,
    atr_pct: float,
    risk_pct_per_trade: float = DEFAULT_RISK_PCT,
    sl_atr_multiplier: float = DEFAULT_SL_ATR_MULT,
    max_position_usdt: float = float("inf"),
    min_position_usdt: float = 5.5,
) -> dict:
    """ATR-volatility-adjusted position sizing.

    Returns dict:
        atr_pct, risk_amount, raw_size_usdt, capped_size_usdt, capped_reason
    """
    risk_amount = equity * risk_pct_per_trade / 100.0
    if atr_pct <= 0:
        return {
            "atr_pct": atr_pct,
            "risk_amount": risk_amount,
            "raw_size_usdt": 0,
            "capped_size_usdt": 0,
            "capped_reason": "atr_pct invalid",
        }

    # ATR-based raw size
    raw_size = risk_amount / (atr_pct * sl_atr_multiplier)
    capped_reason = None
    capped = raw_size
    if capped > max_position_usdt:
        capped = max_position_usdt
        capped_reason = f"max_position cap {max_position_usdt:.2f}"
    if capped < min_position_usdt:
        # Either fail or scale UP to min (risk relaxed)
        capped = min_position_usdt
        capped_reason = f"min_notional floor {min_position_usdt:.2f}"

    return {
        "atr_pct": atr_pct,
        "risk_amount": risk_amount,
        "raw_size_usdt": round(raw_size, 4),
        "capped_size_usdt": round(capped, 4),
        "capped_reason": capped_reason,
    }


def atr_sized_position(
    storage,
    symbol: str,
    cfg: dict,
    equity: float,
    max_position_usdt: float,
) -> dict:
    """End-to-end: fetch ATR for symbol, compute ATR-sized position.

    Falls back to max_position_usdt if ATR unavailable.
    """
    pcfg = cfg.get("portfolio") or {}
    risk_pct = float(pcfg.get("risk_pct_per_trade", DEFAULT_RISK_PCT))
    atr_pct = compute_atr_pct(storage, symbol)
    if atr_pct is None:
        return {
            "atr_pct": None,
            "raw_size_usdt": max_position_usdt,
            "capped_size_usdt": max_position_usdt,
            "capped_reason": "ATR unavailable — fallback to max_position",
        }
    return compute_position_size_atr(
        equity=equity,
        atr_pct=atr_pct,
        risk_pct_per_trade=risk_pct,
        sl_atr_multiplier=DEFAULT_SL_ATR_MULT,
        max_position_usdt=max_position_usdt,
    )
