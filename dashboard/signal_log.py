"""Signal event detector — monitoring layer di atas data pipe.

Event types:
  - REGIME_CHANGE   : regime symbol beda dengan regime cycle sebelumnya
  - FUNDING_EXTREME : funding rate crossed threshold extreme (|rate| >= 0.000242, p99)
  - FUNDING_FLIP    : funding sign flipped (positif → negatif atau sebaliknya)
  - Z_SPIKE         : cointegration z-score melewati ±2 (fresh crossing)

Historical sweep: detector tidak hanya lihat snapshot terakhir. Tiap run
scan SEMUA bar historis (funding & OHLCV) yang belum ter-log, dan emit event
dengan ts = kejadian asli, bukan ts cycle sekarang. Dedup via id hash
(event_type|symbol|ts_bucket|payload_key) → rerun & overlap aman.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterable

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS

from data_pipe.storage import Storage
from dashboard.regime import classify, RegimeSnapshot
from dashboard.cointegration import engle_granger


# Threshold default — bisa diubah via config nanti
# Step 13C: lowered 0.001 → 0.000242 (p99 dari distribution, Step 10/12 analysis)
# untuk align forward-emit dengan post-12B replay supply (~88 events/365d).
FUNDING_EXTREME = 0.000242   # 0.0242% / 8h → ~26.5% APR (p99 historical)
FUNDING_FLIP_MIN_ABS = 0.0002  # hindari noise, hanya log kalau abs-nya lumayan
Z_SPIKE = 2.0
Z_ROLL_WINDOW = 50           # rolling window untuk z-score historis
REGIME_DEBOUNCE_BARS = 3     # Step 17.A2: emit REGIME_CHANGE hanya saat new regime stable ≥ N bar berturut-turut


def _mk_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _event(
    ts: datetime,
    event_type: str,
    symbol: str | None,
    severity: str,
    payload: str,
) -> dict:
    # id = hash dari (type, symbol, bucket-ts, payload-key)
    ts_naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
    ts_bucket = ts_naive.strftime("%Y%m%d%H%M")
    return {
        "id": _mk_id(event_type, symbol or "", ts_bucket, payload),
        "ts": ts_naive,
        "event_type": event_type,
        "symbol": symbol,
        "severity": severity,
        "payload": payload,
    }


def _last_event_ts(storage: Storage, event_type: str, symbol: str | None = None) -> pd.Timestamp | None:
    """Watermark: latest ts for a given event_type (& optional symbol)."""
    sql = "SELECT MAX(ts) FROM signal_events WHERE event_type=?"
    params: list = [event_type]
    if symbol is not None:
        sql += " AND symbol=?"
        params.append(symbol)
    row = storage._conn.execute(sql, params).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] is not None else None


def detect_regime_changes(
    storage: Storage,
    symbols: list[str],
    exchange: str,
    tf: str,
    debounce_bars: int = REGIME_DEBOUNCE_BARS,
) -> list[dict]:
    """Historical sweep REGIME_CHANGE.

    Step 17.A2 debounce: emit hanya saat new regime persisted ≥ debounce_bars
    consecutive bars (default 3). Mengurangi noise classifier yang Step 11 surface
    (REGIME 77→6,847 di 365d backfill = 89× inflation oleh transient flicker).
    Emit ts = bar mulai transisi (back-dated debounce_bars-1).
    debounce_bars=1 = behaviour lama (no debounce).
    """
    events = []
    for sym in symbols:
        df = storage.get_ohlcv(exchange, sym, tf, limit=500)
        if len(df) < 30:
            continue

        # Ambil state terakhir per symbol
        last = storage.get_signals(limit=1, event_type="REGIME_CHANGE", symbol=sym)
        last_regime: str | None = None
        last_ts: pd.Timestamp | None = None
        if not last.empty:
            last_ts = pd.Timestamp(last.iloc[0]["ts"])
            p = str(last.iloc[0]["payload"])
            for kv in p.split(";"):
                if kv.startswith("to="):
                    last_regime = kv.split("=", 1)[1]
                    break

        if last_regime is None:
            # First observation — emit satu event di bar terbaru
            snap = classify(df)
            if snap.regime == "insufficient_data":
                continue
            bar_ts = pd.Timestamp(df.iloc[-1]["ts"]).to_pydatetime()
            payload = f"from=?;to={snap.regime};t={snap.trend_slope_t:.2f};debounce={debounce_bars}"
            events.append(_event(bar_ts, "REGIME_CHANGE", sym, "info", payload))
            continue

        # Walk bar-per-bar untuk bar yang ts > last_ts dengan stability counter
        df_ts = pd.to_datetime(df["ts"])
        prev_regime = last_regime
        pending_regime: str | None = None
        pending_start_idx: int | None = None
        pending_stability: int = 0
        pending_t: float = 0.0
        # Classify butuh minimal 30 bar trailing, jadi mulai dari index 29
        for i in range(29, len(df)):
            if df_ts.iloc[i] <= last_ts:
                continue
            window = df.iloc[: i + 1]
            snap = classify(window)
            if snap.regime == "insufficient_data":
                continue
            if snap.regime == prev_regime:
                # Reverted ke prev — cancel pending (transient flicker)
                pending_regime = None
                pending_stability = 0
                pending_start_idx = None
                continue
            # Bar regime != prev_regime
            if snap.regime == pending_regime:
                pending_stability += 1
                pending_t = snap.trend_slope_t
            else:
                # Different from both prev and pending → reset pending fresh
                pending_regime = snap.regime
                pending_stability = 1
                pending_start_idx = i
                pending_t = snap.trend_slope_t

            if pending_stability >= debounce_bars:
                # Confirmed transition — emit at the bar where it started
                bar_ts = df_ts.iloc[pending_start_idx].to_pydatetime()
                severity = "warn" if pending_regime == "chaos" else "info"
                payload = (
                    f"from={prev_regime};to={pending_regime};"
                    f"t={pending_t:.2f};debounce={debounce_bars}"
                )
                events.append(_event(bar_ts, "REGIME_CHANGE", sym, severity, payload))
                prev_regime = pending_regime
                pending_regime = None
                pending_stability = 0
                pending_start_idx = None
    return events


def detect_funding_events(storage: Storage, symbols: list[str], exchange: str) -> list[dict]:
    """Historical sweep FUNDING_EXTREME & FUNDING_FLIP.

    Loop semua funding row per symbol. Untuk tiap row ke-i, bandingkan dengan
    row[i-1]. Event di-emit dengan ts = funding-row-ts, bukan now.
    """
    events = []
    for sym in symbols:
        df = storage.get_funding(exchange, sym, limit=500)
        if len(df) < 2:
            continue
        fts_series = pd.to_datetime(df["ts"])
        for i in range(1, len(df)):
            ts = fts_series.iloc[i].to_pydatetime()
            curr = float(df.iloc[i]["funding"])
            prev = float(df.iloc[i - 1]["funding"])
            bucket = fts_series.iloc[i].strftime("%Y%m%d%H")

            # FUNDING_EXTREME — emit tiap kali row crosses threshold
            if abs(curr) >= FUNDING_EXTREME:
                side = "long-pays" if curr > 0 else "short-pays"
                payload = f"rate={curr*100:.4f}%;apr={curr*3*365*100:+.1f}%;side={side}"
                ev = _event(ts, "FUNDING_EXTREME", sym, "critical", payload)
                ev["id"] = _mk_id("FUNDING_EXTREME", sym, bucket, f"{curr:.5f}")
                events.append(ev)

            # FUNDING_FLIP — sign change + absolute signifikan
            if (curr > 0) != (prev > 0) and abs(curr) >= FUNDING_FLIP_MIN_ABS:
                payload = f"prev={prev*100:.4f}%;curr={curr*100:.4f}%"
                ev = _event(ts, "FUNDING_FLIP", sym, "warn", payload)
                ev["id"] = _mk_id("FUNDING_FLIP", sym, bucket, f"{curr:.5f}")
                events.append(ev)
    return events


def detect_cointegration_zspikes(
    storage: Storage, pairs: list[list[str]], exchange: str, tf: str,
) -> list[dict]:
    """Historical sweep Z_SPIKE.

    Untuk tiap pair cointegrated: refit OLS (alpha, beta) pada seluruh history,
    hitung spread = y - (alpha + beta*x), rolling z-score (window=Z_ROLL_WINDOW).
    Emit event hanya pada FRESH CROSSING: |z[i]| >= 2 AND |z[i-1]| < 2, supaya
    tidak spam tiap bar selama z tetap di luar band.
    """
    events = []
    for pair in pairs:
        a, b = pair
        df_a = storage.get_ohlcv(exchange, a, tf, limit=500)
        df_b = storage.get_ohlcv(exchange, b, tf, limit=500)
        if df_a.empty or df_b.empty:
            continue
        sa = df_a.set_index("ts")["close"]
        sb = df_b.set_index("ts")["close"]
        y = np.log(sa)
        x = np.log(sb)
        try:
            res = engle_granger(y, x, pair_names=(a, b))
        except Exception:
            continue
        if not res.is_cointegrated:
            continue

        # Refit untuk ambil alpha (engle_granger tidak return alpha)
        df = pd.concat([y, x], axis=1).dropna()
        df.columns = ["y", "x"]
        if len(df) < Z_ROLL_WINDOW + 2:
            continue
        X = np.column_stack([np.ones(len(df)), df["x"].values])
        m = OLS(df["y"].values, X).fit()
        alpha, beta = m.params
        spread = df["y"] - (alpha + beta * df["x"])

        # Rolling z-score
        mean = spread.rolling(Z_ROLL_WINDOW).mean()
        std = spread.rolling(Z_ROLL_WINDOW).std()
        z = (spread - mean) / std
        z = z.dropna()
        if len(z) < 2:
            continue

        prev_abs = abs(z.iloc[0])
        ts_index = z.index
        for i in range(1, len(z)):
            zval = float(z.iloc[i])
            abs_now = abs(zval)
            # fresh crossing: naik dari <2 ke >=2
            if abs_now >= Z_SPIKE and prev_abs < Z_SPIKE:
                side = "short-spread" if zval > 0 else "long-spread"
                bar_ts = pd.Timestamp(ts_index[i]).to_pydatetime()
                payload = (
                    f"pair={a}|{b};z={zval:+.2f};adf_p={res.adf_pvalue:.3f};"
                    f"hl={res.half_life_bars:.1f};side={side}"
                )
                sev = "critical" if abs_now >= 3 else "warn"
                bucket = pd.Timestamp(ts_index[i]).strftime("%Y%m%d%H")
                ev = _event(bar_ts, "Z_SPIKE", f"{a}|{b}", sev, payload)
                ev["id"] = _mk_id("Z_SPIKE", f"{a}|{b}", bucket, side)
                events.append(ev)
            prev_abs = abs_now
    return events


def run_detectors(storage: Storage, cfg: dict) -> dict:
    """Run semua detector, upsert events. Return stats."""
    symbols = cfg["tier1"] + cfg["tier2"]
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    pairs = cfg.get("cointegration_candidates", [])
    regime_debounce = int(cfg.get("regime_debounce_bars", REGIME_DEBOUNCE_BARS))

    all_events = []
    all_events.extend(detect_regime_changes(storage, symbols, exchange, tf, debounce_bars=regime_debounce))
    all_events.extend(detect_funding_events(storage, symbols, exchange))
    all_events.extend(detect_cointegration_zspikes(storage, pairs, exchange, tf))

    n = storage.upsert_signals(all_events)
    by_type: dict[str, int] = {}
    for ev in all_events:
        by_type[ev["event_type"]] = by_type.get(ev["event_type"], 0) + 1
    return {"total": n, "by_type": by_type}
