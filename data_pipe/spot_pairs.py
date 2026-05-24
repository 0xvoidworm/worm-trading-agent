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

# Pairs to scan (must both exist in DB OHLCV) — EXPANDED 2026-05-24 after
# universe expansion backfill (18 new symbols). Includes both validated
# (will surface scan candidates) AND anti-edge (filtered to score 1).
DEFAULT_PAIRS: list[tuple[str, str]] = [
    # Original 6 (DO NOT remove — backward compat with existing reports)
    ("ETH/USDT", "BTC/USDT"),
    ("SOL/USDT", "ETH/USDT"),
    ("AVAX/USDT", "SOL/USDT"),
    ("ARB/USDT", "OP/USDT"),
    ("BNB/USDT", "BTC/USDT"),
    ("XRP/USDT", "LTC/USDT"),
    # Round 1 validated (per universe_pairs 2026-05-24 first pass)
    ("NEAR/USDT", "AVAX/USDT"),
    ("SUI/USDT", "SOL/USDT"),
    ("SUI/USDT", "ETH/USDT"),
    ("TON/USDT", "ETH/USDT"),
    ("DOGE/USDT", "BTC/USDT"),
    ("APT/USDT", "SOL/USDT"),
    # Round 2 validated (added 2026-05-24 second pass)
    ("TRX/USDT", "ETH/USDT"),   # P1+P2+P3 all positive (consistent like NEAR/AVAX)
]

# Per-pair backtest verdict — REVISED 2026-05-24 (universe expansion).
# Uses walk-forward P3 (RECENT YEAR = May 2025 → May 2026).
# Full-history verdict shown as comment for context.
#
# Walk-forward exposed many "full-history validated" pairs broke recently
# (BNB/BTC, AVAX/SOL decay, ETH/BTC flipped negative). Universe expansion
# revealed new edges in alt-cluster pairs (NEAR/AVAX consistent across all
# periods, SUI/SOL strong recent +3.46% avg).
#
# CONVICTION = recent year only. Full history is suggestive but not decisive.
PAIR_BACKTEST_VERDICT: dict[tuple[str, str], str] = {
    # ─────── CPCV ROBUST (Lopez de Prado audit 2026-05-24, PBO 10.5%) ───────
    # Only pair surviving full statistical rigor: Sharpe>1, p<0.001, DSR>0.5, 91% positive folds
    ("NEAR/USDT", "AVAX/USDT"): "CPCV_ROBUST",        # CPCV Sharpe=1.47 DSR=0.80 91% positive folds 496 trades
    # ─────── MARGINAL (CPCV positive but DSR≈0 — survives single-test, fails multi-comparison) ───────
    ("AVAX/USDT", "SOL/USDT"):  "MARGINAL_POSITIVE",  # CPCV Sharpe=0.90 62% pos 484 trades
    ("DOGE/USDT", "BTC/USDT"):  "MARGINAL_POSITIVE",  # CPCV Sharpe=0.79 69% pos 392 trades
    ("TRX/USDT", "ETH/USDT"):   "MARGINAL_POSITIVE",  # CPCV Sharpe=0.72 58% pos 458 trades
    ("APT/USDT", "SOL/USDT"):   "MARGINAL_POSITIVE",  # CPCV Sharpe=0.51 58% pos 412 trades (p=0.08 borderline)
    ("TON/USDT", "ETH/USDT"):   "MARGINAL_POSITIVE",  # CPCV Sharpe=0.40 62% pos 405 trades
    ("SUI/USDT", "SOL/USDT"):   "MARGINAL_POSITIVE",  # CPCV Sharpe=0.16 high mean +3.78% but high variance (p=0.26)
    # ─────── DEMOTED by CPCV (was MARGINAL_POSITIVE) ───────
    ("SUI/USDT", "ETH/USDT"):   "INCONSISTENT",       # CPCV Sharpe=-0.07 NEG, 49% pos folds, p=0.60
    # ─────── Recently BROKEN ───────
    ("BNB/USDT", "BTC/USDT"):   "RECENTLY_BROKEN",    # P3 31% -2.31%
    # ─────── Inconsistent ───────
    ("SOL/USDT", "ETH/USDT"):   "INCONSISTENT",
    # ─────── Anti-edge — AVOID signals ───────
    ("ETH/USDT", "BTC/USDT"):   "STABLE_NEGATIVE",    # P3 31% -4.38%
    ("ARB/USDT", "OP/USDT"):    "STABLE_NEGATIVE",    # P3 22% -5.10%
    ("ENA/USDT", "ETH/USDT"):   "STABLE_NEGATIVE",    # P3 13% -7.73% — WORST recent
    ("STRK/USDT", "ARB/USDT"):  "STABLE_NEGATIVE",    # P3 14% -7.29%
    ("STX/USDT", "ETH/USDT"):   "STABLE_NEGATIVE",    # P3 25% -5.19%
    ("FET/USDT", "WLD/USDT"):   "STABLE_NEGATIVE",    # P3 25% -4.95%
    ("FET/USDT", "ETH/USDT"):   "STABLE_NEGATIVE",    # P3 36% -4.03%
    ("PYTH/USDT", "LINK/USDT"): "STABLE_NEGATIVE",    # P3 39% -4.04%
    ("POL/USDT", "ARB/USDT"):   "STABLE_NEGATIVE",    # P3 36% -3.89%
    ("SEI/USDT", "SOL/USDT"):   "STABLE_NEGATIVE",    # P3 20% -3.38%
    ("DOT/USDT", "ETH/USDT"):   "STABLE_NEGATIVE",    # P3 33% -3.47%
    ("WIF/USDT", "BONK/USDT"):  "STABLE_NEGATIVE",    # P3 35% -2.64%
    ("SHIB/USDT", "DOGE/USDT"): "STABLE_NEGATIVE",    # P3 38% -2.35%
    ("TIA/USDT", "ETH/USDT"):   "STABLE_NEGATIVE",    # P3 31% -1.30%
    ("CAKE/USDT", "UNI/USDT"):  "STABLE_NEGATIVE",    # P3 38% -1.80%
    ("NEAR/USDT", "SOL/USDT"):  "STABLE_NEGATIVE",    # P3 32% -1.22%
    # ─────── No-data / insufficient ───────
    ("XRP/USDT", "LTC/USDT"):   "NO_SIGNALS",
}

