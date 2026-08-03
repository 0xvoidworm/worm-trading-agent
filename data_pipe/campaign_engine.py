#!/usr/bin/env python3
"""BRUTAL1000 autonomous campaign engine (decision layer).

This module is the pure, testable brain of the autonomous live campaign the
operator asked for: once armed, the bot runs entry -> protect -> exit -> next
entry on its own, and only stops on a bounded, pre-declared end condition or a
structural fault. There is NO per-candle human intervention.

Design invariants (why this file is pure and network-free):
  * Every decision here is a deterministic function of explicit inputs, so the
    behavior can be graded by tests that never touch Binance.
  * Exit geometry is delegated to brutal1000_exit_policy.next_status so live and
    paper close on the SAME rule (-1R stop, +1R breakeven arm, +1.5R trail arm /
    0.75R giveback, +2R take profit). No second, drifting copy.
  * The live wrapper (scripts/brutalctl.py) injects a broker + candidate source;
    it must not re-implement any of the decisions below.

Nothing in this module places an order, reads secrets, or prints raw secrets.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import fcntl
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


_EXIT_POLICY_PATH = Path.home() / "brutal1000_scanner" / "brutal1000_exit_policy.py"


def _load_exit_policy():
    spec = importlib.util.spec_from_file_location("brutal1000_exit_policy", _EXIT_POLICY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load exit policy from {_EXIT_POLICY_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_EXIT = _load_exit_policy()
HARD_STOP = float(_EXIT.HARD_STOP)
TAKE_PROFIT = float(_EXIT.TAKE_PROFIT)
BREAKEVEN_ARM = float(_EXIT.BREAKEVEN_ARM)
TRAIL_ARM = float(_EXIT.TRAIL_ARM)
TRAIL_GIVEBACK = float(_EXIT.TRAIL_GIVEBACK)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(ts: str) -> datetime:
    """Parse an ISO8601 timestamp to an aware UTC datetime."""
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class CampaignConfig:
    """Immutable-by-convention campaign contract. Set once at arm time."""

    campaign_id: str
    risk_pct: float = 0.25
    leverage: int = 2
    max_concurrent: int = 1
    max_entries_per_day: int = 4
    max_closed_trades: int = 50
    max_days: float = 21.0
    loss_budget_r: float = 20.0
    freshness_sec: float = 600.0
    cooldown_sec: float = 1800.0
    entry_min_r: float = -0.10
    entry_max_r: float = 0.25
    lane: str = "CEX_PERP"
    live: bool = False

    def validate(self) -> None:
        if not self.campaign_id:
            raise ValueError("campaign_id required")
        if not (0 < self.risk_pct <= 0.5):
            raise ValueError("risk_pct must be in (0, 0.5]")
        if self.leverage not in (1, 2):
            raise ValueError("leverage must be 1 or 2 for the pilot")
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if self.max_entries_per_day < 1:
            raise ValueError("max_entries_per_day must be >= 1")
        if self.max_closed_trades < 1:
            raise ValueError("max_closed_trades must be >= 1")
        if self.max_days <= 0:
            raise ValueError("max_days must be > 0")
        if self.loss_budget_r <= 0:
            raise ValueError("loss_budget_r must be > 0")
        if self.entry_min_r >= self.entry_max_r:
            raise ValueError("entry_min_r must be < entry_max_r")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CampaignConfig":
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class Position:
    """A live campaign position. current_r/peak_r are updated each tick."""

    signal_id: str
    instrument: str
    side: str
    entry: float
    stop: float
    tp: float
    qty: float
    opened_at: str
    campaign_id: str = ""
    risk_usd: float = 0.0
    entry_fee_usd: float = 0.0
    peak_r: float = 0.0
    current_r: float = 0.0
    protective_stop: Optional[float] = None
    entry_order_id: Optional[str] = None
    entry_client_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    sl_client_id: Optional[str] = None
    retired_stop_client_ids: list[str] = field(default_factory=list)
    tp_order_id: Optional[str] = None
    tp_client_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Position":
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in fields})


@dataclass
class CampaignState:
    status: str = "RUNNING"
    started_at: str = ""
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    realized_r: float = 0.0
    gross_win_r: float = 0.0
    gross_loss_r: float = 0.0
    realized_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    equity_peak_r: float = 0.0
    max_drawdown_r: float = 0.0
    closed_records: list[dict[str, Any]] = field(default_factory=list)
    entry_records: list[dict[str, Any]] = field(default_factory=list)
    executed_signal_ids: list[str] = field(default_factory=list)
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    last_exit_at: Optional[str] = None
    stop_reason: Optional[str] = None
    halt_reason: Optional[str] = None
    pending_entry: Optional[dict[str, Any]] = None
    last_cycle_at: Optional[str] = None
    last_cycle_result: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CampaignState":
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in fields})


def compute_r(side: str, entry: float, stop: float, price: float) -> float:
    """Realized R multiple of price vs entry, given the hard stop distance."""
    dist = abs(float(entry) - float(stop))
    if dist <= 0:
        raise ValueError("stop distance is zero")
    s = side.upper()
    if s == "LONG":
        return (float(price) - float(entry)) / dist
    if s == "SHORT":
        return (float(entry) - float(price)) / dist
    raise ValueError(f"unknown side {side!r}")


def size_qty(
    bankroll_usd: float,
    risk_pct: float,
    entry: float,
    stop: float,
    step_size: float,
    min_qty: float,
    allow_min_qty_risk_overrun: bool = False,
) -> float:
    """Quantity so a stop-out loses ~risk_pct% of bankroll. Snapped down to step."""
    dist = abs(float(entry) - float(stop))
    if dist <= 0 or bankroll_usd <= 0 or risk_pct <= 0:
        return 0.0
    risk_usd = bankroll_usd * risk_pct / 100.0
    raw = risk_usd / dist
    if step_size and step_size > 0:
        qty = math.floor(raw / step_size) * step_size
    else:
        qty = raw
    if min_qty and qty < min_qty:
        if not allow_min_qty_risk_overrun:
            return 0.0
        qty = min_qty
    return float(qty)


def targets_from_entry(side: str, entry: float, stop: float) -> tuple[float, float]:
    """(hard_stop_price, take_profit_price) implied by entry+stop and the policy."""
    dist = abs(float(entry) - float(stop))
    s = side.upper()
    if s == "LONG":
        return float(stop), float(entry) + TAKE_PROFIT * dist
    if s == "SHORT":
        return float(stop), float(entry) - TAKE_PROFIT * dist
    raise ValueError(f"unknown side {side!r}")


def protective_stop_price(
    side: str,
    entry: float,
    stop: float,
    peak_r: float,
) -> float:
    """Exchange stop level implied by the shared paper exit policy.

    Before +1R MFE the original hard stop remains. At +1R it moves to
    breakeven. At +1.5R it trails peak by 0.75R. This gives the live position
    exchange-side protection even if the controller is briefly offline.
    """
    entry = float(entry)
    stop = float(stop)
    peak = max(0.0, float(peak_r))
    dist = abs(entry - stop)
    if dist <= 0:
        raise ValueError("stop distance is zero")
    locked_r = HARD_STOP
    if peak >= BREAKEVEN_ARM:
        locked_r = 0.0
    if peak >= TRAIL_ARM:
        locked_r = max(locked_r, round(peak - TRAIL_GIVEBACK, 4))
    s = side.upper()
    if s == "LONG":
        return entry + locked_r * dist
    if s == "SHORT":
        return entry - locked_r * dist
    raise ValueError(f"unknown side {side!r}")


def select_entry(
    candidates: Iterable[dict[str, Any]],
    *,
    now: datetime,
    started_at: datetime,
    executed_ids: Iterable[str],
    open_count: int,
    last_exit_at: Optional[datetime],
    cfg: CampaignConfig,
) -> dict[str, Any]:
    """Choose at most one entry. Returns {'action': 'ENTER'|'SKIP', ...}."""
    if open_count >= cfg.max_concurrent:
        return {"action": "SKIP", "reason": "CAPACITY"}
    if last_exit_at is not None:
        if (now - last_exit_at).total_seconds() < cfg.cooldown_sec:
            return {"action": "SKIP", "reason": "COOLDOWN"}

    done = set(executed_ids)
    eligible: list[dict[str, Any]] = []
    for c in candidates:
        if str(c.get("lane", cfg.lane)) != cfg.lane:
            continue
        sid = str(c.get("signal_id") or "")
        if not sid or sid in done:
            continue
        try:
            detected = parse_iso(c["detected_at"])
        except Exception:
            continue
        if detected < started_at:
            continue
        if (now - detected).total_seconds() > cfg.freshness_sec:
            continue
        side = str(c.get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            continue
        try:
            if abs(float(c["entry_ref"]) - float(c["stop_ref"])) <= 0:
                continue
        except Exception:
            continue
        eligible.append(c)

    if not eligible:
        return {"action": "SKIP", "reason": "NO_FRESH_CANDIDATE"}

    eligible.sort(key=lambda c: (-float(c.get("score") or 0.0), str(c.get("signal_id"))))
    return {"action": "ENTER", "candidate": eligible[0]}


def exit_decision(position: dict[str, Any] | Position, price: float) -> Optional[dict[str, Any]]:
    """Return an exit dict if the position should close now, else None."""
    p = position.to_dict() if isinstance(position, Position) else dict(position)
    r = compute_r(p["side"], p["entry"], p["stop"], price)
    peak = max(float(p.get("peak_r", 0.0)), r)
    decision = _EXIT.next_status(peak, r)
    if decision is None:
        return None
    exit_r, status = decision
    return {"exit_r": float(exit_r), "status": status, "current_r": r, "peak_r": peak}


def mark_position(position: dict[str, Any] | Position, price: float) -> dict[str, Any]:
    """Return the position dict with current_r/peak_r refreshed (no exit logic)."""
    p = position.to_dict() if isinstance(position, Position) else dict(position)
    r = compute_r(p["side"], p["entry"], p["stop"], price)
    p["current_r"] = r
    p["peak_r"] = max(float(p.get("peak_r", 0.0)), r)
    return p


def stop_reason(state: CampaignState, cfg: CampaignConfig, now: datetime) -> Optional[str]:
    """Bounded end conditions. Returns a reason string or None."""
    if state.closed_trades >= cfg.max_closed_trades:
        return "MAX_TRADES"
    if state.realized_r <= -abs(cfg.loss_budget_r):
        return "LOSS_BUDGET"
    if state.started_at:
        age_days = (now - parse_iso(state.started_at)).total_seconds() / 86400.0
        if age_days >= cfg.max_days:
            return "MAX_DAYS"
    return None


def structural_faults(
    state: CampaignState,
    exchange_positions: list[dict[str, Any]],
    exchange_triggers_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Compare campaign intent vs exchange truth. Any mismatch is a HALT reason."""
    faults: list[str] = []
    tracked = {normalize_instrument(p["instrument"]) for p in state.open_positions}
    ex_syms = {
        normalize_instrument(p.get("instrument") or p.get("symbol"))
        for p in exchange_positions
        if abs(float(p.get("contracts") or 0)) > 0
    }

    for pos in state.open_positions:
        sym = normalize_instrument(pos["instrument"])
        # If the exchange is already flat, a protective SL/TP may have filled
        # between controller cycles. Do not classify that normal lifecycle as
        # NAKED/MISSING here; the controller resolves the executed trigger from
        # order history before recording the exit.
        if sym not in ex_syms:
            continue
        trigs = (
            exchange_triggers_by_symbol.get(sym)
            or exchange_triggers_by_symbol.get(str(pos["instrument"]))
            or []
        )
        if not trigs:
            faults.append(f"NAKED_POSITION:{sym}")
            continue
        trigger_ids = {
            str(t.get("id") or (t.get("info") or {}).get("algoId") or "")
            for t in trigs
        }
        for field, label in (("sl_order_id", "SL"), ("tp_order_id", "TP")):
            expected = str(pos.get(field) or "")
            if expected and expected not in trigger_ids:
                faults.append(f"MISSING_{label}:{sym}:{expected}")
        kinds = []
        for trigger in trigs:
            info = trigger.get("info") or {}
            kinds.append(
                str(
                    trigger.get("type")
                    or info.get("orderType")
                    or info.get("origType")
                    or ""
                ).upper()
            )
        stop_count = sum(
            1 for kind in kinds
            if "STOP" in kind and "TAKE_PROFIT" not in kind
        )
        tp_count = sum(1 for kind in kinds if "TAKE_PROFIT" in kind)
        if stop_count != 1:
            faults.append(f"STOP_COUNT:{sym}:{stop_count}")
        if tp_count != 1:
            faults.append(f"TP_COUNT:{sym}:{tp_count}")

    for sym in ex_syms:
        if sym and sym not in tracked:
            faults.append(f"GHOST_POSITION:{sym}")

    for sym, trigs in exchange_triggers_by_symbol.items():
        norm = normalize_instrument(sym)
        if trigs and norm not in tracked and norm not in ex_syms:
            faults.append(f"ORPHAN_TRIGGERS:{norm}")

    return faults


