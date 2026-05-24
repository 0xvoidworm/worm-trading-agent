"""Spot pair relative-value scanner — adapt cointegration concept for long-only.

Spot tidak bisa short, jadi pure pair-trading (long A + short B) tidak feasible.
Tapi relative-value insight tetap useful: kalau ratio A/B sangat low vs historical
mean, A relatif cheap vs B → buy A signal (mean reversion expected).

Algorithm:
  ratio[t] = price_A[t] / price_B[t]
  z[t] = (ratio[t] - rolling_mean) / rolling_std
  z < -threshold → A is cheap → BUY A
  z > +threshold → B is cheap (relatively) → BUY B (or AVOID A)

Threshold default ±2.0 (2 std dev).

Default same-sector pairs dari sectors.py universe yang tersedia di DB OHLCV.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .event_scanner import Candidate
from .sectors import get_sector


# Default lookback for rolling z-score
DEFAULT_LOOKBACK_HOURS = 24 * 30  # 30 days of 1h bars = 720 bars
DEFAULT_Z_THRESHOLD = 2.0

# Pairs to scan (must both exist in DB OHLCV)
DEFAULT_PAIRS: list[tuple[str, str]] = [
    ("ETH/USDT", "BTC/USDT"),   # L1 vs Store of Value
    ("SOL/USDT", "ETH/USDT"),   # L1 vs L1
    ("AVAX/USDT", "SOL/USDT"),  # L1 vs L1
    ("ARB/USDT", "OP/USDT"),    # L2 vs L2 (classic same-sector)
    ("BNB/USDT", "BTC/USDT"),   # CEX vs Store of Value
    ("XRP/USDT", "LTC/USDT"),   # Payment vs Payment
]

# Per-pair backtest verdict (from scripts/backtest_spot_pairs.py 2026-05-24,
# z=2.0 entry, z=0 exit, hold 14d, SL -10%, fee 0.1%/side, 3y 1h OHLCV).
# Use to filter signals: only act on VALIDATED pairs in live trading.
PAIR_BACKTEST_VERDICT: dict[tuple[str, str], str] = {
    ("ETH/USDT", "BTC/USDT"):  "INCONSISTENT",       # 44% wr, -8% compound
    ("SOL/USDT", "ETH/USDT"):  "INCONSISTENT",       # 48% wr, -4% compound
    ("AVAX/USDT", "SOL/USDT"): "MARGINAL_POSITIVE",  # 52% wr, +311% compound (robust @z=2.5 hold=7d: +105%)
    ("ARB/USDT", "OP/USDT"):   "STABLE_NEGATIVE",    # 33% wr, -77% compound — AVOID
    ("BNB/USDT", "BTC/USDT"):  "STABLE_POSITIVE",    # 56% wr, +20% compound
    ("XRP/USDT", "LTC/USDT"):  "NO_SIGNALS",         # insufficient bars
}

VALIDATED_VERDICTS = {"STABLE_POSITIVE", "MARGINAL_POSITIVE"}


def is_pair_validated(pair_a: str, pair_b: str) -> bool:
    """Return True if backtest verdict suggests historical edge."""
    return PAIR_BACKTEST_VERDICT.get((pair_a, pair_b), "UNKNOWN") in VALIDATED_VERDICTS


@dataclass
class PairZSignal:
    pair_a: str
    pair_b: str
    sector_a: str
    sector_b: str
    same_sector: bool
    ratio_now: float
    mean: float
    std: float
    z: float
    direction: str         # "BUY_A" / "BUY_B" / "NEUTRAL"
    n_bars: int


def _fetch_aligned_ohlcv(
    storage,
    pair_a: str,
    pair_b: str,
    exchange: str = "bybit",
    timeframe: str = "1h",
    lookback_h: int = DEFAULT_LOOKBACK_HOURS,
) -> pd.DataFrame:
    """Pull aligned close prices for both symbols, inner-join on ts."""
    cutoff = datetime.now(timezone.utc) - pd.Timedelta(hours=lookback_h)
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
        return pd.DataFrame()
    merged = pd.merge(
        df_a.rename(columns={"close": "a"}),
        df_b.rename(columns={"close": "b"}),
        on="ts", how="inner",
    )
    return merged


def compute_pair_zsignal(
    storage,
    pair_a: str,
    pair_b: str,
    exchange: str = "bybit",
    timeframe: str = "1h",
    lookback_h: int = DEFAULT_LOOKBACK_HOURS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> Optional[PairZSignal]:
    """Single pair z-score signal. Returns None if insufficient data."""
    df = _fetch_aligned_ohlcv(storage, pair_a, pair_b, exchange, timeframe, lookback_h)
    if df.empty or len(df) < 50:
        return None
    ratio = df["a"] / df["b"]
    mean = float(ratio.mean())
    std = float(ratio.std())
    now = float(ratio.iloc[-1])
    if std <= 0:
        return None
    z = (now - mean) / std
    direction = "NEUTRAL"
    if z <= -z_threshold:
        direction = "BUY_A"   # A relatively cheap
    elif z >= z_threshold:
        direction = "BUY_B"   # B relatively cheap (A overvalued)
    sec_a = get_sector(pair_a)
    sec_b = get_sector(pair_b)
    return PairZSignal(
        pair_a=pair_a, pair_b=pair_b,
        sector_a=sec_a, sector_b=sec_b,
        same_sector=(sec_a == sec_b and sec_a != "UNKNOWN"),
        ratio_now=now, mean=mean, std=std, z=z,
        direction=direction, n_bars=len(df),
    )


def scan_spot_pairs(
    storage,
    pairs: Optional[list[tuple[str, str]]] = None,
    exchange: str = "bybit",
    timeframe: str = "1h",
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> list[PairZSignal]:
    """Scan default pair list (or custom). Return all signals (even NEUTRAL)
    sorted by |z| desc."""
    pairs = pairs or DEFAULT_PAIRS
    out: list[PairZSignal] = []
    for pa, pb in pairs:
        sig = compute_pair_zsignal(storage, pa, pb, exchange, timeframe, z_threshold=z_threshold)
        if sig is not None:
            out.append(sig)
    out.sort(key=lambda s: -abs(s.z))
    return out


def pair_signals_as_candidates(signals: list[PairZSignal]) -> list[Candidate]:
    """Convert PairZSignals into Candidate list for unified scan output.

    Only emit when direction != NEUTRAL.

    Score logic uses backtest validation:
    - VALIDATED pair (STABLE_POSITIVE / MARGINAL_POSITIVE) → base 7 + |z|
    - INCONSISTENT pair → base 4 + |z| (penalize, less conviction)
    - STABLE_NEGATIVE pair → score 1 (avoid signal — historical anti-edge)
    """
    out: list[Candidate] = []
    for s in signals:
        if s.direction == "NEUTRAL":
            continue
        target = s.pair_a if s.direction == "BUY_A" else s.pair_b
        other = s.pair_b if s.direction == "BUY_A" else s.pair_a
        base = target.split("/")[0]
        verdict = PAIR_BACKTEST_VERDICT.get((s.pair_a, s.pair_b), "UNKNOWN")
        if verdict == "STABLE_NEGATIVE":
            score = 1.0  # historical anti-edge, avoid
            verdict_note = "AVOID (backtest STABLE_NEGATIVE)"
        elif verdict in VALIDATED_VERDICTS:
            score = min(9.0, 7.0 + abs(s.z))
            verdict_note = f"backtest {verdict}"
        elif verdict == "INCONSISTENT":
            score = min(7.0, 4.0 + abs(s.z))
            verdict_note = f"backtest {verdict} — low conviction"
        else:
            score = min(8.0, 5.0 + abs(s.z))
            verdict_note = f"backtest {verdict}"
        out.append(Candidate(
            symbol=target,
            base=base,
            kind="REL_VALUE",
            event_type="PAIR_ZSIGNAL",
            score=score,
            rationale=(
                f"{target} relatively cheap vs {other} "
                f"(ratio z={s.z:+.2f}, {verdict_note}). "
                f"Mean reversion expected if validated."
            ),
        ))
    return out