# CPCV_ROBUST > STABLE_POSITIVE > MARGINAL_POSITIVE in conviction order.
# Only these 3 count as actionable (auto-trade eligible).
# "RECENTLY_BROKEN" = backtest claimed edge but walk-forward exposed decay.
# "STABLE_NEGATIVE" = anti-edge, AVOID signal.
# "OVERFIT_REJECTED" = CPCV PBO test exposed as noise.
VALIDATED_VERDICTS = {"CPCV_ROBUST", "STABLE_POSITIVE", "MARGINAL_POSITIVE"}


# Per-pair asymmetric TP/SL from MFE/MAE backtest analysis 2026-05-24.
# Methodology: TP ≈ p75 MFE (capture upper-half winners),
#              SL ≈ 2× median MAE (room before typical losers stop).
# Capped within practical bounds [2%, 15%].
PAIR_TP_SL: dict[tuple[str, str], dict[str, float]] = {
    # CPCV_ROBUST tier — best edge, asymmetric R:R favoring upside
    ("NEAR/USDT", "AVAX/USDT"): {"tp_pct": 12.0, "sl_pct": 6.0},   # MFE +11.2% MAE -4.0% → R:R 2.0:1
    # MARGINAL_POSITIVE tier — pair-specific tuning
    ("TRX/USDT", "ETH/USDT"):   {"tp_pct": 8.0,  "sl_pct": 4.0},   # MFE +4.6% MAE -2.4% → R:R 2.0:1 (tightest SL)
    ("AVAX/USDT", "SOL/USDT"):  {"tp_pct": 12.0, "sl_pct": 10.0},  # MFE +7.3% MAE -6.4% → R:R 1.2:1
    ("APT/USDT", "SOL/USDT"):   {"tp_pct": 14.0, "sl_pct": 10.0},  # MFE +8.8% MAE -7.0% → R:R 1.4:1
    ("DOGE/USDT", "BTC/USDT"):  {"tp_pct": 10.0, "sl_pct": 6.0},   # MFE +4.4% MAE -4.3% → R:R 1.67:1
    ("TON/USDT", "ETH/USDT"):   {"tp_pct": 10.0, "sl_pct": 8.0},   # MFE +5.5% MAE -5.3% → R:R 1.25:1
    ("SUI/USDT", "SOL/USDT"):   {"tp_pct": 15.0, "sl_pct": 10.0},  # MFE +6.3% MAE -8.6% → R:R 1.5:1
}

DEFAULT_TP_SL = {"tp_pct": 5.0, "sl_pct": 5.0}


def get_tp_sl_for_pair(pair_a: str, pair_b: str) -> dict[str, float]:
    """Return per-pair TP/SL config from backtest MFE/MAE analysis.
    Falls back to symmetric 5%/5% for unknown pairs.
    """
    return PAIR_TP_SL.get((pair_a, pair_b), dict(DEFAULT_TP_SL))


