"""Step 19 — Ensemble voting strategy.

Filosofi: 4 thesis class sudah rejected individually (FUNDING_FLIP, COINTEGRATION,
TREND_FOLLOW; REGIME_CHANGE Step 13 inversion-neutral). Tapi mereka **orthogonal**
(funding/regime/momentum/spread). Diversifikasi voting bisa menemukan edge yang
individual signal tidak punya.

Per-symbol voting model (4 signal sources):
  - REGIME_CHANGE  → LONG (trending_up) / SHORT (trending_down) / 0 (chaos|sideways)
  - FUNDING_EXTREME → SHORT (long-pays = bearish exhaustion) / LONG (short-pays)
  - FUNDING_FLIP   → LONG (pos→neg flip) / SHORT (neg→pos flip)
  - TREND_BREAKOUT → LONG (long-breakout) / SHORT (short-breakdown)

Aggregate: per-symbol per-bar, sum votes from signals di lookback window (default 24h).
Emit ENSEMBLE_LONG/SHORT saat |vote_sum| ≥ min_agreement (default 2 dari 4).

NOTE: Z_SPIKE + COINTEGRATION_SPREAD pair-relational (2 simbol leg), tidak fit
ke per-symbol voting. Future Step 19+ bisa ensemble portfolio-level kalau ini
berhasil.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

import pandas as pd


SIGNAL_TYPES_PER_SYMBOL = (
    "REGIME_CHANGE",
    "FUNDING_EXTREME",
    "FUNDING_FLIP",
    "TREND_BREAKOUT",
)


def _mk_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _vote_from_signal(event_type: str, payload: str) -> int:
    """Map satu signal event → vote -1 (short) / 0 (neutral) / +1 (long).

    Payload formats per signal_log + trend_follow:
      REGIME_CHANGE:    `from=X;to=trending_up|trending_down|chaos|sideways;t=...`
      FUNDING_EXTREME:  `rate=±0.04%;apr=...;side=long-pays|short-pays`
      FUNDING_FLIP:     `prev=±0.01%;curr=∓0.02%`
      TREND_BREAKOUT:   `sym=...;side=long-breakout|short-breakdown;...`
    """
    if event_type == "REGIME_CHANGE":
        if "to=trending_up" in payload:
            return +1
        if "to=trending_down" in payload:
            return -1
        return 0
    if event_type == "FUNDING_EXTREME":
        # long-pays = funding positif, longs paying shorts → bearish exhaustion → SHORT
        # short-pays = funding negatif, shorts paying longs → LONG opportunity
        if "side=long-pays" in payload:
            return -1
        if "side=short-pays" in payload:
            return +1
        return 0
    if event_type == "FUNDING_FLIP":
        # Flip semantics: parse prev + curr signs
        m_prev = re.search(r"prev=(-?[\d.]+)%", payload)
        m_curr = re.search(r"curr=(-?[\d.]+)%", payload)
        if not m_prev or not m_curr:
            return 0
        try:
            prev = float(m_prev.group(1))
            curr = float(m_curr.group(1))
        except ValueError:
            return 0
        # pos → neg flip: bearish carry now becomes bullish carry → LONG bias
        if prev > 0 and curr < 0:
            return +1
        # neg → pos flip: bullish carry → bearish → SHORT
        if prev < 0 and curr > 0:
            return -1
        return 0
    if event_type == "TREND_BREAKOUT":
        if "side=long-breakout" in payload:
            return +1
        if "side=short-breakdown" in payload:
            return -1
        return 0
    return 0


def aggregate_votes(
    signal_events: pd.DataFrame,
    symbols: list[str],
    lookback_hours: int = 24,
    bar_resolution: str = "1h",
) -> pd.DataFrame:
    """Aggregate per-symbol per-bar vote_sum dari signals dalam lookback window.

    Args:
      signal_events: DataFrame dengan kolom `ts, event_type, symbol, payload`
        (output dari `Storage.get_signals()`).
      symbols: list symbols untuk filter (single-leg only).
      lookback_hours: window untuk count votes.
      bar_resolution: pandas freq string ("1h", "4H", etc.).

    Return DataFrame [ts, symbol, vote_sum, n_signals, signal_types]. Bar-aligned
    timestamps (sesuai resolution).
    """
    if signal_events.empty:
        return pd.DataFrame(columns=["ts", "symbol", "vote_sum", "n_signals", "signal_types"])

    df = signal_events.copy()
    df["ts"] = pd.to_datetime(df["ts"])
    df = df[df["event_type"].isin(SIGNAL_TYPES_PER_SYMBOL)]
    # Filter only events tied to our symbols (drop pair-format symbols dengan `|`)
    df = df[df["symbol"].isin(symbols)]
    if df.empty:
        return pd.DataFrame(columns=["ts", "symbol", "vote_sum", "n_signals", "signal_types"])

    df["vote"] = df.apply(
        lambda row: _vote_from_signal(row["event_type"], str(row["payload"])),
        axis=1,
    )
    df = df[df["vote"] != 0]
    if df.empty:
        return pd.DataFrame(columns=["ts", "symbol", "vote_sum", "n_signals", "signal_types"])

    # Build bar grid: for each symbol, generate bars from earliest signal to latest
    ts_min = df["ts"].min().floor(bar_resolution)
    ts_max = df["ts"].max().ceil(bar_resolution)
    bars = pd.date_range(ts_min, ts_max, freq=bar_resolution)

    rows: list[dict] = []
    lookback = pd.Timedelta(hours=lookback_hours)
    for sym in symbols:
        df_sym = df[df["symbol"] == sym].sort_values("ts")
        if df_sym.empty:
            continue
        ts_arr = df_sym["ts"].values
        votes = df_sym["vote"].values
        types = df_sym["event_type"].values

        for bar_ts in bars:
            window_start = bar_ts - lookback
            mask = (ts_arr > window_start) & (ts_arr <= bar_ts)
            if not mask.any():
                continue
            n = int(mask.sum())
            v = int(votes[mask].sum())
            unique_types = sorted(set(types[mask].tolist()))
            rows.append({
                "ts": bar_ts,
                "symbol": sym,
                "vote_sum": v,
                "n_signals": n,
                "signal_types": ",".join(unique_types),
            })
    return pd.DataFrame(rows)


def emit_ensemble_signals(
    votes_df: pd.DataFrame,
    min_agreement: int = 2,
    min_distinct_types: int = 2,
    debounce_hours: int = 4,
) -> list[dict]:
    """Emit ENSEMBLE_ENTRY events saat |vote_sum| ≥ min_agreement.

    Filter tambahan:
      - min_distinct_types: butuh ≥N distinct signal types berkontribusi (anti
        single-source dominance, e.g., multiple REGIME_CHANGE jangan dihitung
        sebagai "ensemble").
      - debounce_hours: gap minimum antara consecutive emit per symbol.
    """
    events: list[dict] = []
    if votes_df.empty:
        return events

    last_emit_per_sym: dict[str, pd.Timestamp] = {}
    debounce = pd.Timedelta(hours=debounce_hours)

    for row in votes_df.sort_values("ts").itertuples(index=False):
        v = int(row.vote_sum)
        if abs(v) < min_agreement:
            continue
        types = str(row.signal_types).split(",") if row.signal_types else []
        if len(set(types)) < min_distinct_types:
            continue
        sym = str(row.symbol)
        ts = pd.Timestamp(row.ts)
        last = last_emit_per_sym.get(sym)
        if last is not None and (ts - last) < debounce:
            continue
        side = "long" if v > 0 else "short"
        bucket = ts.strftime("%Y%m%d%H")
        payload = (
            f"sym={sym};side={side};vote_sum={v};n_signals={int(row.n_signals)};"
            f"types={row.signal_types};agreement={min_agreement}"
        )
        ts_naive = ts.to_pydatetime().replace(tzinfo=None)
        events.append({
            "id": _mk_id("ENSEMBLE_ENTRY", sym, bucket, side),
            "ts": ts_naive,
            "event_type": "ENSEMBLE_ENTRY",
            "symbol": sym,
            "severity": "warn" if abs(v) < 3 else "critical",
            "payload": payload,
        })
        last_emit_per_sym[sym] = ts
    return events


def parse_ensemble_payload(payload: str) -> dict:
    """Parse ENSEMBLE_ENTRY payload back to dict."""
    out: dict = {}
    for kv in payload.split(";"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ("vote_sum", "n_signals", "agreement"):
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out
