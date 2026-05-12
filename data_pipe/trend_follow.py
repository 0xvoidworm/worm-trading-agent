"""Trend-follow detector — Step 18 thesis (θ).

Class signal yang fundamentally berbeda dari thesis-thesis sebelumnya:
- FUNDING_FLIP (Step 14B/15 REJECTED) = funding-rate mean reversion
- COINTEGRATION (Step 17.B REJECTED) = pair-spread mean reversion
- **TREND_FOLLOW** = directional momentum breakout (LAWAN dari mean-revert)

Filosofi: buy strength (close > upper Bollinger), sell weakness (close < lower).
Exit via trailing-ATR stop atau SMA cross balik. Sizing volatility-adaptive
(ATR-based).

Signal flow:
1. Compute panel: SMA(20) ± 2×stdev (Bollinger), ATR(14).
2. Detect fresh crossings: prev close was inside band, current close outside.
3. Volatility filter: skip if ATR/price < min_atr_pct (dead vol).
4. Debounce: minimum gap antara consecutive emits per symbol.

Exit logic (delegated ke backtest_trend_follow.simulate_trend_trades):
- Stop loss: entry ± stop_atr_mult × ATR (default 2×)
- Trailing stop: drawdown dari peak/trough ≥ trail_atr_mult × ATR (default 3×)
- SMA cross: close re-crosses SMA in opposite direction (trend broken)
- Time stop: hold > max_hold_hours (default 168 = 1 week)
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


# Defaults — tuned vs Step 18 backtest, not hand-picked from sweep
BB_WINDOW = 20
BB_STD_MULT = 2.0
ATR_WINDOW = 14
MIN_ATR_PCT = 0.003       # 0.3% ATR/price minimum — avoid dead-vol periods
DEFAULT_DEBOUNCE = 4      # min bars antara emit per symbol


def _mk_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def compute_bollinger_atr_panel(
    df: pd.DataFrame,
    bb_window: int = BB_WINDOW,
    bb_std: float = BB_STD_MULT,
    atr_window: int = ATR_WINDOW,
) -> pd.DataFrame:
    """Panel untuk trend-follow analysis.

    Args:
      df: OHLCV DataFrame dengan kolom `ts, open, high, low, close, volume`.
      bb_window: window untuk SMA + stdev Bollinger.
      bb_std: multiplier stdev untuk upper/lower band.
      atr_window: window untuk ATR.

    Return df + columns: sma, bb_std, upper, lower, atr.
    Rows pertama (sebelum window ready) akan punya NaN — caller filter sendiri.
    """
    if df.empty:
        return df.copy()

    out = df.copy().reset_index(drop=True)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)

    sma = close.rolling(bb_window).mean()
    sd = close.rolling(bb_window).std()
    out["sma"] = sma
    out["bb_std"] = sd
    out["upper"] = sma + bb_std * sd
    out["lower"] = sma - bb_std * sd

    # True Range = max(H-L, |H - prev_C|, |L - prev_C|)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_window).mean()

    return out


def detect_breakout_signals(
    panel: pd.DataFrame,
    symbol: str,
    min_atr_pct: float = MIN_ATR_PCT,
    debounce_bars: int = DEFAULT_DEBOUNCE,
) -> list[dict]:
    """Emit TREND_BREAKOUT events on fresh Bollinger crossings.

    Side semantics:
      - long-breakout:  close > upper AND prev_close ≤ prev_upper
      - short-breakdown: close < lower AND prev_close ≥ prev_lower

    Skip emit kalau:
      - ATR/close < min_atr_pct (volatility terlalu rendah → noise breakout)
      - Last emit < debounce_bars bar lalu (anti-spam consecutive crossings)
    """
    events: list[dict] = []
    if panel.empty or len(panel) < BB_WINDOW + 2:
        return events

    last_emit_idx = -10_000
    for i in range(1, len(panel)):
        row = panel.iloc[i]
        prev = panel.iloc[i - 1]

        upper = row["upper"]
        lower = row["lower"]
        atr = row["atr"]
        close_now = float(row["close"])
        prev_upper = prev["upper"]
        prev_lower = prev["lower"]
        prev_close = float(prev["close"])

        if (
            pd.isna(upper) or pd.isna(lower) or pd.isna(atr)
            or pd.isna(prev_upper) or pd.isna(prev_lower)
        ):
            continue

        # Volatility filter
        if close_now <= 0 or float(atr) / close_now < min_atr_pct:
            continue

        # Debounce
        if i - last_emit_idx < debounce_bars:
            continue

        side: str | None = None
        if close_now > float(upper) and prev_close <= float(prev_upper):
            side = "long-breakout"
        elif close_now < float(lower) and prev_close >= float(prev_lower):
            side = "short-breakdown"

        if side is None:
            continue

        ts = row["ts"]
        ts_dt = (
            ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        )
        if getattr(ts_dt, "tzinfo", None) is not None:
            ts_dt = ts_dt.replace(tzinfo=None)
        bucket = pd.Timestamp(ts).strftime("%Y%m%d%H")
        atr_pct = float(atr) / close_now * 100.0
        payload = (
            f"sym={symbol};side={side};close={close_now:.4f};"
            f"upper={float(upper):.4f};lower={float(lower):.4f};"
            f"sma={float(row['sma']):.4f};atr={float(atr):.4f};"
            f"atr_pct={atr_pct:.3f}"
        )
        severity = "warn" if atr_pct < 2.0 else "critical"
        events.append({
            "id": _mk_id("TREND_BREAKOUT", symbol, bucket, side),
            "ts": ts_dt,
            "event_type": "TREND_BREAKOUT",
            "symbol": symbol,
            "severity": severity,
            "payload": payload,
        })
        last_emit_idx = i

    return events


def parse_breakout_payload(payload: str) -> dict:
    """Parse TREND_BREAKOUT payload back to dict. Helper untuk consumer."""
    out: dict = {}
    for kv in payload.split(";"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ("close", "upper", "lower", "sma", "atr", "atr_pct"):
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out
