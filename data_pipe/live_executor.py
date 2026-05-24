"""Live trade executor for LLM-driven / manual spot orders.

Companion to executor.py (which handles daemon detector → signal → verdict
flow for paper-trading). live_executor.py is the spot-only path for Phase 1+
LLM/manual orders:
- Direct order request (symbol, side, amount)
- Run risk gates (manual disable, permanent floor, daily killswitch,
  max_trades_per_day, exposure caps)
- Place order via Broker (BinanceSpotBroker or DryRunSpotBroker)
- Record to trade_journal with actual fill price + order id

Spot semantics:
- BUY  = open LONG position (direction=LONG, size=base_coin received)
- SELL = close existing LONG (compute PnL via storage.close_trade)
- SHORT not supported (spot only; margin would change risk profile)

Gates priority (ENTRY):
  1. Manual disable file (data/executor.disabled)        → block ALL
  2. Permanent floor killswitch                          → block ENTRY (EXIT allowed)
  3. Daily-loss killswitch                               → block ENTRY (EXIT allowed)
  4. max_trades_per_day cap                              → block ENTRY only
  5. Per-position / gross / concurrent caps              → block ENTRY only

EXIT gates: only #1 (manual disable). Daily/permanent killswitch must NOT
block EXIT — closing losing positions stops bleeding.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .brokers import Broker, OrderResult
from .portfolio import get_account_state
from .risk_guard import (
    check_max_trades_per_day,
    is_killswitch_active,
    is_permanent_killswitch_active,
    read_killswitch_reason,
    read_permanent_killswitch_reason,
)
from .storage import Storage


MANUAL_DISABLE_FILE = "executor.disabled"


@dataclass
class LiveExecResult:
    action: str
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trade_id: Optional[int] = None
    symbol: Optional[str] = None
    order: Optional[dict] = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ─── persist ────────────────────────────────────────────────────────────────

def _persist(result: LiveExecResult, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "live_executor.latest.json").write_text(
        json.dumps(result.to_dict(), indent=2, default=str)
    )
    ts = result.timestamp[:19].replace("T", " ")
    dry = " [DRY]" if result.dry_run else ""
    tid = f" #{result.trade_id}" if result.trade_id else ""
    sym = f" {result.symbol}" if result.symbol else ""
    line = f"[{ts} UTC] {result.action:<5}{dry}{tid}{sym} -- {result.reason}\n"
    with (data_dir / "live_executor.log").open("a") as f:
        f.write(line)


# ─── gate helpers ──────────────────────────────────────────────────────────

def _check_entry_gates(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    symbol: str,
    usdt_amount: float,
) -> Optional[str]:
    if (data_dir / MANUAL_DISABLE_FILE).exists():
        return f"manual disable file: {MANUAL_DISABLE_FILE}"
    if is_permanent_killswitch_active(data_dir):
        info = read_permanent_killswitch_reason(data_dir) or {}
        dd = info.get("drawdown_pct")
        return f"permanent_floor active: drawdown {dd}% (manual reset required)"
    if is_killswitch_active(data_dir):
        info = read_killswitch_reason(data_dir) or {}
        pct = info.get("daily_pnl_pct")
        return f"daily_loss_killswitch active: daily {pct}%"
    trade_cap = check_max_trades_per_day(storage, cfg)
    if trade_cap["exceeded"]:
        return f"max_trades_per_day cap: {trade_cap['today']}/{trade_cap['cap']}"
    state = get_account_state(storage, cfg)
    max_pos_n = state["equity"] * state["max_position_pct"] / 100.0
    max_gross_n = state["equity"] * state["max_gross_pct"] / 100.0
    if usdt_amount > max_pos_n:
        return f"per-position cap: {usdt_amount:.2f} > {max_pos_n:.2f}"
    new_gross = state["gross_notional"] + usdt_amount
    if new_gross > max_gross_n:
        return f"gross cap: {new_gross:.2f} > {max_gross_n:.2f}"
    if state["n_open"] + 1 > state["max_concurrent"]:
        return f"concurrent cap: {state['n_open']+1} > {state['max_concurrent']}"
    return None


def _check_exit_gates(data_dir: Path) -> Optional[str]:
    if (data_dir / MANUAL_DISABLE_FILE).exists():
        return f"manual disable file: {MANUAL_DISABLE_FILE}"
    return None


# ─── public ops ────────────────────────────────────────────────────────────

def execute_live_open(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    broker: Broker,
    symbol: str,
    usdt_amount: float,
    thesis: str = "",
    auto_protect: bool = False,
    tp_pct: Optional[float] = None,
    sl_pct: Optional[float] = None,
) -> LiveExecResult:
    """Open LONG spot position. usdt_amount = USDT budget to spend.

    If auto_protect=True and broker.is_live=True, immediately place OCO
    SL+TP after BUY fill. Default tp/sl pct from cfg.exits.
    """
    err = _check_entry_gates(storage, cfg, data_dir, symbol, usdt_amount)
    if err:
        r = LiveExecResult(
            action="SKIP", reason=err, symbol=symbol,
            dry_run=not broker.is_live,
        )
        _persist(r, data_dir)
        return r

    order = broker.place_market_buy(symbol, usdt_amount)
    if not order.success:
        r = LiveExecResult(
            action="ERROR", reason=f"broker BUY failed: {order.error}",
            symbol=symbol, order=_order_dict(order), dry_run=order.dry_run,
        )
        _persist(r, data_dir)
        return r

    pair = order.symbol
    full_thesis = (
        f"[LIVE_LLM{' DRY' if order.dry_run else ''}] {thesis} | "
        f"order_id={order.order_id} fee={order.fee_usdt:.4f}"
    )[:200]
    trade_id = storage.add_trade(
        pair=pair,
        direction="LONG",
        entry_price=order.avg_fill_price,
        size=order.filled_amount,
        thesis=full_thesis,
        signal_id=None,
    )

    reason = (
        f"BUY {pair} filled {order.filled_amount:.8g} @ "
        f"{order.avg_fill_price:.6g} = {order.notional_usdt:.4f} USDT"
    )

    # Auto-protect after fill (live broker only; dry-run can't place OCO)
    protection_info = None
    if auto_protect and broker.is_live and order.success:
        try:
            from .protection import place_protective_oco
            exits = cfg.get("exits") or {}
            tp = tp_pct if tp_pct is not None else float(exits.get("take_profit_pct", 5.0))
            sl = sl_pct if sl_pct is not None else float(exits.get("stop_loss_pct", 5.0))
            prot = place_protective_oco(
                broker=broker, trade_id=int(trade_id),
                symbol=pair, quantity=order.filled_amount,
                entry_price=order.avg_fill_price,
                tp_pct=tp, sl_pct=sl,
            )
            if prot.success:
                protection_info = {
                    "order_list_id": prot.order_list_id,
                    "tp_price": prot.tp_price,
                    "sl_stop": prot.sl_stop,
                    "sl_limit": prot.sl_limit,
                    "qty_protected": prot.quantity,
                }
                reason += f" | PROTECTED OCO {prot.order_list_id} TP=${prot.tp_price:.6g} SL=${prot.sl_stop:.6g}"
            else:
                reason += f" | auto-protect FAILED: {prot.error}"
        except Exception as e:
            reason += f" | auto-protect EXC: {type(e).__name__}: {e}"

    r = LiveExecResult(
        action="OPEN",
        reason=reason,
        trade_id=int(trade_id),
        symbol=pair,
        order=_order_dict(order),
        dry_run=order.dry_run,
    )
    if protection_info:
        # Attach protection summary to result.order dict
        if r.order is None:
            r.order = {}
        r.order["protection"] = protection_info
    _persist(r, data_dir)

    # Fire Telegram (silent no-op if not configured)
    try:
        from .telegram import notify_trade
        notify_trade(
            action="OPEN", symbol=pair,
            details={
                "trade_id": trade_id,
                "filled": f"{order.filled_amount:.8g}",
                "price": f"{order.avg_fill_price:.6g}",
                "notional": f"{order.notional_usdt:.4f} USDT",
                "thesis": thesis[:80] if thesis else "(none)",
                "protected": "YES" if protection_info else "NO",
            },
            urgent=True,
        )
    except Exception:
        pass

    return r


def execute_live_close(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    broker: Broker,
    trade_id: int,
) -> LiveExecResult:
    """Close LONG spot position by trade_id."""
    err = _check_exit_gates(data_dir)
    if err:
        r = LiveExecResult(
            action="SKIP", reason=err, trade_id=trade_id,
            dry_run=not broker.is_live,
        )
        _persist(r, data_dir)
        return r

    row = storage._conn.execute(
        "SELECT pair, direction, size, status FROM trade_journal WHERE id=?",
        [trade_id],
    ).fetchone()
    if row is None:
        r = LiveExecResult(
            action="SKIP", reason=f"trade #{trade_id} not found",
            trade_id=trade_id, dry_run=not broker.is_live,
        )
        _persist(r, data_dir)
        return r
    pair, direction, size, status = row
    if status != "OPEN":
        r = LiveExecResult(
            action="SKIP", reason=f"trade #{trade_id} status={status}",
            trade_id=trade_id, symbol=str(pair),
            dry_run=not broker.is_live,
        )
        _persist(r, data_dir)
        return r
    if direction != "LONG":
        r = LiveExecResult(
            action="SKIP",
            reason=f"trade #{trade_id} direction={direction} (only LONG supported)",
            trade_id=trade_id, symbol=str(pair),
            dry_run=not broker.is_live,
        )
        _persist(r, data_dir)
        return r

    order = broker.place_market_sell(str(pair), float(size))
    if not order.success:
        r = LiveExecResult(
            action="ERROR", reason=f"broker SELL failed: {order.error}",
            trade_id=trade_id, symbol=str(pair),
            order=_order_dict(order), dry_run=order.dry_run,
        )
        _persist(r, data_dir)
        return r

    notes = (
        f"[LIVE_CLOSE{' DRY' if order.dry_run else ''}] "
        f"order_id={order.order_id} fee={order.fee_usdt:.4f}"
    )[:200]
    try:
        result = storage.close_trade(
            trade_id=trade_id,
            exit_price=order.avg_fill_price,
            notes=notes,
        )
    except ValueError as e:
        r = LiveExecResult(
            action="ERROR", reason=f"storage.close_trade error: {e}",
            trade_id=trade_id, symbol=str(pair),
            order=_order_dict(order), dry_run=order.dry_run,
        )
        _persist(r, data_dir)
        return r

    pnl = result.get("pnl")
    pnl_s = f" pnl={pnl:+.4f}" if pnl is not None else " pnl=n/a"
    r = LiveExecResult(
        action="CLOSE",
        reason=(
            f"SELL {pair} {order.filled_amount:.8g} @ "
            f"{order.avg_fill_price:.6g}{pnl_s}"
        ),
        trade_id=trade_id,
        symbol=str(pair),
        order=_order_dict(order),
        dry_run=order.dry_run,
    )
    _persist(r, data_dir)
    return r


def execute_panic_close_all(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    broker: Broker,
) -> list[LiveExecResult]:
    """Emergency: close ALL open LONG positions immediately."""
    df = storage.get_trades(status="OPEN")
    results: list[LiveExecResult] = []
    if df.empty:
        return results
    for _, row in df.iterrows():
        if row["direction"] != "LONG":
            continue
        r = execute_live_close(
            storage, cfg, data_dir, broker, int(row["id"]),
        )
        results.append(r)
    return results


def _order_dict(o: OrderResult) -> dict:
    """asdict but drop the raw ccxt blob (too large, not needed in log)."""
    d = asdict(o)
    d.pop("raw", None)
    if "ts" in d and hasattr(d["ts"], "isoformat"):
        d["ts"] = d["ts"].isoformat()
    return d