# Per-pair allowed-regime gate from regime-conditional backtest 2026-05-24.
# Only auto-trade when BTC HMM regime ∈ this set. Empty set = never trade.
# Derived: include regime only if n_trades ≥ 5 AND avg_pct_net > 0.
PAIR_REGIME_GATE: dict[tuple[str, str], set[str]] = {
    # NEAR/AVAX (CPCV_ROBUST): BULL +5.4% avg, NEUTRAL +3.1%, BEAR -3.5% (avoid)
    ("NEAR/USDT", "AVAX/USDT"): {"BULL", "NEUTRAL"},
    # AVAX/SOL INVERTED: BULL +3.9%, BEAR +4.7% (small n), NEUTRAL -1.5% (AVOID)
    ("AVAX/USDT", "SOL/USDT"):  {"BULL", "BEAR"},
    # TRX/ETH NEUTRAL specialist: NEUTRAL +1.9% wr 70%, BULL marginal, BEAR -3.3%
    ("TRX/USDT", "ETH/USDT"):   {"NEUTRAL"},
    # DOGE/BTC BULL-only: BULL +0.6% (weak), others negative
    ("DOGE/USDT", "BTC/USDT"):  {"BULL"},
    # APT/SOL NEUTRAL-only: NEUTRAL +1.4%, BULL NEGATIVE -1.7%
    ("APT/USDT", "SOL/USDT"):   {"NEUTRAL"},
    # SUI/SOL BULL-only: BULL +5.6%, NEUTRAL -0.2%
    ("SUI/USDT", "SOL/USDT"):   {"BULL"},
    # TON/ETH borderline: BULL +0.6%, NEUTRAL -0.1%, all weak — pick BULL+NEUTRAL
    ("TON/USDT", "ETH/USDT"):   {"BULL", "NEUTRAL"},
}


def is_pair_allowed_in_regime(pair_a: str, pair_b: str, regime: str) -> bool:
    """Check if pair is allowed to auto-trade in current BTC regime.

    Returns True if pair has no gate (e.g. unknown), default permissive.
    Returns False if pair has gate AND regime not in allowed set.
    """
    gate = PAIR_REGIME_GATE.get((pair_a, pair_b))
    if gate is None:
        return True  # no gate = permissive default
    return regime in gate


# Per-pair formal cointegration status (Engle-Granger ADF on log-prices 2026-05-24).
# Schema: (pair_a, pair_b) → {"coint": bool, "adf_p": float, "beta": float,
#                              "half_life_days": float, "max_hold_h": int}
#
# BRUTAL FINDING (audit 2026-05-24):
# Only 3/26 pairs PASS ADF p<0.05 (CAKE/UNI, POL/ARB, FET/WLD) — and we labeled
# them STABLE_NEGATIVE based on backtest! Validated pairs ALL fail classical
# cointegration test. Means our z-score edge captures sector rotation / sentiment
# patterns, NOT pure mean reversion.
#
# Implication: keep CPCV verdicts as primary (statistical edge confirmed).
# Use this map for:
#   - max_hold_h per-pair (4× half-life if applicable, else default 14d)
#   - small bonus conviction (+1) for pairs that ARE cointegrated AND CPCV-validated
PAIR_COINT_STATUS: dict[tuple[str, str], dict] = {
    ("NEAR/USDT", "AVAX/USDT"): {"coint": False, "adf_p": 0.515, "beta": 1.045, "half_life_days": 90.2, "max_hold_h": 21*24},
    ("AVAX/USDT", "SOL/USDT"):  {"coint": False, "adf_p": 0.747, "beta": 0.436, "half_life_days": 240.7, "max_hold_h": 21*24},
    ("DOGE/USDT", "BTC/USDT"):  {"coint": False, "adf_p": 0.261, "beta": 0.940, "half_life_days": 81.7, "max_hold_h": 21*24},
    ("TRX/USDT", "ETH/USDT"):   {"coint": False, "adf_p": 0.902, "beta": 0.799, "half_life_days": 1265, "max_hold_h": 14*24},
    ("APT/USDT", "SOL/USDT"):   {"coint": False, "adf_p": 0.970, "beta": 0.067, "half_life_days": 5012, "max_hold_h": 14*24},  # beta near 0 = no relationship
    ("TON/USDT", "ETH/USDT"):   {"coint": False, "adf_p": 0.575, "beta": 0.969, "half_life_days": 219.7, "max_hold_h": 21*24},
    ("SUI/USDT", "SOL/USDT"):   {"coint": False, "adf_p": 0.259, "beta": 0.714, "half_life_days": 112, "max_hold_h": 21*24},
    ("SUI/USDT", "ETH/USDT"):   {"coint": False, "adf_p": 0.599, "beta": 1.441, "half_life_days": 211, "max_hold_h": 21*24},
}


def get_max_hold_h_for_pair(pair_a: str, pair_b: str, default_h: int = 14*24) -> int:
    """Return per-pair max_hold_h based on half-life (4× hl capped 3-21d)."""
    status = PAIR_COINT_STATUS.get((pair_a, pair_b))
    if not status:
        return default_h
    return int(status.get("max_hold_h", default_h))


def is_pair_cointegrated(pair_a: str, pair_b: str) -> bool:
    """True if formal Engle-Granger ADF p < 0.05 confirms cointegration."""
    status = PAIR_COINT_STATUS.get((pair_a, pair_b))
    return bool(status and status.get("coint", False))


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
