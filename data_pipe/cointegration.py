"""Cointegration detector — Step 17.B production module.

Strategi: rolling Engle-Granger refit setiap `refit_freq_bars` bar, hitung
z-score spread di tiap bar (selama pair masih cointegrated), emit
COINTEGRATION_SPREAD events pada fresh entry crossings.

Differentiation dari existing `dashboard/signal_log.detect_cointegration_zspikes`:
- Rolling lookback (default 30d=720 bars 1h) bukan full history.
- ADF gate dicek tiap refit, bukan sekali untuk seluruh series — sehingga
  pair yang cointegrated periode-wise tetap emit untuk periode itu.
- Output dictionary kompatibel dengan storage.upsert_signals (id, ts,
  event_type, symbol, severity, payload).
- Hedge ratio (β) di-emit di payload → executor (B3) pakai untuk sizing leg B.

Step 17.B2 nanti tambah dynamic z-threshold (percentile-based) tanpa
mutate signature ini (entry_z param tetap, caller compute percentile).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from dashboard.cointegration import engle_granger


def _mk_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def rolling_cointegration_panel(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    pair_names: tuple[str, str],
    lookback_bars: int = 720,
    refit_freq_bars: int = 24,
    z_window: int = 50,
) -> pd.DataFrame:
    """Rolling cointegration panel.

    Untuk tiap bar mulai dari index `lookback_bars-1`:
      - Refit Engle-Granger di lookback window setiap `refit_freq_bars` bar.
      - Compute spread = log(y) - (alpha + beta × log(x)) pakai cached refit.
      - Compute z-score spread dari last `z_window` bar.

    Return DataFrame index=numeric, columns: ts, hedge_ratio (β), alpha,
    adf_pvalue, half_life, is_cointegrated, z_score, spread.
    Empty kalau data terlalu sedikit.

    Args:
      df_a, df_b: OHLCV DataFrame dengan kolom `ts` + `close`. Akan di-merge
        on ts (inner join).
      pair_names: (leg_a, leg_b) untuk diteruskan ke engle_granger.
      lookback_bars: window untuk Engle-Granger refit (default 720 = 30d × 24h).
      refit_freq_bars: refit cadence (default 24 = daily).
      z_window: rolling window untuk z-score (default 50, match existing
        Z_ROLL_WINDOW di signal_log).
    """
    merged = pd.merge(
        df_a[["ts", "close"]].rename(columns={"close": "close_a"}),
        df_b[["ts", "close"]].rename(columns={"close": "close_b"}),
        on="ts",
    )
    merged = merged.sort_values("ts").reset_index(drop=True)
    n = len(merged)
    if n < max(lookback_bars + 1, z_window + 1):
        return pd.DataFrame(columns=[
            "ts", "hedge_ratio", "alpha", "adf_pvalue", "half_life",
            "is_cointegrated", "z_score", "spread",
        ])

    log_y = np.log(merged["close_a"].astype(float))
    log_x = np.log(merged["close_b"].astype(float))

    rows: list[dict] = []
    last_refit_idx = -refit_freq_bars
    cached_alpha: float | None = None
    cached_beta: float | None = None
    cached_pval: float = 1.0
    cached_hl: float = float("inf")
    cached_coint: bool = False

    for i in range(lookback_bars - 1, n):
        if i - last_refit_idx >= refit_freq_bars:
            y_win = log_y.iloc[i - lookback_bars + 1 : i + 1].reset_index(drop=True)
            x_win = log_x.iloc[i - lookback_bars + 1 : i + 1].reset_index(drop=True)
            res = engle_granger(y_win, x_win, pair_names)
            cached_beta = float(res.hedge_ratio)
            # engle_granger tidak return alpha; recompute pakai OLS-mean
            cached_alpha = float(y_win.mean() - cached_beta * x_win.mean())
            cached_pval = float(res.adf_pvalue)
            cached_hl = float(res.half_life_bars)
            cached_coint = bool(res.is_cointegrated)
            last_refit_idx = i

        # Spread window berdasarkan cached alpha+beta
        y_win = log_y.iloc[i - lookback_bars + 1 : i + 1]
        x_win = log_x.iloc[i - lookback_bars + 1 : i + 1]
        spread = y_win - (cached_alpha + cached_beta * x_win)
        spread_recent = spread.iloc[-z_window:]
        if spread_recent.std() > 0:
            z = float((spread.iloc[-1] - spread_recent.mean()) / spread_recent.std())
        else:
            z = 0.0

        rows.append({
            "ts": merged["ts"].iloc[i],
            "hedge_ratio": cached_beta,
            "alpha": cached_alpha,
            "adf_pvalue": cached_pval,
            "half_life": cached_hl,
            "is_cointegrated": cached_coint,
            "z_score": z,
            "spread": float(spread.iloc[-1]),
        })

    return pd.DataFrame(rows)


def detect_spread_signals(
    panel: pd.DataFrame,
    pair_names: tuple[str, str],
    entry_z: float = 2.0,
    max_z_kill: float = 4.0,
    require_cointegrated: bool = True,
    debounce_bars: int = 24,
) -> list[dict]:
    """Emit COINTEGRATION_SPREAD events pada fresh entry crossings.

    Side semantics (mean-reversion):
      - long-spread:  z ≤ -entry_z (spread terlalu rendah, expect revert UP)
                      → LONG leg A, SHORT leg B × hedge_ratio
      - short-spread: z ≥ +entry_z (spread terlalu tinggi, expect revert DOWN)
                      → SHORT leg A, LONG leg B × hedge_ratio

    Args:
      entry_z: |z| threshold untuk emit entry signal.
      max_z_kill: skip emit kalau |z| ≥ ini (broken spread, not mean-reverting).
      require_cointegrated: skip emit kalau ADF gate dari panel = False.
      debounce_bars: minimal bar gap antara consecutive emit untuk pair sama.
    """
    events: list[dict] = []
    if panel.empty:
        return events

    last_emit_ts: Optional[pd.Timestamp] = None
    prev_z: float = float(panel["z_score"].iloc[0])
    # Skip first bar (no prior z to compare crossing)
    for row in panel.iloc[1:].itertuples(index=False):
        ts = pd.Timestamp(row.ts)
        if require_cointegrated and not row.is_cointegrated:
            prev_z = float(row.z_score)
            continue
        if last_emit_ts is not None:
            gap_h = (ts - last_emit_ts).total_seconds() / 3600.0
            if gap_h < debounce_bars:
                prev_z = float(row.z_score)
                continue

        z = float(row.z_score)
        side: str | None = None
        if z <= -entry_z and prev_z > -entry_z:
            side = "long-spread"
        elif z >= entry_z and prev_z < entry_z:
            side = "short-spread"

        if side is not None and abs(z) < max_z_kill:
            sev = "critical" if abs(z) >= 3.0 else "warn"
            symbol = f"{pair_names[0]}|{pair_names[1]}"
            payload = (
                f"pair={pair_names[0]}|{pair_names[1]};"
                f"z={z:+.2f};hedge_ratio={float(row.hedge_ratio):.4f};"
                f"adf_p={float(row.adf_pvalue):.3f};"
                f"hl={float(row.half_life):.1f};"
                f"side={side};entry_z={entry_z}"
            )
            ts_naive = ts.to_pydatetime().replace(tzinfo=None)
            bucket = ts.strftime("%Y%m%d%H")
            events.append({
                "id": _mk_id("COINTEGRATION_SPREAD", symbol, bucket, side),
                "ts": ts_naive,
                "event_type": "COINTEGRATION_SPREAD",
                "symbol": symbol,
                "severity": sev,
                "payload": payload,
            })
            last_emit_ts = ts

        prev_z = z

    return events


def dynamic_entry_threshold(
    panel: pd.DataFrame,
    percentile: float = 95.0,
    min_threshold: float = 1.5,
    max_threshold: float = 3.5,
    fallback: float = 2.0,
    min_samples: int = 50,
) -> float:
    """Step 17.B2 — volatility-adaptive entry threshold.

    Hitung percentile-based threshold dari distribusi |z| historis dalam panel.
    Default percentile=95 → top 5% events trigger entries (sparse but high-quality).

    Bounded ke [min_threshold, max_threshold] supaya tidak absurdly tight (false
    positive deluge) atau loose (no trades). Fallback dipakai kalau panel kosong
    atau sample < min_samples.

    Justifies threshold instead of hand-tuning: pair dengan volatility spread
    tinggi otomatis dapat threshold lebih tinggi.
    """
    if panel.empty or "z_score" not in panel.columns:
        return fallback
    abs_z = panel["z_score"].abs().dropna()
    abs_z = abs_z[abs_z > 0]
    if len(abs_z) < min_samples:
        return fallback
    p = float(np.percentile(abs_z, percentile))
    return max(min_threshold, min(max_threshold, p))


def parse_spread_payload(payload: str) -> dict:
    """Helper: parse COINTEGRATION_SPREAD payload kembali ke dict.

    Untuk consumer (executor B3, analytics, tests).
    """
    out: dict = {}
    for kv in payload.split(";"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        try:
            if k in ("z", "hedge_ratio", "adf_p", "hl", "entry_z"):
                out[k] = float(v)
            else:
                out[k] = v
        except ValueError:
            out[k] = v
    return out
