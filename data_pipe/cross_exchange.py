"""Step 20 — Cross-exchange funding rate differential.

Hipotesis: funding rate per perpetual swap mencerminkan positioning imbalance
di exchange tersebut. Differential Bybit_funding - Binance_funding mengindikasikan
exchange mana yang punya crowding lebih besar di salah satu side.

Predictive thesis to test (2 arah):
  A. Mean-revert: positive diff (Bybit longs more crowded) → SHORT bias (longs about to
     get squeezed) → expect price decrease.
  B. Momentum: positive diff indicates strong demand on Bybit → LONG bias (trend continues).

Detector emits CROSS_FUNDING_DIVERGE events saat |diff| ≥ threshold (default p90
absolute differential, atau fixed). Direction tag dipisah dari thesis-direction —
backtest harness pilih mean-revert atau momentum.

Data dependency: butuh BOTH bybit funding (default daemon) DAN binance funding
(via `scripts/backfill_binance_funding.py`). Storage funding_rate table mendukung
multi-exchange via `exchange` column.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd


def _mk_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def compute_funding_diff_panel(
    bybit_funding: pd.DataFrame,
    binance_funding: pd.DataFrame,
    symbol: str,
    tolerance_minutes: int = 30,
) -> pd.DataFrame:
    """Merge bybit + binance funding by aligned timestamp.

    Funding cycle Bybit = 8h boundaries (00:00, 08:00, 16:00 UTC).
    Binance funding cycle = 8h juga tapi mungkin offset beberapa menit (mark-time
    differences). Tolerance untuk match adjacent timestamps.

    Args:
      bybit_funding: DataFrame [ts, funding] dari storage.get_funding("bybit", sym).
      binance_funding: similarly untuk binance.

    Return DataFrame [ts, symbol, bybit_funding, binance_funding, diff].
    Empty kalau salah satu side kosong.
    """
    if bybit_funding.empty or binance_funding.empty:
        return pd.DataFrame(columns=[
            "ts", "symbol", "bybit_funding", "binance_funding", "diff",
        ])

    by = bybit_funding.copy()
    bi = binance_funding.copy()
    by["ts"] = pd.to_datetime(by["ts"])
    bi["ts"] = pd.to_datetime(bi["ts"])
    by = by[["ts", "funding"]].rename(columns={"funding": "bybit_funding"})
    bi = bi[["ts", "funding"]].rename(columns={"funding": "binance_funding"})

    # Round to 1h floor for joining — both exchanges have 8h funding so this
    # buckets them safely
    by["join_ts"] = by["ts"].dt.floor("h")
    bi["join_ts"] = bi["ts"].dt.floor("h")
    merged = pd.merge(by, bi, on="join_ts", suffixes=("_by", "_bi"))
    if merged.empty:
        return pd.DataFrame(columns=[
            "ts", "symbol", "bybit_funding", "binance_funding", "diff",
        ])

    out = pd.DataFrame({
        "ts": merged["ts_by"],
        "symbol": symbol,
        "bybit_funding": merged["bybit_funding"].astype(float),
        "binance_funding": merged["binance_funding"].astype(float),
        "diff": (merged["bybit_funding"] - merged["binance_funding"]).astype(float),
    })
    out = out.sort_values("ts").reset_index(drop=True)
    return out


def dynamic_diff_threshold(
    panel: pd.DataFrame,
    percentile: float = 90.0,
    min_threshold: float = 0.00005,   # 0.005% / 8h
    max_threshold: float = 0.001,      # 0.1% / 8h
    fallback: float = 0.0002,
    min_samples: int = 50,
) -> float:
    """Percentile-based threshold dari |diff| distribution. Bounded."""
    if panel.empty or "diff" not in panel.columns:
        return fallback
    abs_diff = panel["diff"].abs().dropna()
    abs_diff = abs_diff[abs_diff > 0]
    if len(abs_diff) < min_samples:
        return fallback
    p = float(np.percentile(abs_diff, percentile))
    return max(min_threshold, min(max_threshold, p))


def detect_funding_diff_signals(
    panel: pd.DataFrame,
    symbol: str,
    threshold: float = 0.0002,    # 0.02% / 8h
    debounce_hours: int = 24,
) -> list[dict]:
    """Emit CROSS_FUNDING_DIVERGE events saat |diff| ≥ threshold.

    Side tag (NOT direction — thesis-agnostic):
      - bybit-crowded: bybit_funding > binance_funding (bybit longs more crowded)
      - binance-crowded: bybit_funding < binance_funding

    Direction interpretation (LONG/SHORT) dipilih oleh backtest harness via
    `--thesis-direction {mean-revert|momentum}`.
    """
    events: list[dict] = []
    if panel.empty:
        return events

    last_emit: Optional[pd.Timestamp] = None
    debounce = pd.Timedelta(hours=debounce_hours)

    for row in panel.itertuples(index=False):
        diff = float(row.diff)
        if abs(diff) < threshold:
            continue
        ts = pd.Timestamp(row.ts)
        if last_emit is not None and (ts - last_emit) < debounce:
            continue
        side = "bybit-crowded" if diff > 0 else "binance-crowded"
        bucket = ts.strftime("%Y%m%d%H")
        payload = (
            f"sym={symbol};side={side};diff={diff*100:.4f}%;"
            f"bybit_f={float(row.bybit_funding)*100:.4f}%;"
            f"binance_f={float(row.binance_funding)*100:.4f}%;"
            f"threshold={threshold*100:.4f}%"
        )
        ts_naive = ts.to_pydatetime().replace(tzinfo=None)
        events.append({
            "id": _mk_id("CROSS_FUNDING_DIVERGE", symbol, bucket, side),
            "ts": ts_naive,
            "event_type": "CROSS_FUNDING_DIVERGE",
            "symbol": symbol,
            "severity": "warn",
            "payload": payload,
        })
        last_emit = ts
    return events


def parse_diff_payload(payload: str) -> dict:
    """Parse CROSS_FUNDING_DIVERGE payload."""
    out: dict = {}
    for kv in payload.split(";"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ("diff", "bybit_f", "binance_f", "threshold"):
            v = v.rstrip("%")
            try:
                out[k] = float(v) / 100.0
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out