def normalize_instrument(value: Any) -> str:
    """Normalize CCXT/Binance symbols to campaign form, e.g. AKEUSDT."""
    raw = str(value or "").upper().strip()
    if not raw:
        return ""
    raw = raw.split(":", 1)[0]
    return raw.replace("/", "").replace("-", "").replace("_", "")


DEFAULT_STATE_DIR = Path.home() / ".brutal_campaign"


class CampaignController:
    """Thin, restart-safe wrapper. Holds config+state, persists atomically."""

    def __init__(self, cfg: CampaignConfig, state_dir: Path = DEFAULT_STATE_DIR):
        cfg.validate()
        self.cfg = cfg
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.chmod(0o700)
        except Exception:
            pass
        self.state_file = self.dir / f"{cfg.campaign_id}.state.json"
        self.audit_file = self.dir / f"{cfg.campaign_id}.audit.jsonl"
        self.lock_file = self.dir / f"{cfg.campaign_id}.lock"
        self.state = self._load_state()

    def _load_state(self) -> CampaignState:
        if self.state_file.exists():
            return CampaignState.from_dict(json.loads(self.state_file.read_text()))
        return CampaignState(
            status="RUNNING",
            started_at=now_utc().replace(microsecond=0).isoformat(),
        )

    def save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        payload = json.dumps(self.state.to_dict(), indent=2)
        with tmp.open("w") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(self.state_file)
        try:
            self.state_file.chmod(0o600)
        except Exception:
            pass

    def audit(self, event: str, **payload: Any) -> None:
        rec = {"ts": now_utc().isoformat(timespec="seconds"), "event": event, **payload}
        with self.audit_file.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            self.audit_file.chmod(0o600)
        except Exception:
            pass

    def record_entry(self, pos: Position) -> None:
        self.state.open_positions.append(pos.to_dict())
        self.state.entry_records.append({
            "signal_id": pos.signal_id,
            "instrument": pos.instrument,
            "opened_at": pos.opened_at,
        })
        self.state.pending_entry = None
        if pos.signal_id not in self.state.executed_signal_ids:
            self.state.executed_signal_ids.append(pos.signal_id)
        self.audit("ENTER", **pos.to_dict())

    def record_exit(
        self,
        signal_id: str,
        exit_r: float,
        status: str,
        *,
        exit_price: Optional[float] = None,
        exit_fee_usd: float = 0.0,
    ) -> None:
        matched = next(
            (
                p for p in self.state.open_positions
                if p.get("signal_id") == signal_id
            ),
            {},
        )
        self.state.open_positions = [
            p for p in self.state.open_positions if p.get("signal_id") != signal_id
        ]
        self.state.closed_trades += 1
        self.state.realized_r = round(self.state.realized_r + float(exit_r), 4)
        if exit_r > 0:
            self.state.wins += 1
            self.state.gross_win_r = round(
                self.state.gross_win_r + float(exit_r), 4
            )
        elif exit_r < 0:
            self.state.losses += 1
            self.state.gross_loss_r = round(
                self.state.gross_loss_r + abs(float(exit_r)), 4
            )
        entry_fee = float(matched.get("entry_fee_usd") or 0.0)
        fees = entry_fee + float(exit_fee_usd or 0.0)
        risk_usd = float(matched.get("risk_usd") or 0.0)
        pnl_usd = risk_usd * float(exit_r) - fees
        self.state.fees_usd = round(self.state.fees_usd + fees, 8)
        self.state.realized_pnl_usd = round(
            self.state.realized_pnl_usd + pnl_usd, 8
        )
        self.state.equity_peak_r = max(
            self.state.equity_peak_r, self.state.realized_r
        )
        drawdown = self.state.realized_r - self.state.equity_peak_r
        self.state.max_drawdown_r = min(
            self.state.max_drawdown_r, round(drawdown, 4)
        )
        self.state.closed_records.append(
            {
                "signal_id": signal_id,
                "instrument": matched.get("instrument"),
                "side": matched.get("side"),
                "status": status,
                "exit_r": float(exit_r),
                "risk_usd": risk_usd,
                "fees_usd": fees,
                "pnl_usd": round(pnl_usd, 8),
                "entry": matched.get("entry"),
                "exit_price": exit_price,
                "opened_at": matched.get("opened_at"),
                "closed_at": now_utc().isoformat(timespec="seconds"),
            }
        )
        self.state.last_exit_at = now_utc().replace(microsecond=0).isoformat()
        self.audit("EXIT", signal_id=signal_id, exit_r=exit_r, status=status)

    def set_pending_entry(self, intent: dict[str, Any]) -> None:
        """Persist order intent before submission, preventing blind retries."""
        self.state.pending_entry = dict(intent)
        self.audit("ENTRY_INTENT", **intent)
        self.save()

    @contextmanager
    def cycle_lock(self):
        """Serialize tick/stop cycles across systemd and manual invocations."""
        with self.lock_file.open("a+") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    f"campaign {self.cfg.campaign_id!r} cycle already running"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def clear_pending_entry(self, outcome: str, **payload: Any) -> None:
        if self.state.pending_entry:
            self.audit(
                "ENTRY_INTENT_RESOLVED",
                outcome=outcome,
                intent=self.state.pending_entry,
                **payload,
            )
        self.state.pending_entry = None
        self.save()

    def halt(self, reason: str) -> None:
        self.state.status = "HALTED"
        self.state.halt_reason = reason
        self.audit("HALT", reason=reason)

    def maybe_complete(self, now: Optional[datetime] = None) -> None:
        now = now or now_utc()
        reason = stop_reason(self.state, self.cfg, now)
        if reason and not self.state.open_positions:
            self.state.status = "COMPLETED"
            self.state.stop_reason = reason
            self.audit("COMPLETE", reason=reason)
        elif reason:
            self.state.stop_reason = reason

    def entries_blocked(self, now: Optional[datetime] = None) -> Optional[str]:
        now = now or now_utc()
        if self.state.status != "RUNNING":
            return self.state.status
        bounded = stop_reason(self.state, self.cfg, now)
        if bounded:
            return bounded
        today = now.astimezone(timezone.utc).date()
        entries_today = 0
        for row in self.state.entry_records:
            try:
                if parse_iso(row["opened_at"]).date() == today:
                    entries_today += 1
            except Exception:
                continue
        if entries_today >= self.cfg.max_entries_per_day:
            return "DAILY_ENTRY_CAP"
        return None

    def summary(self) -> dict[str, Any]:
        closed = self.state.closed_trades
        exp = round(self.state.realized_r / closed, 4) if closed else 0.0
        win_rate = round(100.0 * self.state.wins / closed, 2) if closed else 0.0
        budget_used = min(1.0, max(0.0, -self.state.realized_r) / self.cfg.loss_budget_r)
        profit_factor = (
            round(self.state.gross_win_r / self.state.gross_loss_r, 4)
            if self.state.gross_loss_r > 0
            else ("inf" if self.state.gross_win_r > 0 else 0.0)
        )
        return {
            "campaign_id": self.cfg.campaign_id,
            "status": self.state.status,
            "live": self.cfg.live,
            "closed_trades": closed,
            "open_positions": len(self.state.open_positions),
            "realized_r": self.state.realized_r,
            "expectancy_r": exp,
            "win_rate_pct": win_rate,
            "wins": self.state.wins,
            "losses": self.state.losses,
            "profit_factor": profit_factor,
            "gross_win_r": self.state.gross_win_r,
            "gross_loss_r": self.state.gross_loss_r,
            "max_drawdown_r": self.state.max_drawdown_r,
            "realized_pnl_usd": self.state.realized_pnl_usd,
            "fees_usd": self.state.fees_usd,
            "loss_budget_r": self.cfg.loss_budget_r,
            "max_entries_per_day": self.cfg.max_entries_per_day,
            "budget_used_pct": round(100.0 * budget_used, 2),
            "stop_reason": self.state.stop_reason,
            "halt_reason": self.state.halt_reason,
        }
