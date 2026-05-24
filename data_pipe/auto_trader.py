"""Autonomous trading decision engine — Phase 4.

Composes signal + positions + account state → decision (OPEN / ROTATE /
CLOSE / SKIP). Executes via live_executor with auto-protect.

Decision philosophy (defensive defaults):
1. Trade only VALIDATED signals (PAIR_BACKTEST_VERDICT ∈ {STABLE_POS, MARGINAL_POS})
2. Honor max_trades_per_day cap (currently 4)
3. Honor max_concurrent cap (currently 2)
4. Cooldown 30min between auto-trades (anti-spam)
5. Rotation: only if NEW conviction > existing min by margin
6. Position size = allocation.max_per_position_usdt
7. Auto-protect (OCO) every entry
8. Permanent floor + daily killswitch always respected (live_executor gates)
9. Anti-edge signals (STABLE_NEGATIVE) NEVER auto-traded — only alerted

Audit: every decision logged to data/auto_trader.log + Telegram if configured.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .brokers import BinanceSpotBroker
from .live_executor import execute_live_close, execute_live_open
from .portfolio import get_account_state
from .risk_guard import check_max_trades_per_day
from .spot_pairs import (
    PAIR_BACKTEST_VERDICT, VALIDATED_VERDICTS, compute_pair_zsignal,
)
from .storage import Storage
from .strategy import allocate_capital
from .telegram import send_message

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_LOG = REPO_ROOT / "data" / "auto_trader.log"
STATE_FILE = REPO_ROOT / "data" / "auto_trader.state.json"

# Conviction tiers
CONVICTION_BY_VERDICT = {
    "STABLE_POSITIVE": 8.0,
    "MARGINAL_POSITIVE": 6.0,
    "INCONSISTENT": 3.0,
    "RECENTLY_BROKEN": 2.0,
    "STABLE_NEGATIVE": 0.0,  # Never auto-trade
    "NO_SIGNALS": 0.0,
}

# Min cooldown between auto-trades (seconds)
DEFAULT_COOLDOWN_SEC = 1800  # 30 min

# Min conviction delta required to ROTATE (close existing + open new)
ROTATION_MIN_DELTA = 1.5

# Z-score reverted to (or beyond) this magnitude → consider closing existing pair-trade
Z_REVERT_THRESHOLD = 0.5


@dataclass
class Decision:
    action: str             # "OPEN" / "CLOSE" / "ROTATE" / "SKIP" / "ERROR"
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    signal_pair: Optional[tuple[str, str]] = None
    signal_z: Optional[float] = None
    target_symbol: Optional[str] = None
    target_usdt: Optional[float] = None
    close_trade_id: Optional[int] = None
    conviction: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("signal_pair") and isinstance(d["signal_pair"], (list, tuple)):
            d["signal_pair"] = list(d["signal_pair"])
        return d


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _audit(decision: Decision) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"[{decision.timestamp[:19].replace('T', ' ')} UTC] "
        f"{decision.action:<6} {decision.target_symbol or '-':<14} "
        f"reason={decision.reason}\n"
    )
    with AUDIT_LOG.open("a") as f:
        f.write(line)


def _is_in_cooldown(state: dict, cooldown_sec: int) -> tuple[bool, float]:
    last = state.get("last_auto_trade_ts")
    if not last:
        return False, 0
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return False, 0
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return elapsed < cooldown_sec, elapsed


def _get_pair_from_trade_thesis(thesis: str) -> Optional[tuple[str, str]]:
    """Extract source pair from thesis text (heuristic). Returns None if absent.

    Recognizes formats like:
      "validated signal NEAR/AVAX z=+3.76"
      "z-signal SUI/SOL +2.5"
    """
    if not thesis:
        return None
    import re
    # Match patterns like "X/USDT/Y/USDT" or "X/Y" first
    m = re.search(r"([A-Z]{2,8})/([A-Z]{2,8})\s+z=", thesis)
    if not m:
        m = re.search(r"signal\s+([A-Z]{2,8})/([A-Z]{2,8})", thesis)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    return (f"{a}/USDT", f"{b}/USDT")


def _evaluate_close_for_zrevert(
    storage: Storage,
    trade_row: dict,
) -> Optional[Decision]:
    """If trade was opened via pair signal, check if z has reverted enough to close."""
    thesis = trade_row.get("thesis", "")
    pair = _get_pair_from_trade_thesis(thesis)
    if not pair:
        return None
    sig = compute_pair_zsignal(storage, pair[0], pair[1])
    if sig is None:
        return None
    # Reverted = |z| now <= Z_REVERT_THRESHOLD
    if abs(sig.z) <= Z_REVERT_THRESHOLD:
        return Decision(
            action="CLOSE",
            reason=(
                f"z-revert thesis fulfilled: source pair {pair[0]}/{pair[1]} "
                f"z={sig.z:+.2f} (was opened on outer-band signal)"
            ),
            close_trade_id=int(trade_row["id"]),
            target_symbol=str(trade_row["pair"]),
            signal_pair=pair,
            signal_z=float(sig.z),
        )
    return None


def decide_actions(
    storage: Storage,
    cfg: dict,
    broker: BinanceSpotBroker,
    state: dict,
    cooldown_sec: int = DEFAULT_COOLDOWN_SEC,
) -> list[Decision]:
    """Evaluate all signals + open positions, return list of decisions to take.

    Order:
    1. CLOSE candidates (z-revert exit) — frees capacity for next signal
    2. OPEN candidates (validated signal cross, slot available)
    3. ROTATE candidates (validated signal, no slot, but conviction > existing)

    Returns list (can be empty).
    """
    decisions: list[Decision] = []

    # Cooldown check
    cd_active, elapsed = _is_in_cooldown(state, cooldown_sec)
    if cd_active:
        # Still log status decisions but no execute
        decisions.append(Decision(
            action="SKIP",
            reason=f"cooldown active ({elapsed:.0f}s elapsed, need ≥{cooldown_sec}s)",
        ))
        return decisions

    # ───── Step 1: CLOSE candidates (z-revert on open positions) ─────
    open_df = storage.get_trades(status="OPEN")
    open_records = open_df.to_dict("records") if not open_df.empty else []

    for trade in open_records:
        close_dec = _evaluate_close_for_zrevert(storage, trade)
        if close_dec:
            decisions.append(close_dec)

    # ───── Step 2: OPEN candidates via ENSEMBLE scoring ─────
    # Aggregate signals across validated pairs by target symbol
    from .ensemble_signals import compute_ensemble_signals, rank_ensemble

    ensembles = compute_ensemble_signals(storage, threshold=2.0)
    if not ensembles:
        if not decisions:
            decisions.append(Decision(action="SKIP", reason="no validated signals + no z-reverts"))
        return decisions

    ranked = rank_ensemble(ensembles)

    # Account state for cap check
    state_account = get_account_state(storage, cfg)
    trade_cap = check_max_trades_per_day(storage, cfg)
    n_open = state_account["n_open"]
    max_concurrent = state_account["max_concurrent"]

    # Effective equity for allocation
    try:
        bal = broker.fetch_spot_balance()
        usdt_total = sum(
            (v if asset == "USDT" else 0) for asset, v in bal.items()
        )
        equity_proxy = max(usdt_total, state_account["equity"])
    except Exception:
        equity_proxy = state_account["equity"]
    alloc = allocate_capital(equity_proxy, cfg)
    base_per_position = alloc.max_per_position_usdt

    # Don't trade if per-position too small
    if base_per_position < 5.5:
        decisions.append(Decision(
            action="SKIP",
            reason=f"per-position allocation ${base_per_position:.2f} < $5.50 (min_notional + safety)",
        ))
        return decisions

    # Take top-ranked ensemble (1 auto-decision per cycle)
    top = ranked[0]
    target_symbol = top.target_symbol
    conviction = top.effective_conviction
    # Sizing scaled by ensemble — capped by max_position_pct of equity
    max_position_usdt = state_account["equity"] * state_account["max_position_pct"] / 100.0
    scaled = base_per_position * top.sizing_multiplier
    usdt_per_position = min(scaled, max_position_usdt)
    # Synthesize a "pair" for downstream compatibility (use highest |z| supporting)
    if top.supporting_pairs:
        sp = max(top.supporting_pairs, key=lambda p: abs(p[2]))
        pair = (sp[0], sp[1])
        z = sp[2]
        verdict = PAIR_BACKTEST_VERDICT.get(pair, "UNKNOWN")
    else:
        pair, z, verdict = (target_symbol, target_symbol), 0.0, "UNKNOWN"

    # Check if we already have this exact position open
    have_target_open = any(
        str(r.get("pair", "")) == target_symbol for r in open_records
    )
    if have_target_open:
        decisions.append(Decision(
            action="SKIP",
            reason=f"{target_symbol} already held",
            signal_pair=pair, signal_z=z, conviction=conviction,
        ))
        return decisions

    # Cap check: open slot?
    has_slot = (n_open < max_concurrent) and (not trade_cap["exceeded"])

    if has_slot:
        decisions.append(Decision(
            action="OPEN",
            reason=(
                f"ENSEMBLE n={top.n_supporting} (conflicts={top.n_conflicting}) "
                f"target={target_symbol} eff_conviction={conviction:.2f} "
                f"max_z={top.max_abs_z:.2f} sizing×{top.sizing_multiplier:.2f}, "
                f"slot {n_open}/{max_concurrent}, trades {trade_cap['today']}/{trade_cap['cap']}"
            ),
            signal_pair=pair, signal_z=z,
            target_symbol=target_symbol, target_usdt=usdt_per_position,
            conviction=conviction,
            metadata={"ensemble": top.to_dict()},
        ))
        return decisions

    # No slot — consider ROTATION
    # Find weakest existing position (lowest conviction)
    weakest = None
    weakest_conv = float("inf")
    for trade in open_records:
        src_pair = _get_pair_from_trade_thesis(trade.get("thesis", ""))
        if src_pair is None:
            existing_conv = 4.0  # untracked source = medium-low default
        else:
            v = PAIR_BACKTEST_VERDICT.get(src_pair, "UNKNOWN")
            existing_conv = CONVICTION_BY_VERDICT.get(v, 3.0)
        if existing_conv < weakest_conv:
            weakest_conv = existing_conv
            weakest = trade

    if weakest is None:
        decisions.append(Decision(
            action="SKIP",
            reason="no slot + no rotatable position",
            signal_pair=pair, signal_z=z, conviction=conviction,
        ))
        return decisions

    if conviction - weakest_conv >= ROTATION_MIN_DELTA:
        decisions.append(Decision(
            action="ROTATE",
            reason=(
                f"new conviction {conviction:.2f} > existing weakest {weakest_conv:.2f} "
                f"by ≥{ROTATION_MIN_DELTA}; close #{weakest['id']} {weakest['pair']} "
                f"to open {target_symbol} on signal {pair[0]}/{pair[1]} z={z:+.2f}"
            ),
            signal_pair=pair, signal_z=z,
            target_symbol=target_symbol, target_usdt=usdt_per_position,
            close_trade_id=int(weakest["id"]),
            conviction=conviction,
            metadata={"existing_conviction": weakest_conv,
                      "rotate_out_pair": str(weakest["pair"])},
        ))
    else:
        decisions.append(Decision(
            action="SKIP",
            reason=(
                f"new conviction {conviction:.2f} not > existing weakest "
                f"{weakest_conv:.2f} by required {ROTATION_MIN_DELTA}; hold"
            ),
            signal_pair=pair, signal_z=z, conviction=conviction,
        ))

    return decisions


def execute_decision(
    decision: Decision,
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    broker: BinanceSpotBroker,
    state: dict,
) -> dict:
    """Execute a decision via live_executor. Returns result + updated state."""
    result = {"decision": decision.to_dict(), "executed": False, "outputs": []}

    if decision.action in ("SKIP", "ERROR"):
        _audit(decision)
        return result

    # CLOSE / ROTATE: close existing first
    if decision.action in ("CLOSE", "ROTATE"):
        if decision.close_trade_id is None:
            decision.action = "ERROR"
            decision.reason = "CLOSE/ROTATE without close_trade_id"
            _audit(decision)
            return result
        # Cancel OCO first (best-effort) to avoid SELL conflict with locked qty
        try:
            from .protection import cancel_protection
            cancel_protection(broker, decision.close_trade_id)
        except Exception:
            pass
        close_res = execute_live_close(
            storage=storage, cfg=cfg, data_dir=data_dir,
            broker=broker, trade_id=decision.close_trade_id,
        )
        result["outputs"].append({
            "step": "close",
            "trade_id": decision.close_trade_id,
            "action": close_res.action,
            "reason": close_res.reason,
        })
        if close_res.action != "CLOSE":
            decision.action = "ERROR"
            decision.reason = f"close-leg failed: {close_res.reason}"
            _audit(decision)
            return result

    # OPEN / ROTATE: open new
    if decision.action in ("OPEN", "ROTATE"):
        if not decision.target_symbol or not decision.target_usdt:
            decision.action = "ERROR"
            decision.reason = "OPEN/ROTATE without target_symbol/usdt"
            _audit(decision)
            return result
        thesis = (
            f"AUTO {decision.action} "
            f"z-signal {decision.signal_pair[0]}/{decision.signal_pair[1]} "
            f"z={decision.signal_z:+.2f} conviction={decision.conviction:.2f}"
        ) if decision.signal_pair else f"AUTO {decision.action}"
        open_res = execute_live_open(
            storage=storage, cfg=cfg, data_dir=data_dir,
            broker=broker, symbol=decision.target_symbol,
            usdt_amount=decision.target_usdt,
            thesis=thesis,
            auto_protect=True,
        )
        result["outputs"].append({
            "step": "open",
            "symbol": decision.target_symbol,
            "usdt": decision.target_usdt,
            "action": open_res.action,
            "reason": open_res.reason,
            "trade_id": open_res.trade_id,
        })
        if open_res.action != "OPEN":
            decision.action = "ERROR"
            decision.reason = f"open-leg failed: {open_res.reason}"
            _audit(decision)
            return result

    result["executed"] = True
    state["last_auto_trade_ts"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    _audit(decision)

    # Telegram fire
    try:
        send_message(
            f"*[AUTO {decision.action}]*\n"
            f"  symbol: `{decision.target_symbol}`\n"
            f"  usdt: `{decision.target_usdt}`\n"
            f"  reason: {decision.reason[:200]}\n"
            f"  conviction: `{decision.conviction:.2f}`",
            silent=False,
        )
    except Exception:
        pass

    return result


def run_auto_cycle(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    broker: BinanceSpotBroker,
    cooldown_sec: int = DEFAULT_COOLDOWN_SEC,
) -> list[dict]:
    """One auto-trade cycle: decide → execute → log. Returns list of results."""
    state = _load_state()
    decisions = decide_actions(storage, cfg, broker, state, cooldown_sec)
    results = []
    for d in decisions:
        r = execute_decision(d, storage, cfg, data_dir, broker, state)
        results.append(r)
    return results
