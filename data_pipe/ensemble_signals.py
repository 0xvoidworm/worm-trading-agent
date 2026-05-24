"""Ensemble signal aggregation across validated pairs.

For each TARGET coin (USDT pair), collect all validated-pair signals that
recommend buying it. Sum convictions to produce composite ensemble score.

Why ensemble:
- Single pair signal = single perspective on coin's relative value
- If MULTIPLE validated pairs converge on same target → confidence higher
- Studies (and common sense): ensemble outperforms single signal

Used by auto_trader for position sizing:
  n_supporting=1 → 50% max_per_position (single perspective)
  n_supporting=2 → 75% (corroboration)
  n_supporting=3+ → 100% (strong consensus)

Also detects CONFLICT — if pair A says BUY X and pair B says SELL X (target
on opposite side), score adjusted down. In spot long-only context, conflict
= just don't BUY X (no SHORT capability).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .spot_pairs import (
    PAIR_BACKTEST_VERDICT,
    VALIDATED_VERDICTS,
    compute_pair_zsignal,
)


# Conviction weights per verdict (mirror auto_trader)
CONVICTION_BY_VERDICT = {
    "STABLE_POSITIVE": 8.0,
    "MARGINAL_POSITIVE": 6.0,
    "INCONSISTENT": 3.0,
    "RECENTLY_BROKEN": 2.0,
    "STABLE_NEGATIVE": 0.0,
    "NO_SIGNALS": 0.0,
}


@dataclass
class EnsembleSignal:
    target_symbol: str
    n_supporting: int = 0
    n_conflicting: int = 0  # other pairs say SELL/AVOID this target
    total_conviction: float = 0.0
    max_abs_z: float = 0.0
    supporting_pairs: list = field(default_factory=list)
    conflicting_pairs: list = field(default_factory=list)

    @property
    def sizing_multiplier(self) -> float:
        """Position size scaling vs base allocation.max_per_position_usdt.

        Single signal = 1.0 (no penalty — below min_notional sensitive)
        Multi signal = boost capped (caller still bounded by max_position_pct):
          2 signals → 1.25
          3+ signals → 1.5
        Conflicts reduce: -0.15 per conflict, min 0.5.
        """
        base = {1: 1.0, 2: 1.25}.get(self.n_supporting, 1.5 if self.n_supporting >= 3 else 0)
        penalty = min(0.5, 0.15 * self.n_conflicting)
        return max(0.5, base - penalty)

    @property
    def effective_conviction(self) -> float:
        """Conviction for ranking — penalized by conflicts."""
        return self.total_conviction - (2.0 * self.n_conflicting)

    def to_dict(self) -> dict:
        return {
            "target_symbol": self.target_symbol,
            "n_supporting": self.n_supporting,
            "n_conflicting": self.n_conflicting,
            "total_conviction": round(self.total_conviction, 2),
            "effective_conviction": round(self.effective_conviction, 2),
            "max_abs_z": round(self.max_abs_z, 2),
            "sizing_multiplier": round(self.sizing_multiplier, 2),
            "supporting": [
                {"pair_a": p[0], "pair_b": p[1], "z": round(p[2], 2),
                 "conviction": round(p[3], 2)}
                for p in self.supporting_pairs
            ],
            "conflicting": [
                {"pair_a": p[0], "pair_b": p[1], "z": round(p[2], 2)}
                for p in self.conflicting_pairs
            ],
        }


def compute_ensemble_signals(
    storage,
    threshold: float = 2.0,
) -> dict[str, EnsembleSignal]:
    """Aggregate validated pair signals by target symbol.

    For each validated pair (A,B):
    - z > +threshold → target = B (B is cheap vs A)
    - z < -threshold → target = A (A is cheap vs B)
    - |z| ≤ threshold → no signal (NEUTRAL)

    Anti-edge pairs (STABLE_NEGATIVE) are NOT included — pure noise.

    Returns dict keyed by target_symbol.
    """
    by_target: dict[str, EnsembleSignal] = {}

    for (pa, pb), verdict in PAIR_BACKTEST_VERDICT.items():
        if verdict not in VALIDATED_VERDICTS:
            continue
        sig = compute_pair_zsignal(storage, pa, pb, z_threshold=threshold)
        if sig is None or sig.direction == "NEUTRAL":
            continue

        target = pa if sig.direction == "BUY_A" else pb
        other = pb if sig.direction == "BUY_A" else pa
        base_conv = CONVICTION_BY_VERDICT.get(verdict, 0.0)
        conv = base_conv + abs(sig.z) * 0.5

        # Get-or-create target entry
        if target not in by_target:
            by_target[target] = EnsembleSignal(target_symbol=target)
        es = by_target[target]
        es.n_supporting += 1
        es.total_conviction += conv
        es.max_abs_z = max(es.max_abs_z, abs(sig.z))
        es.supporting_pairs.append((pa, pb, sig.z, conv))

        # Conflict detection: this signal also implies OTHER is overvalued.
        # If `other` is in by_target as a SUPPORTING target, that's a conflict.
        # E.g., BUY_B AVAX from NEAR/AVAX → NEAR is overvalued.
        #       If another pair NEAR/SOL fired BUY_A NEAR earlier, conflict.
        if other in by_target:
            by_target[other].n_conflicting += 1
            by_target[other].conflicting_pairs.append((pa, pb, sig.z))

    return by_target


def rank_ensemble(
    ensembles: dict[str, EnsembleSignal],
) -> list[EnsembleSignal]:
    """Sort ensembles by effective_conviction desc."""
    return sorted(
        ensembles.values(),
        key=lambda e: -e.effective_conviction,
    )
