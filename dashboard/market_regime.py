"""Market regime overlay — meta-layer untuk dispatch signal per kondisi pasar.

Dua dimensi:
  1. TREND (dari BTC 30d): btc_up / btc_down / sideways
     - Pakai t-stat regresi log-price terhadap bar index
     - Threshold ±2.0 (statistical significance)
  2. VOL (dari BTC 30d realized vol): high_vol / low_vol
     - std(log returns) di window 30d
     - Threshold = median across full sample (calibrated, no per-bar lookahead
       karena ini meta-feature bukan signal)

Kombinasi 3×2 = 6 regime:
  low_vol_up       → bull steady (trend-follow favorable)
  low_vol_sideways → quiet range
  low_vol_down     → controlled decline
  high_vol_up      → FOMO rally
  high_vol_sideways → choppy range (mean-reversion favorable)
  high_vol_down    → panic/capitulation

Dispatch logic di caller:
  - REGIME_UP cuma aktif saat low_vol_up
  - Z_SPIKE cuma aktif saat high_vol_sideways
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


TREND_WINDOW = 720       # 30 hari × 24 jam
VOL_WINDOW = 720
TREND_TSTAT_UP = 2.0
TREND_TSTAT_DOWN = -2.0
# Vol threshold di-set relatif — median dari seluruh window BTC 90d


@dataclass
class RegimeSnapshot:
    ts: pd.Timestamp
    trend_tstat: float
    trend: str              # "btc_up" / "btc_down" / "sideways"
    vol_hourly: float       # std of log returns (hourly)
    vol: str                # "high_vol" / "low_vol"
    label: str              # combined, e.g. "low_vol_up"


def _trend_tstat(log_prices: np.ndarray) -> float:
    """t-statistic of slope — lebih robust dari raw slope untuk beda durasi."""
    n = len(log_prices)
    if n < 10:
        return 0.0
    x = np.arange(n, dtype=float)
    x_mean = x.mean()
    y_mean = log_prices.mean()
    sxx = ((x - x_mean) ** 2).sum()
    sxy = ((x - x_mean) * (log_prices - y_mean)).sum()
    if sxx == 0:
        return 0.0
    slope = sxy / sxx
    # residuals
    fitted = y_mean + slope * (x - x_mean)
    resid = log_prices - fitted
    dof = n - 2
    if dof <= 0:
        return 0.0
    se2 = (resid ** 2).sum() / dof
    slope_se = np.sqrt(se2 / sxx)
    if slope_se == 0:
        return 0.0
    return float(slope / slope_se)


def _realized_vol(log_returns: np.ndarray) -> float:
    """Std hourly log returns."""
    if len(log_returns) < 10:
        return 0.0
    return float(log_returns.std(ddof=1))


def build_regime_timeline(btc_ohlcv: pd.DataFrame,
                           trend_win: int = TREND_WINDOW,
                           vol_win: int = VOL_WINDOW) -> pd.DataFrame:
    """Return DataFrame kolom [ts, trend_tstat, trend, vol_hourly, vol, label]
    untuk SETIAP bar yang memiliki cukup history (>= max(trend_win, vol_win))."""
    df = btc_ohlcv.sort_values("ts").reset_index(drop=True)
    log_close = np.log(df["close"].values)
    log_ret = np.diff(log_close, prepend=log_close[0])

    tstats = np.full(len(df), np.nan)
    vols = np.full(len(df), np.nan)
    win = max(trend_win, vol_win)
    for i in range(win - 1, len(df)):
        t_slice = log_close[i - trend_win + 1 : i + 1]
        v_slice = log_ret[i - vol_win + 1 : i + 1]
        tstats[i] = _trend_tstat(t_slice)
        vols[i] = _realized_vol(v_slice)

    # vol threshold = median of computed vols (skip NaN)
    valid_vols = vols[~np.isnan(vols)]
    vol_median = float(np.median(valid_vols)) if len(valid_vols) else 0.0

    labels = []
    trends = []
    vol_cats = []
    for t, v in zip(tstats, vols):
        if np.isnan(t) or np.isnan(v):
            labels.append(None); trends.append(None); vol_cats.append(None)
            continue
        trend = ("btc_up" if t >= TREND_TSTAT_UP else
                 "btc_down" if t <= TREND_TSTAT_DOWN else "sideways")
        vol_cat = "high_vol" if v >= vol_median else "low_vol"
        trends.append(trend); vol_cats.append(vol_cat)
        # combined label: low_vol_up / high_vol_sideways / etc
        if trend == "btc_up":
            labels.append(f"{vol_cat}_up")
        elif trend == "btc_down":
            labels.append(f"{vol_cat}_down")
        else:
            labels.append(f"{vol_cat}_sideways")

    out = pd.DataFrame({
        "ts": df["ts"].values,
        "trend_tstat": tstats,
        "trend": trends,
        "vol_hourly": vols,
        "vol": vol_cats,
        "label": labels,
    })
    return out


def regime_at(timeline: pd.DataFrame, ts: pd.Timestamp) -> str | None:
    """Lookup regime label pada/sebelum ts (searchsorted, O(log n))."""
    arr = timeline["ts"].values.astype("datetime64[ns]")
    target = np.datetime64(pd.Timestamp(ts).to_datetime64())
    idx = int(np.searchsorted(arr, target, side="right")) - 1
    if idx < 0:
        return None
    return timeline.iloc[idx]["label"]


def regime_summary(timeline: pd.DataFrame) -> dict:
    """Count distribusi regime di timeline. Untuk visibility saja."""
    counts = timeline["label"].value_counts(dropna=True).to_dict()
    total = sum(counts.values())
    return {"counts": counts, "pct": {k: 100 * v / total for k, v in counts.items()},
            "total": total}
