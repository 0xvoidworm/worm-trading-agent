"""Protective OCO orders for spot positions — exchange-side risk management.

Phase 2.5+ defensive layer: place Binance OCO (One-Cancels-Other) sell
that combines:
  - LIMIT_MAKER above entry (take-profit, sells at +TP%)
  - STOP_LOSS_LIMIT below entry (stops out at -SL%)

Either fill cancels the other. Exchange enforces — works even kalau
daemon/monitor/dashboard mati semua. Asset (base coin) locked dalam OCO
selama orders aktif → tidak bisa manual sell tanpa cancel dulu.

State persisted di data/protection.json (keyed by trade_id) — bypass DB
lock contention dengan daemon. Schema:
{
    "<trade_id>": {
        "symbol": "NEAR/USDT",
        "quantity": 2.9,
        "tp_price": 2.495,
        "sl_stop": 2.260,
        "sl_limit": 2.250,
        "order_list_id": 12345,
        "list_client_order_id": "claude_protect_6",
        "placed_at": "2026-05-24T03:30:00Z",
        "status": "ACTIVE" / "CANCELLED" / "FILLED"
    }
}

Min notional applies to EACH leg — 5 USDT on Binance. Validates pre-flight.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .brokers import BinanceSpotBroker


PROTECTION_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "protection.json"


@dataclass
class ProtectionResult:
    success: bool
    symbol: str
    trade_id: int
    quantity: float
    tp_price: float
    sl_stop: float
    sl_limit: float
    order_list_id: Optional[int] = None
    list_client_order_id: Optional[str] = None
    error: Optional[str] = None
    raw: Optional[dict] = None


def _ccxt_symbol_to_binance(symbol: str) -> str:
    """NEAR/USDT → NEARUSDT (Binance raw symbol format for native API)."""
    return symbol.replace("/", "")


def _load_state() -> dict:
    if not PROTECTION_STATE_FILE.exists():
        return {}
    try:
        return json.loads(PROTECTION_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    PROTECTION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROTECTION_STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _round_to_tick(price: float, tick_size: float) -> float:
    """Round price to exchange tick size (avoids PRICE_FILTER reject)."""
    if tick_size <= 0:
        return price
    return round(round(price / tick_size) * tick_size, 12)


def _round_to_step(qty: float, step_size: float) -> float:
    """Round quantity DOWN to exchange step size (avoids LOT_SIZE reject + over-sell).

    Float-precision safe: 2.9 / 0.1 = 28.999... → int floors to 28 = 2.8 (wrong).
    Add small epsilon before floor so 28.999... → 29 = 2.9 (correct).
    """
    if step_size <= 0:
        return qty
    import math
    n_steps = math.floor(qty / step_size + 1e-9)
    return round(n_steps * step_size, 12)


def place_protective_oco(
    broker: BinanceSpotBroker,
    trade_id: int,
    symbol: str,
    quantity: float,
    entry_price: float,
    tp_pct: float = 5.0,
    sl_pct: float = 5.0,
) -> ProtectionResult:
    """Place Binance OCO sell to protect a LONG spot position.

    Args:
        trade_id: trade_journal id to key state
        symbol: e.g. "NEAR/USDT"
        quantity: base coin amount to protect (must own this much in spot)
        entry_price: avg entry price (used to compute TP/SL prices)
        tp_pct: take-profit % above entry (default 5)
        sl_pct: stop-loss % below entry (default 5)

    Returns:
        ProtectionResult with order_list_id if successful.
    """
    broker._ensure_markets()
    ex = broker._ex
    mk = ex.market(symbol)
    tick = float(mk.get("precision", {}).get("price") or 0.01)
    step = float(mk.get("precision", {}).get("amount") or 0.001)
    min_notional = float((mk.get("limits", {}).get("cost") or {}).get("min", 5))

    # Round quantity DOWN to step (own quantity may be slightly less due to dust)
    qty = _round_to_step(quantity, step)
    tp_price = _round_to_tick(entry_price * (1 + tp_pct / 100), tick)
    # SL trigger slightly above limit price (so order can fill at limit)
    sl_stop = _round_to_tick(entry_price * (1 - sl_pct / 100), tick)
    sl_limit = _round_to_tick(entry_price * (1 - (sl_pct + 0.1) / 100), tick)

    # Pre-flight: both legs must satisfy min_notional
    tp_notional = qty * tp_price
    sl_notional = qty * sl_limit
    if tp_notional < min_notional:
        return ProtectionResult(
            success=False, symbol=symbol, trade_id=trade_id,
            quantity=qty, tp_price=tp_price, sl_stop=sl_stop, sl_limit=sl_limit,
            error=f"TP notional {tp_notional:.4f} < min {min_notional}",
        )
    if sl_notional < min_notional:
        return ProtectionResult(
            success=False, symbol=symbol, trade_id=trade_id,
            quantity=qty, tp_price=tp_price, sl_stop=sl_stop, sl_limit=sl_limit,
            error=f"SL notional {sl_notional:.4f} < min {min_notional}",
        )
    if qty <= 0:
        return ProtectionResult(
            success=False, symbol=symbol, trade_id=trade_id,
            quantity=0, tp_price=tp_price, sl_stop=sl_stop, sl_limit=sl_limit,
            error="quantity rounded to 0 after step alignment",
        )

    list_client_order_id = f"claude_protect_{trade_id}_{int(datetime.now(timezone.utc).timestamp())}"

    params = {
        "symbol": _ccxt_symbol_to_binance(symbol),
        "side": "SELL",
        "quantity": f"{qty:.8f}".rstrip("0").rstrip("."),
        "aboveType": "LIMIT_MAKER",
        "abovePrice": f"{tp_price:.8f}".rstrip("0").rstrip("."),
        "belowType": "STOP_LOSS_LIMIT",
        "belowStopPrice": f"{sl_stop:.8f}".rstrip("0").rstrip("."),
        "belowPrice": f"{sl_limit:.8f}".rstrip("0").rstrip("."),
        "belowTimeInForce": "GTC",
        "listClientOrderId": list_client_order_id,
    }

    try:
        resp = ex.private_post_orderlist_oco(params)
    except Exception as e:
        return ProtectionResult(
            success=False, symbol=symbol, trade_id=trade_id,
            quantity=qty, tp_price=tp_price, sl_stop=sl_stop, sl_limit=sl_limit,
            error=f"{type(e).__name__}: {str(e)[:300]}",
        )

    order_list_id = resp.get("orderListId") or resp.get("listId")
    result = ProtectionResult(
        success=True, symbol=symbol, trade_id=trade_id,
        quantity=qty, tp_price=tp_price, sl_stop=sl_stop, sl_limit=sl_limit,
        order_list_id=int(order_list_id) if order_list_id else None,
        list_client_order_id=list_client_order_id,
        raw=resp,
    )

    # Persist to state file
    state = _load_state()
    state[str(trade_id)] = {
        "symbol": symbol,
        "quantity": qty,
        "tp_price": tp_price,
        "sl_stop": sl_stop,
        "sl_limit": sl_limit,
        "order_list_id": result.order_list_id,
        "list_client_order_id": list_client_order_id,
        "placed_at": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
    }
    _save_state(state)
    return result


def cancel_protection(
    broker: BinanceSpotBroker,
    trade_id: int,
) -> dict:
    """Cancel OCO list for trade_id. Returns dict with cancel result + state."""
    state = _load_state()
    record = state.get(str(trade_id))
    if not record:
        return {"success": False, "error": f"no protection record for trade #{trade_id}"}
    if record.get("status") != "ACTIVE":
        return {"success": False, "error": f"protection status={record.get('status')} (already non-active)"}

    ex = broker._ex
    broker._ensure_markets()
    try:
        # Cancel via orderListId
        params = {
            "symbol": _ccxt_symbol_to_binance(record["symbol"]),
            "orderListId": record["order_list_id"],
        }
        resp = ex.private_delete_orderlist(params)
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}

    record["status"] = "CANCELLED"
    record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
    state[str(trade_id)] = record
    _save_state(state)
    return {"success": True, "trade_id": trade_id, "record": record, "raw": resp}


def get_protection_status(
    broker: BinanceSpotBroker,
    trade_id: Optional[int] = None,
) -> dict:
    """Return state for one trade_id, or all if trade_id is None.

    Also queries Binance to refresh ACTIVE → FILLED/CANCELLED status
    by inspecting open orders.
    """
    state = _load_state()
    if not state:
        return {"records": [], "open_orders": []}

    ex = broker._ex
    broker._ensure_markets()
    try:
        # Single fetch_open_orders for all symbols where we have protection
        symbols_to_check = set()
        for k, v in state.items():
            if v.get("status") == "ACTIVE":
                symbols_to_check.add(v["symbol"])
        open_orders_by_sym: dict[str, list] = {}
        for sym in symbols_to_check:
            try:
                open_orders_by_sym[sym] = ex.fetch_open_orders(sym)
            except Exception as e:
                open_orders_by_sym[sym] = [{"error": str(e)}]
    except Exception as e:
        open_orders_by_sym = {"error": str(e)}

    out_records = []
    target = [trade_id] if trade_id is not None else list(state.keys())
    for tid_key in target:
        tid_str = str(tid_key)
        rec = state.get(tid_str)
        if not rec:
            continue
        # Cross-reference: still in open_orders?
        sym = rec["symbol"]
        active_open = []
        if sym in open_orders_by_sym and isinstance(open_orders_by_sym[sym], list):
            for o in open_orders_by_sym[sym]:
                if isinstance(o, dict) and not o.get("error"):
                    # Match by orderListId (Binance returns int or string)
                    raw = o.get("info", {}) or {}
                    olid_raw = raw.get("orderListId")
                    olid_rec = rec.get("order_list_id")
                    if olid_raw is None or olid_rec is None:
                        continue
                    try:
                        if int(olid_raw) == int(olid_rec):
                            active_open.append({
                                "order_id": o.get("id"),
                                "type": o.get("type"),
                                "side": o.get("side"),
                                "price": o.get("price"),
                                "stop_price": raw.get("stopPrice"),
                                "amount": o.get("amount"),
                                "status": o.get("status"),
                            })
                    except (TypeError, ValueError):
                        continue

        rec_out = dict(rec)
        rec_out["trade_id"] = tid_str
        rec_out["binance_open_legs"] = active_open
        # If status ACTIVE but no open orders → either filled or cancelled externally
        if rec.get("status") == "ACTIVE" and not active_open:
            rec_out["status_inferred"] = "FILLED_OR_CANCELLED_EXTERNAL"
        out_records.append(rec_out)

    return {"records": out_records}
