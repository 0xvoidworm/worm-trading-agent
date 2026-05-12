"""Deterministic regime classifier.

Bukan ML, bukan LLM. Hanya feature sederhana berbasis literatur quant:
  - Realized vol (ATR / price) → proxy untuk risk level
  - Trend strength (ADX-like via linear regression slope / stderr)
  - Range compression (Bollinger bandwidth relatif ke history)

Output regime label: {trending_up, trending_down, ranging, chaos}

Referensi: regime detection by simple feature threshold lebih robust
daripada HMM/Markov switching untuk sample kecil (Lopez de Prado, Ch.8).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


Regime = Literal["trending_up", "trending_down", "ranging", "chaos", "insufficient_data"]


@dataclass
class RegimeSnapshot:
    regime: Regime
    atr_pct: float          # ATR as % of close
    trend_slope_t: float    # t-stat dari linreg slope (trend significance)
    bb_width: float         # (upper - lower) / middle
    confidence: float       # [0..1] — consistency antar indikator


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    """True Range rata-rata. Standard Wilder formulation."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def _trend_tstat(series: pd.Series, n: int = 30) -> float:
    """t-stat dari slope linear regression over last n bars.

    |t| > 2 ≈ trend signifikan secara statistik. Ini lebih robust dari MA cross
    karena memperhitungkan noise.
    """
    y = series.tail(n).values.astype(float)
    if len(y) < n:
        return 0.0
    x = np.arange(len(y), dtype=float)
    # slope via least squares
    x_mean, y_mean = x.mean(), y.mean()
    x_dev = x - x_mean
    y_dev = y - y_mean
    ss_xx = (x_dev ** 2).sum()
    if ss_xx == 0:
        return 0.0
    slope = (x_dev * y_dev).sum() / ss_xx
    # residual stderr
    y_hat = y_mean + slope * x_dev
    resid = y - y_hat
    dof = len(y) - 2
    if dof <= 0:
        return 0.0
    se = np.sqrt((resid ** 2).sum() / dof / ss_xx)
    if se == 0:
        return 0.0
    return float(slope / se)


def _bb_width(series: pd.Series, n: int = 20, k: float = 2.0) -> float:
    tail = series.tail(n)
    if len(tail) < n:
        return 0.0
    ma = tail.mean()
    sd = tail.std()
    if ma == 0:
        return 0.0
    return float(2 * k * sd / ma)


def classify(df: pd.DataFrame) -> RegimeSnapshot:
    """Input: OHLCV DataFrame, minimal 30 bars. Output: regime snapshot."""
    if df is None or len(df) < 30:
        return RegimeSnapshot("insufficient_data", 0, 0, 0, 0)

    close = df["close"]
    atr_val = _atr(df, 14)
    atr_pct = atr_val / float(close.iloc[-1]) if close.iloc[-1] else 0.0
    t_stat = _trend_tstat(close, 30)
    bbw = _bb_width(close, 20)

    # Rule-based klasifikasi
    # - |t_stat| > 2: trend signifikan
    # - bb_width historical quantile proxy: bandingkan ke mean bbw dari history
    hist_bbw = close.rolling(20).std() * 4 / close.rolling(20).mean()
    hist_bbw_median = float(hist_bbw.tail(200).median()) if len(hist_bbw) > 30 else bbw

    is_compressed = bbw < hist_bbw_median * 0.8
    is_wide = bbw > hist_bbw_median * 1.5

    if t_stat > 2.0 and not is_wide:
        regime: Regime = "trending_up"
        conf = min(1.0, abs(t_stat) / 4.0)
    elif t_stat < -2.0 and not is_wide:
        regime = "trending_down"
        conf = min(1.0, abs(t_stat) / 4.0)
    elif is_wide and abs(t_stat) < 2.0:
        regime = "chaos"
        conf = min(1.0, bbw / (hist_bbw_median * 2.0))
    else:
        regime = "ranging"
        conf = 1.0 - min(1.0, abs(t_stat) / 3.0)

    return RegimeSnapshot(
        regime=regime,
        atr_pct=atr_pct,
        trend_slope_t=t_stat,
        bb_width=bbw,
        confidence=conf,
    )
