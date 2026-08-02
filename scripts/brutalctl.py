#!/usr/bin/env python3
"""brutalctl - autonomous BRUTAL1000 live-campaign controller CLI.

Once armed, `brutalctl run` executes the whole loop by itself: pull fresh HOT
CEX tickets, enter one bounded micro position, protect it with exchange SL/TP,
let the shared paper exit policy close it, reconcile against the exchange, and
repeat -- until a pre-declared end condition (max trades / max days / loss
budget) or a structural fault. No per-tick human approval.

Safety model (fail-closed):
  * DRY-RUN is the default. A real order is only possible with `--live` AND a
    campaign that was armed with `--confirm "CONFIRM AUTONOMOUS BRUTAL CAMPAIGN"`.
  * Live entries additionally require the broker's own hardening preflight
    (IP whitelist, least-privilege key) via assert_live_entry_ready.
  * Every mutation is audited; secrets are never printed.

Subcommands: plan | arm | status | tick | run | report | stop
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from data_pipe import campaign_engine as ce  # noqa: E402

HOME = Path.home()
ACTION_PLAN = HOME / "brutal1000_scanner" / "data" / "action_plan.json"
WORM = HOME / ".local" / "bin" / "worm"
CAMPAIGN_SIDECAR = (
    HOME / "tradingview_bloomberg_terminal" / "brutal1000_campaign.js"
)
ARM_PHRASE = "CONFIRM AUTONOMOUS BRUTAL CAMPAIGN"
STOP_PHRASE = "CONFIRM STOP AUTONOMOUS BRUTAL CAMPAIGN"
MAX_CONSECUTIVE_RECONCILE_FAILURES = 3
TERMINAL_ENTRY_FAILURES = {
    "BRACKET_VERIFY_FAILED_CLOSED_FLAT",
    "SL_FAILED_CLOSED_FLAT",
    "TP_FAILED_CLOSED_FLAT",
}


def _load_action_plan() -> dict:
    if not ACTION_PLAN.exists():
        return {}
    try:
        return json.loads(ACTION_PLAN.read_text())
    except Exception:
        return {}


def candidates_from_action_plan() -> list[dict]:
    """Normalize current HOT CEX micro tickets into engine candidate dicts.

    The scanner ticket carries entry/stop/tp refs and a score but not a
    signal_id/detected_at pair; the open-paper block does. We join on instrument
    so each candidate has a dedup key + detection time for freshness gating.
    """
    ap = _load_action_plan()
    if ap.get("mode") not in (
        "CONDITIONAL_MICRO_ONLY",
        "MICRO_LIVE_REVIEW",
        "CEX_REVIEW_ONLY",
    ):
        return []
    hot = (ap.get("current_hot") or {}).get("cex_micro_tickets") or []
    open_valid = (ap.get("open_paper") or {}).get("valid_for_live_review") or []
    meta = {str(r.get("instrument")): r for r in open_valid}
    out = []
    for t in hot:
        inst = str(t.get("instrument"))
        m = meta.get(inst, {})
        # A live campaign only consumes canonical paper signal IDs. Inventing a
        # fallback ID from the action timestamp would allow repeated entry into
        # the same ongoing candidate every scanner cycle.
        if not m.get("signal_id") or not m.get("detected_at"):
            continue
        if str(m.get("current_gate") or m.get("gate")) != "HOT":
            continue
        if str(m.get("side", "")).upper() != str(t.get("side", "")).upper():
            continue
        out.append(
            {
                "signal_id": m["signal_id"],
                "detected_at": m["detected_at"],
                "lane": "CEX_PERP",
                "instrument": inst,
                "side": str(t.get("side", "")).upper(),
                "score": t.get("score") or 0.0,
                "entry_ref": t.get("entry_ref"),
                "stop_ref": t.get("stop_ref"),
                "tp2_ref": t.get("tp2_ref"),
            }
        )
    return out


def live_bankroll() -> float:
    proc = subprocess.run(
        [str(WORM), "bal", "--json"], capture_output=True, text=True, timeout=30, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bankroll query failed: {proc.stderr.strip()[:160]}")
    return float((json.loads(proc.stdout).get("futures") or {}).get("total") or 0.0)


def _sym_ccxt(instrument: str) -> str:
    base = instrument.upper().replace("USDT", "")
    return f"{base}/USDT:USDT"


def _cfg_from_args(args) -> ce.CampaignConfig:
    return ce.CampaignConfig(
        campaign_id=args.id,
        risk_pct=getattr(args, "risk", 0.25),
        leverage=getattr(args, "lev", 2),
        max_entries_per_day=getattr(args, "max_entries_per_day", 4),
        max_closed_trades=getattr(args, "max_trades", 50),
        max_days=getattr(args, "max_days", 21.0),
        loss_budget_r=getattr(args, "loss_budget_r", 20.0),
        freshness_sec=getattr(args, "freshness_sec", 600.0),
        cooldown_sec=getattr(args, "cooldown_sec", 1800.0),
        entry_min_r=getattr(args, "entry_min_r", -0.10),
        entry_max_r=getattr(args, "entry_max_r", 0.25),
        live=bool(getattr(args, "live", False)),
    )


def _controller(args) -> ce.CampaignController:
    cfg_file = ce.DEFAULT_STATE_DIR / f"{args.id}.config.json"
    if not cfg_file.exists():
        raise RuntimeError(
            f"campaign {args.id!r} is not armed; run `brutalctl arm` first"
        )
    cfg = ce.CampaignConfig.from_dict(json.loads(cfg_file.read_text()))
    requested_live = getattr(args, "live", None)
    if requested_live is not None and bool(requested_live) != bool(cfg.live):
        raise RuntimeError(
            f"campaign mode mismatch: armed live={cfg.live}, "
            f"runtime requested live={bool(requested_live)}"
        )
    return ce.CampaignController(cfg)


def _campaign_runtime_health(ctrl: ce.CampaignController) -> dict:
    result = ctrl.state.last_cycle_result or {}
    error = (
        result.get("reconcile_error")
        or result.get("entry_error")
        or result.get("halted")
    )
    age_seconds = None
    if ctrl.state.last_cycle_at:
        try:
            age_seconds = max(
                0,
                round(
                    (
                        ce.now_utc()
                        - ce.parse_iso(ctrl.state.last_cycle_at)
                    ).total_seconds()
                ),
            )
        except Exception:
            age_seconds = None
    healthy = (
        ctrl.state.status == "RUNNING"
        and not error
        and age_seconds is not None
        and age_seconds <= 180
    )
    return {
        "status": "healthy" if healthy else "degraded",
        "healthy": healthy,
        "exchange_reconciled": not bool(result.get("reconcile_error")),
        "last_cycle_age_seconds": age_seconds,
        "last_error": str(error)[:240] if error else None,
    }


def export_campaign_snapshot(ctrl: ce.CampaignController) -> None:
    """Publish a secret-free read-only campaign sidecar for Project Umbrela."""
    payload = {
        "schema": "brutal1000_campaign.v1",
        "generated_at_utc": ce.now_utc().isoformat(timespec="seconds"),
        "config": ctrl.cfg.to_dict(),
        "summary": ctrl.summary(),
        "health": _campaign_runtime_health(ctrl),
        "open_positions": ctrl.state.open_positions,
        "closed_records": ctrl.state.closed_records[-25:],
        "last_cycle_at": ctrl.state.last_cycle_at,
        "last_cycle_result": ctrl.state.last_cycle_result,
        "started_at": ctrl.state.started_at,
        "stop_reason": ctrl.state.stop_reason,
        "halt_reason": ctrl.state.halt_reason,
    }
    CAMPAIGN_SIDECAR.parent.mkdir(parents=True, exist_ok=True)
    tmp = CAMPAIGN_SIDECAR.with_suffix(".tmp")
    tmp.write_text(
        "// Auto-generated by brutalctl. No secrets or credentials.\n"
        "window.BRUTAL1000_CAMPAIGN = "
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        + ";\n"
    )
    tmp.replace(CAMPAIGN_SIDECAR)


def cmd_plan(args) -> int:
    cfg = _cfg_from_args(args)
    cfg.validate()
    print(json.dumps({"plan": cfg.to_dict(), "candidates": candidates_from_action_plan()}, indent=2))
    return 0


def cmd_arm(args) -> int:
    if args.confirm != ARM_PHRASE:
        print(f"arm: BLOCKED. Re-run with --confirm \"{ARM_PHRASE}\"", file=sys.stderr)
        return 3
    cfg = _cfg_from_args(args)
    cfg.validate()
    if cfg.live:
        from data_pipe.futures_broker import BinanceFuturesBroker

        preflight_broker = BinanceFuturesBroker()
        preflight_broker.assert_live_entry_ready(cfg.risk_pct)
        if preflight_broker.fetch_positions():
            raise RuntimeError(
                "cannot arm live campaign while futures positions are open"
            )
        open_orders = preflight_broker.fetch_all_open_orders()
        if open_orders["regular"] or open_orders["conditional"]:
            raise RuntimeError(
                "cannot arm live campaign while futures open orders exist"
            )
    ce.DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    config_file = ce.DEFAULT_STATE_DIR / f"{args.id}.config.json"
    state_file = ce.DEFAULT_STATE_DIR / f"{args.id}.state.json"
    if config_file.exists() or state_file.exists():
        print(
            f"arm: BLOCKED. campaign {args.id!r} already exists; "
            "use a new --id or archive the completed campaign.",
            file=sys.stderr,
        )
        return 3
    tmp = config_file.with_suffix(".tmp")
    with tmp.open("w") as fh:
        fh.write(json.dumps(cfg.to_dict(), indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(config_file)
    config_file.chmod(0o600)
    ctrl = ce.CampaignController(cfg)
    ctrl.save()
    ctrl.audit("ARM", live=cfg.live, config=cfg.to_dict())
    export_campaign_snapshot(ctrl)
    print(json.dumps({"armed": True, "live": cfg.live, "summary": ctrl.summary()}, indent=2))
    return 0


def cmd_status(args) -> int:
    ctrl = _controller(args)
    print(json.dumps({"summary": ctrl.summary(), "open_positions": ctrl.state.open_positions}, indent=2))
    return 0


def _make_broker(cfg: ce.CampaignConfig):
    from data_pipe.futures_broker import make_futures_broker

    return make_futures_broker(live=cfg.live)


def _client_id(campaign_id: str, signal_id: str, leg: str) -> str:
    """Binance-safe deterministic client order ID (<=36 chars)."""
    digest = hashlib.sha256(f"{campaign_id}|{signal_id}".encode()).hexdigest()[:20]
    return f"brt{digest}{leg}"[:36]


def _consume_terminal_entry_failure(
    ctrl: ce.CampaignController,
    signal_id: str,
    outcome: str,
) -> None:
    """Prevent a failed, closed-flat live intent from retrying every cycle."""
    if outcome not in TERMINAL_ENTRY_FAILURES:
        return
    if signal_id not in ctrl.state.executed_signal_ids:
        ctrl.state.executed_signal_ids.append(signal_id)
    ctrl.audit("ENTRY_SIGNAL_CONSUMED", signal_id=signal_id, outcome=outcome)
    ctrl.save()


def _exchange_position_map(raw_positions) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in raw_positions:
        if hasattr(p, "__dict__"):
            d = dict(p.__dict__)
        else:
            d = dict(p)
        instrument = ce.normalize_instrument(d.get("instrument") or d.get("symbol"))
        if instrument:
            d["instrument"] = instrument
            out[instrument] = d
    return out


def _find_position(raw_positions, instrument: str) -> dict | None:
    return _exchange_position_map(raw_positions).get(
        ce.normalize_instrument(instrument)
    )


def _has_stop_and_tp(triggers: list[dict]) -> bool:
    has_stop = False
    has_tp = False
    for t in triggers:
        info = t.get("info") or {}
        kind = str(
            t.get("type")
            or info.get("type")
            or info.get("origType")
            or info.get("orderType")
            or ""
        ).upper()
        has_stop = has_stop or ("STOP" in kind and "TAKE_PROFIT" not in kind)
        has_tp = has_tp or ("TAKE_PROFIT" in kind)
    return has_stop and has_tp


def _trigger_client_id(trigger: dict) -> str:
    info = trigger.get("info") or {}
    return str(
        trigger.get("clientOrderId")
        or info.get("clientAlgoId")
        or info.get("clientOrderId")
        or ""
    )


def _find_trigger(
    triggers: list[dict],
    client_order_id: str,
    kind: str,
) -> dict | None:
    kind = kind.upper()
    for trigger in triggers:
        if client_order_id and _trigger_client_id(trigger) == client_order_id:
            return trigger
    for trigger in triggers:
        info = trigger.get("info") or {}
        order_type = str(
            trigger.get("type")
            or info.get("orderType")
            or info.get("origType")
            or ""
        ).upper()
        if kind == "STOP" and "STOP" in order_type and "TAKE_PROFIT" not in order_type:
            return trigger
        if kind == "TP" and "TAKE_PROFIT" in order_type:
            return trigger
    return None


def _order_status(order: dict | None) -> str:
    if not order:
        return ""
    info = order.get("info") or {}
    return str(
        order.get("status")
        or info.get("orderStatus")
        or info.get("status")
        or ""
    ).upper()


def _order_average(order: dict | None, fallback: float) -> float:
    order = order or {}
    info = order.get("info") or {}
    for value in (
        order.get("average"),
        info.get("avgPrice"),
        info.get("actualPrice"),
        order.get("price"),
    ):
        try:
            number = float(value or 0.0)
            if number > 0:
                return number
        except Exception:
            pass
    return float(fallback)


def _order_fee_usd(order: dict | None) -> float:
    order = order or {}
    fee = order.get("fee") or {}
    try:
        return float(fee.get("cost") or 0.0)
    except Exception:
        return 0.0


def _order_filled_amount(order: dict | None, fallback: float) -> float:
    order = order or {}
    info = order.get("info") or {}
    for value in (
        order.get("filled"),
        info.get("executedQty"),
        info.get("actualQty"),
        order.get("amount"),
    ):
        try:
            amount = float(value or 0.0)
            if amount > 0:
                return amount
        except Exception:
            pass
    return float(fallback)


def _status_is_filled(status: str) -> bool:
    status = str(status or "").upper()
    return (
        status in ("CLOSED", "FILLED", "FINISHED")
        or status.endswith("_FILLED")
        or status.endswith("_FINISHED")
    )


def _trigger_exit_record(broker, pos: dict, sym: str, fallback_price: float):
    """Return (exit_r, status, price, fee) from the actual executed trigger."""
    candidates = []
    stop_ids = [
        str(pos.get("sl_client_id") or ""),
        *[
            str(value)
            for value in (pos.get("retired_stop_client_ids") or [])
            if value
        ],
    ]
    lookups = [
        ("STOPPED", client_id) for client_id in stop_ids if client_id
    ]
    tp_client_id = str(pos.get("tp_client_id") or "")
    if tp_client_id:
        lookups.append(("TP2", tp_client_id))
    for label, client_id in lookups:
        if not client_id:
            continue
        try:
            order = broker.fetch_trigger_execution(sym, client_id)
        except Exception:
            continue
        status = _order_status(order)
        if _status_is_filled(status):
            price = _order_average(order, fallback_price)
            actual_r = ce.compute_r(
                pos["side"], pos["entry"], pos["stop"], price
            )
            candidates.append(
                (actual_r, label, price, _order_fee_usd(order))
            )
    if not candidates:
        return None
    # At most one bracket leg should fill. If Binance reports both, choose the
    # one closest to the current/fallback mark and let audit expose the anomaly.
    return min(candidates, key=lambda row: abs(row[2] - fallback_price))


def _replace_protective_stop(broker, pos: dict, sym: str, stop_price: float) -> bool:
    current = float(pos.get("protective_stop") or pos["stop"])
    dist = abs(float(pos["entry"]) - float(pos["stop"]))
    tolerance = max(1e-12, dist * 1e-6)
    side = str(pos["side"]).upper()
    improves = (
        stop_price > current + tolerance
        if side == "LONG"
        else stop_price < current - tolerance
    )
    if not improves:
        return True

    # Place the tighter stop first. Only after Binance accepts it do we cancel
    # the older stop, so the position is never intentionally naked.
    close_side = "sell" if side == "LONG" else "buy"
    new_client_id = _client_id(
        str(pos.get("campaign_id") or "campaign"),
        f"{pos['signal_id']}|{stop_price:.12g}",
        "s",
    )
    new_stop = broker.place_reduce_trigger(
        sym,
        close_side,
        float(pos["qty"]),
        float(stop_price),
        "STOP",
        client_order_id=new_client_id,
    )
    if not new_stop.success:
        return False

    old_id = str(pos.get("sl_order_id") or "")
    old_client_id = str(pos.get("sl_client_id") or "")
    if old_id:
        if not broker.cancel_order(sym, old_id, conditional=True):
            # Both stops are protective; keep the new one in state and let
            # reconciliation surface any stale duplicate rather than go naked.
            pos["sl_order_id"] = new_stop.order_id
            pos["sl_client_id"] = new_client_id
            pos["protective_stop"] = float(stop_price)
            return True
    pos["sl_order_id"] = new_stop.order_id
    pos["sl_client_id"] = new_client_id
    pos["protective_stop"] = float(stop_price)
    retired = pos.setdefault("retired_stop_client_ids", [])
    if old_client_id and old_client_id not in retired:
        retired.append(old_client_id)
    return True


def _resolve_pending_entry(ctrl: ce.CampaignController, broker) -> dict | None:
    """Resolve a pre-existing entry intent before any new order is considered."""
    intent = ctrl.state.pending_entry
    if not intent:
        return None
    sym = str(intent["symbol"])
    client_id = str(intent["client_order_id"])
    try:
        found = broker.fetch_order_by_client_id(sym, client_id)
    except Exception as exc:
        ctrl.halt(f"PENDING_ENTRY_RECONCILE_FAILED:{type(exc).__name__}")
        ctrl.save()
        return {"halted": ctrl.state.halt_reason}
    if found is None:
        ctrl.halt(f"PENDING_ENTRY_NOT_FOUND:{client_id}")
        ctrl.save()
        return {"halted": ctrl.state.halt_reason}
    if not _status_is_filled(_order_status(found)):
        ctrl.halt(
            f"PENDING_ENTRY_NOT_FILLED:{client_id}:{_order_status(found)}"
        )
        ctrl.save()
        return {"halted": ctrl.state.halt_reason}

    instrument = str(intent["instrument"])
    exchange_pos = _find_position(
        broker.fetch_positions(sym), instrument,
    )
    if exchange_pos is None:
        ctrl.halt(f"PENDING_ENTRY_FILLED_BUT_FLAT:{client_id}")
        ctrl.save()
        return {"halted": ctrl.state.halt_reason}

    side = str(intent["side"]).upper()
    qty = float(
        exchange_pos.get("contracts")
        or _order_filled_amount(found, intent["qty"])
    )
    fill_entry = _order_average(
        found,
        float(exchange_pos.get("entry_price") or intent["entry_ref"]),
    )
    actual_stop, actual_tp = ce.targets_from_entry(
        side, fill_entry, float(intent["stop_ref"]),
    )
    close_side = "sell" if side == "LONG" else "buy"
    sl_client_id = str(intent["sl_client_id"])
    tp_client_id = str(intent["tp_client_id"])
    triggers = broker.fetch_trigger_orders(sym)
    sl_row = _find_trigger(triggers, sl_client_id, "STOP")
    tp_row = _find_trigger(triggers, tp_client_id, "TP")
    if sl_row is None:
        sl = broker.place_reduce_trigger(
            sym, close_side, qty, actual_stop, "STOP",
            client_order_id=sl_client_id,
        )
        if not sl.success:
            ctrl.halt(f"PENDING_ENTRY_SL_RECOVERY_FAILED:{instrument}")
            ctrl.save()
            return {"halted": ctrl.state.halt_reason}
        sl_row = {"id": sl.order_id, "clientOrderId": sl_client_id}
    if tp_row is None:
        tp = broker.place_reduce_trigger(
            sym, close_side, qty, actual_tp, "TP",
            client_order_id=tp_client_id,
        )
        if not tp.success:
            close = broker.place_market_order(
                sym,
                close_side,
                qty,
                reduce_only=True,
                client_order_id=_client_id(
                    ctrl.cfg.campaign_id, intent["signal_id"], "g",
                ),
            )
            broker.cancel_all_orders(sym)
            if not close.success:
                ctrl.halt(f"PENDING_ENTRY_TP_RECOVERY_NAKED:{instrument}")
                ctrl.save()
                return {"halted": ctrl.state.halt_reason}
            ctrl.clear_pending_entry(
                "PENDING_ENTRY_TP_RECOVERY_CLOSED_FLAT",
                close_success=True,
            )
            return {"pending_entry": "RECOVERY_CLOSED_FLAT"}
        tp_row = {"id": tp.order_id, "clientOrderId": tp_client_id}
    live_triggers = broker.fetch_trigger_orders(sym)
    if not _has_stop_and_tp(live_triggers):
        ctrl.halt(f"PENDING_ENTRY_BRACKET_VERIFY_FAILED:{instrument}")
        ctrl.save()
        return {"halted": ctrl.state.halt_reason}

    pos = ce.Position(
        signal_id=str(intent["signal_id"]),
        instrument=instrument,
        side=side,
        entry=fill_entry,
        stop=actual_stop,
        tp=actual_tp,
        qty=qty,
        opened_at=str(
            intent.get("submitted_at")
            or ce.now_utc().isoformat(timespec="seconds")
        ),
        campaign_id=ctrl.cfg.campaign_id,
        risk_usd=abs(fill_entry - actual_stop) * qty,
        entry_fee_usd=_order_fee_usd(found),
        protective_stop=actual_stop,
        entry_order_id=str(found.get("id") or ""),
        entry_client_id=client_id,
        sl_order_id=str(sl_row.get("id") or ""),
        sl_client_id=sl_client_id,
        tp_order_id=str(tp_row.get("id") or ""),
        tp_client_id=tp_client_id,
    )
    ctrl.record_entry(pos)
    ctrl.save()
    return {"pending_entry": "ADOPTED", "instrument": instrument}


def _tick_once(ctrl: ce.CampaignController) -> dict:
    """One autonomous cycle: reconcile -> exits -> maybe entry. Returns a report."""
    with ctrl.cycle_lock():
        return _tick_once_locked(ctrl)


def _tick_once_locked(ctrl: ce.CampaignController) -> dict:
    """Locked implementation for one autonomous cycle."""
    cfg = ctrl.cfg
    report: dict = {"actions": []}
    broker = _make_broker(cfg)

    previous_result = ctrl.state.last_cycle_result or {}
    try:
        reconcile_failures = int(
            previous_result.get("consecutive_reconcile_failures") or 0
        )
    except (TypeError, ValueError):
        reconcile_failures = 0

    if cfg.live:
        pending = _resolve_pending_entry(ctrl, broker)
        if pending:
            report.update(pending)
            if "halted" in pending or pending.get("pending_entry"):
                return _finish_cycle(ctrl, report)

    # 1. reconcile: exchange truth vs campaign intent (structural fault -> halt)
    ex_positions_raw = []
    try:
        ex_positions_raw = broker.fetch_positions()
        ex_positions = list(_exchange_position_map(ex_positions_raw).values())
        trig_by_sym: dict[str, list] = {}
        for pos in ctrl.state.open_positions:
            sym = _sym_ccxt(pos["instrument"])
            trig_by_sym[pos["instrument"]] = broker.fetch_trigger_orders(sym)
        faults = (
            ce.structural_faults(ctrl.state, ex_positions, trig_by_sym)
            if cfg.live
            else []
        )
    except Exception as e:  # exchange/auth failure: do not enter, keep protection
        reconcile_failures += 1
        report["reconcile_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        report["consecutive_reconcile_failures"] = reconcile_failures
        if (
            cfg.live
            and reconcile_failures >= MAX_CONSECUTIVE_RECONCILE_FAILURES
        ):
            ctrl.halt(f"RECONCILE_UNAVAILABLE:{reconcile_failures}")
            ctrl.save()
            report["halted"] = [ctrl.state.halt_reason]
        return _finish_cycle(ctrl, report)
    if faults:
        ctrl.halt(";".join(faults))
        ctrl.save()
        report["halted"] = faults
        return _finish_cycle(ctrl, report)

    # 2. manage exits for every open position via shared policy
    for pos in list(ctrl.state.open_positions):
        sym = _sym_ccxt(pos["instrument"])
        try:
            price = broker.fetch_mark_price(sym)
        except Exception as e:
            report["actions"].append({"instrument": pos["instrument"], "mark_error": str(e)[:120]})
            continue
        marked = ce.mark_position(pos, price)
        pos["current_r"] = marked["current_r"]
        pos["peak_r"] = marked["peak_r"]
        decision = ce.exit_decision(pos, price)
        if cfg.live:
            current_exchange = _find_position(
                broker.fetch_positions(sym), pos["instrument"]
            )
            if current_exchange is None:
                executed = _trigger_exit_record(broker, pos, sym, price)
                if executed is None:
                    ctrl.halt(
                        f"FLAT_WITHOUT_TRIGGER_EXECUTION:{pos['instrument']}"
                    )
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
                exit_r, status, exit_price, exit_fee = executed
                if not broker.cancel_all_orders(sym):
                    ctrl.halt(f"ORPHAN_CLEANUP_FAILED:{pos['instrument']}")
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
                ctrl.record_exit(
                    pos["signal_id"],
                    exit_r,
                    status,
                    exit_price=exit_price,
                    exit_fee_usd=exit_fee,
                )
                report["actions"].append({
                    "exit": pos["instrument"],
                    "exit_r": exit_r,
                    "status": status,
                    "source": "exchange_trigger",
                })
                continue
            if decision is not None:
                close_side = "sell" if pos["side"] == "LONG" else "buy"
                close = broker.place_market_order(
                    sym,
                    close_side,
                    float(current_exchange.get("contracts") or pos["qty"]),
                    reduce_only=True,
                    client_order_id=_client_id(
                        cfg.campaign_id, pos["signal_id"], "x",
                    ),
                )
                if not close.success:
                    remaining = _find_position(
                        broker.fetch_positions(sym), pos["instrument"],
                    )
                    if remaining is not None:
                        ctrl.halt(f"EXIT_ORDER_FAILED:{pos['instrument']}")
                        ctrl.save()
                        report["halted"] = [ctrl.state.halt_reason]
                        return _finish_cycle(ctrl, report)
                    executed = _trigger_exit_record(
                        broker, pos, sym, price,
                    )
                    if executed is None:
                        ctrl.halt(
                            f"EXIT_RACE_UNRESOLVED:{pos['instrument']}"
                        )
                        ctrl.save()
                        report["halted"] = [ctrl.state.halt_reason]
                        return _finish_cycle(ctrl, report)
                    exit_r, status, exit_price, exit_fee = executed
                else:
                    remaining = _find_position(
                        broker.fetch_positions(sym), pos["instrument"],
                    )
                    if remaining is not None:
                        ctrl.halt(f"EXIT_NOT_FLAT:{pos['instrument']}")
                        ctrl.save()
                        report["halted"] = [ctrl.state.halt_reason]
                        return _finish_cycle(ctrl, report)
                    exit_price = float(close.avg_fill_price or price)
                    exit_r = ce.compute_r(
                        pos["side"], pos["entry"], pos["stop"], exit_price,
                    )
                    status = decision["status"]
                    exit_fee = float(close.fee_usdt or 0.0)
                if not broker.cancel_all_orders(sym):
                    ctrl.halt(f"ORPHAN_CLEANUP_FAILED:{pos['instrument']}")
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
                ctrl.record_exit(
                    pos["signal_id"],
                    exit_r,
                    status,
                    exit_price=exit_price,
                    exit_fee_usd=exit_fee,
                )
                report["actions"].append({
                    "exit": pos["instrument"],
                    "exit_r": exit_r,
                    "status": status,
                    "source": "controller_market_exit",
                })
                continue
            desired_stop = ce.protective_stop_price(
                pos["side"],
                pos["entry"],
                pos["stop"],
                pos["peak_r"],
            )
            if not _replace_protective_stop(broker, pos, sym, desired_stop):
                ctrl.halt(f"PROTECTIVE_STOP_UPDATE_FAILED:{pos['instrument']}")
                ctrl.save()
                report["halted"] = [ctrl.state.halt_reason]
                return _finish_cycle(ctrl, report)
        if decision is not None:
            exit_fee_usd = 0.0
            if cfg.live:
                exchange_pos = _find_position(
                    broker.fetch_positions(sym), pos["instrument"]
                )
                if exchange_pos is not None:
                    ctrl.halt(f"EXIT_RECONCILE_POSITION_STILL_OPEN:{pos['instrument']}")
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
                if not broker.cancel_all_orders(sym):
                    ctrl.halt(f"ORPHAN_CLEANUP_FAILED:{pos['instrument']}")
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
            ctrl.record_exit(
                pos["signal_id"],
                decision["exit_r"],
                decision["status"],
                exit_price=price,
                exit_fee_usd=exit_fee_usd,
            )
            report["actions"].append(
                {"exit": pos["instrument"], "exit_r": decision["exit_r"], "status": decision["status"]}
            )
    ctrl.save()

    # 3. entry (only if not blocked by a bounded stop condition)
    blocked = ctrl.entries_blocked()
    if blocked:
        ctrl.maybe_complete()
        ctrl.save()
        report["entry_blocked"] = blocked
        return _finish_cycle(ctrl, report)

    cands = candidates_from_action_plan()
    sel = ce.select_entry(
        cands,
        now=ce.now_utc(),
        started_at=ce.parse_iso(ctrl.state.started_at),
        executed_ids=ctrl.state.executed_signal_ids,
        open_count=len(ctrl.state.open_positions),
        last_exit_at=ce.parse_iso(ctrl.state.last_exit_at) if ctrl.state.last_exit_at else None,
        cfg=cfg,
    )
    if sel["action"] != "ENTER":
        report["entry_skip"] = sel["reason"]
        return _finish_cycle(ctrl, report)

    c = sel["candidate"]
    sym = _sym_ccxt(c["instrument"])
    try:
        if cfg.live:
            broker.assert_live_entry_ready(cfg.risk_pct)
        bankroll = live_bankroll()
        mark = broker.fetch_mark_price(sym)
        current_r = ce.compute_r(
            c["side"], c["entry_ref"], c["stop_ref"], mark
        )
        if not (cfg.entry_min_r <= current_r <= cfg.entry_max_r):
            report["entry_skip"] = (
                f"PRICE_DRIFT_R:{current_r:.4f} outside "
                f"[{cfg.entry_min_r},{cfg.entry_max_r}]"
            )
            return _finish_cycle(ctrl, report)
        filt = broker.get_market_filters(sym)
        qty = ce.size_qty(bankroll, cfg.risk_pct, c["entry_ref"], c["stop_ref"], filt["step_size"], filt["min_qty"])
        feas = broker.check_order_feasible(sym, qty, mark)
        if qty <= 0 or feas:
            report["entry_skip"] = f"INFEASIBLE:{feas or 'zero-qty'}"
            return _finish_cycle(ctrl, report)
        if not broker.set_margin_mode(sym):
            report["entry_error"] = "MARGIN_MODE_FAILED"
            return _finish_cycle(ctrl, report)
        if not broker.set_leverage(sym, cfg.leverage):
            report["entry_error"] = "LEVERAGE_SET_FAILED"
            return _finish_cycle(ctrl, report)
        entry_side = "buy" if c["side"] == "LONG" else "sell"
        entry_client_id = _client_id(cfg.campaign_id, c["signal_id"], "e")
        sl_client_id = _client_id(cfg.campaign_id, c["signal_id"], "s")
        tp_client_id = _client_id(cfg.campaign_id, c["signal_id"], "t")
        ctrl.set_pending_entry(
            {
                "signal_id": c["signal_id"],
                "instrument": c["instrument"],
                "symbol": sym,
                "side": c["side"],
                "qty": qty,
                "client_order_id": entry_client_id,
                "sl_client_id": sl_client_id,
                "tp_client_id": tp_client_id,
                "entry_ref": float(c["entry_ref"]),
                "stop_ref": float(c["stop_ref"]),
                "submitted_at": ce.now_utc().isoformat(timespec="seconds"),
            }
        )
        res = broker.place_market_order(
            sym,
            entry_side,
            qty,
            client_order_id=entry_client_id,
        )
        if not res.success:
            try:
                found = broker.fetch_order_by_client_id(sym, entry_client_id)
            except Exception as exc:
                ctrl.halt(
                    f"ENTRY_UNKNOWN_RECONCILE_FAILED:{type(exc).__name__}"
                )
                ctrl.save()
                report["halted"] = [ctrl.state.halt_reason]
                return _finish_cycle(ctrl, report)
            if found is not None:
                ctrl.halt(f"ENTRY_UNKNOWN_FOUND:{entry_client_id}")
                ctrl.save()
                report["halted"] = [ctrl.state.halt_reason]
                return _finish_cycle(ctrl, report)
            ctrl.halt(f"ENTRY_UNRESOLVED_NOT_FOUND:{entry_client_id}")
            ctrl.save()
            report["halted"] = [ctrl.state.halt_reason]
            return _finish_cycle(ctrl, report)
        close_side = "sell" if c["side"] == "LONG" else "buy"
        fill_entry = float(res.avg_fill_price or mark)
        filled_qty = float(getattr(res, "filled_amount", 0.0) or qty)
        if filled_qty <= 0:
            ctrl.halt(f"ENTRY_SUCCESS_WITH_ZERO_FILL:{c['instrument']}")
            ctrl.save()
            report["halted"] = [ctrl.state.halt_reason]
            return _finish_cycle(ctrl, report)
        actual_stop, actual_tp = ce.targets_from_entry(
            c["side"], fill_entry, c["stop_ref"]
        )
        sl = broker.place_reduce_trigger(
            sym,
            close_side,
            filled_qty,
            actual_stop,
            "STOP",
            client_order_id=sl_client_id,
        )
        if not sl.success:  # naked guard: never hold an unprotected position
            close = broker.place_market_order(
                sym,
                close_side,
                filled_qty,
                reduce_only=True,
                client_order_id=_client_id(cfg.campaign_id, c["signal_id"], "g"),
            )
            ctrl.clear_pending_entry(
                "SL_FAILED_CLOSE_ATTEMPTED",
                close_success=close.success,
            )
            if not close.success:
                ctrl.halt(f"SL_FAILED_NAKED_POSITION:{c['instrument']}")
                ctrl.save()
                report["halted"] = [ctrl.state.halt_reason]
                return _finish_cycle(ctrl, report)
            _consume_terminal_entry_failure(
                ctrl, c["signal_id"], "SL_FAILED_CLOSED_FLAT"
            )
            report["entry_error"] = "SL_FAILED_CLOSED_FLAT"
            return _finish_cycle(ctrl, report)
        tp = broker.place_reduce_trigger(
            sym,
            close_side,
            filled_qty,
            actual_tp,
            "TP",
            client_order_id=tp_client_id,
        )
        if not tp.success:
            close = broker.place_market_order(
                sym,
                close_side,
                filled_qty,
                reduce_only=True,
                client_order_id=_client_id(cfg.campaign_id, c["signal_id"], "g"),
            )
            broker.cancel_all_orders(sym)
            ctrl.clear_pending_entry(
                "TP_FAILED_CLOSED_FLAT", close_success=close.success
            )
            if not close.success:
                ctrl.halt(f"TP_FAILED_NAKED_POSITION:{c['instrument']}")
                ctrl.save()
                report["halted"] = [ctrl.state.halt_reason]
                return _finish_cycle(ctrl, report)
            _consume_terminal_entry_failure(
                ctrl, c["signal_id"], "TP_FAILED_CLOSED_FLAT"
            )
            report["entry_error"] = "TP_FAILED_CLOSED_FLAT"
            return _finish_cycle(ctrl, report)
        if cfg.live:
            live_triggers = broker.fetch_trigger_orders(sym)
            if not _has_stop_and_tp(live_triggers):
                close = broker.place_market_order(
                    sym,
                    close_side,
                    filled_qty,
                    reduce_only=True,
                    client_order_id=_client_id(
                        cfg.campaign_id, c["signal_id"], "g"
                    ),
                )
                broker.cancel_all_orders(sym)
                ctrl.clear_pending_entry(
                    "BRACKET_VERIFY_FAILED_CLOSED_FLAT",
                    close_success=close.success,
                )
                if not close.success:
                    ctrl.halt(
                        f"BRACKET_VERIFY_FAILED_NAKED:{c['instrument']}"
                    )
                    ctrl.save()
                    report["halted"] = [ctrl.state.halt_reason]
                    return _finish_cycle(ctrl, report)
                _consume_terminal_entry_failure(
                    ctrl,
                    c["signal_id"],
                    "BRACKET_VERIFY_FAILED_CLOSED_FLAT",
                )
                report["entry_error"] = "BRACKET_VERIFY_FAILED_CLOSED_FLAT"
                return _finish_cycle(ctrl, report)
        pos = ce.Position(
            signal_id=c["signal_id"], instrument=c["instrument"], side=c["side"],
            entry=fill_entry,
            stop=actual_stop,
            tp=actual_tp,
            qty=filled_qty,
            opened_at=ce.now_utc().replace(microsecond=0).isoformat(),
            campaign_id=cfg.campaign_id,
            risk_usd=abs(fill_entry - actual_stop) * filled_qty,
            entry_fee_usd=float(res.fee_usdt or 0.0),
            protective_stop=actual_stop,
            entry_order_id=res.order_id,
            entry_client_id=entry_client_id,
            sl_order_id=sl.order_id,
            sl_client_id=sl_client_id,
            tp_order_id=tp.order_id,
            tp_client_id=tp_client_id,
        )
        ctrl.record_entry(pos)
        ctrl.save()
        report["actions"].append({"enter": pos.instrument, "side": pos.side, "qty": filled_qty, "dry_run": not cfg.live})
    except Exception as e:
        report["entry_error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return _finish_cycle(ctrl, report)


def _finish_cycle(ctrl: ce.CampaignController, report: dict) -> dict:
    ctrl.state.last_cycle_at = ce.now_utc().isoformat(timespec="seconds")
    ctrl.state.last_cycle_result = json.loads(json.dumps(report, default=str))
    ctrl.save()
    export_campaign_snapshot(ctrl)
    return report


def _reload_controller_state(ctrl: ce.CampaignController) -> None:
    """Adopt state written by another serialized command between run cycles."""
    if not ctrl.state_file.exists():
        return
    ctrl.state = ce.CampaignState.from_dict(
        json.loads(ctrl.state_file.read_text())
    )


def _emergency_flatten(ctrl: ce.CampaignController, reason: str) -> dict:
    """Close campaign-owned positions before HALT; never touch foreign positions."""
    cfg = ctrl.cfg
    broker = _make_broker(cfg)
    outcomes = []
    for pos in list(ctrl.state.open_positions):
        sym = _sym_ccxt(pos["instrument"])
        if cfg.live:
            exchange_pos = _find_position(
                broker.fetch_positions(sym), pos["instrument"],
            )
            if exchange_pos is not None:
                close_side = "sell" if pos["side"] == "LONG" else "buy"
                close = broker.place_market_order(
                    sym,
                    close_side,
                    float(exchange_pos.get("contracts") or pos["qty"]),
                    reduce_only=True,
                    client_order_id=_client_id(
                        cfg.campaign_id, pos["signal_id"], "q",
                    ),
                )
                outcomes.append({
                    "instrument": pos["instrument"],
                    "close_success": bool(close.success),
                })
                if not close.success:
                    ctrl.halt(
                        f"EMERGENCY_CLOSE_FAILED:{pos['instrument']}:{reason}"
                    )
                    ctrl.save()
                    return {"halted": ctrl.state.halt_reason, "outcomes": outcomes}
                if _find_position(
                    broker.fetch_positions(sym), pos["instrument"],
                ) is not None:
                    ctrl.halt(
                        f"EMERGENCY_CLOSE_NOT_FLAT:{pos['instrument']}:{reason}"
                    )
                    ctrl.save()
                    return {"halted": ctrl.state.halt_reason, "outcomes": outcomes}
            broker.cancel_all_orders(sym)
        ctrl.record_exit(
            pos["signal_id"],
            float(pos.get("current_r") or 0.0),
            "EMERGENCY",
            exit_price=None,
        )
    ctrl.halt(reason)
    ctrl.save()
    return {"halted": reason, "outcomes": outcomes}


def cmd_tick(args) -> int:
    ctrl = _controller(args)
    if ctrl.state.status != "RUNNING":
        print(json.dumps({"noop": ctrl.state.status, "summary": ctrl.summary()}, indent=2))
        return 0
    print(json.dumps(_tick_once(ctrl), indent=2, default=str))
    return 0


def cmd_run(args) -> int:
    ctrl = _controller(args)
    deadline = time.time() + args.max_seconds if args.max_seconds else None
    stop_requested = False

    def _request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    old_term = signal.signal(signal.SIGTERM, _request_stop)
    old_int = signal.signal(signal.SIGINT, _request_stop)
    try:
        while not stop_requested:
            _reload_controller_state(ctrl)
            if ctrl.state.status != "RUNNING":
                break
            report = _tick_once(ctrl)
            print(
                json.dumps(
                    {
                        "ts": ce.now_utc().isoformat(timespec="seconds"),
                        "cycle": report,
                        "summary": ctrl.summary(),
                    },
                    default=str,
                ),
                flush=True,
            )
            if deadline and time.time() >= deadline:
                break
            remaining = max(0.0, float(args.interval))
            while remaining > 0 and not stop_requested:
                step = min(1.0, remaining)
                time.sleep(step)
                remaining -= step
        if stop_requested and ctrl.cfg.live and ctrl.state.open_positions:
            with ctrl.cycle_lock():
                result = _emergency_flatten(
                    ctrl, "SERVICE_SIGTERM",
                )
                export_campaign_snapshot(ctrl)
            print(
                json.dumps({
                    "ts": ce.now_utc().isoformat(timespec="seconds"),
                    "service_stop": result,
                    "summary": ctrl.summary(),
                }),
                flush=True,
            )
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    print(json.dumps({"final": ctrl.summary()}, indent=2))
    return 0


def cmd_report(args) -> int:
    ctrl = _controller(args)
    s = ctrl.summary()
    verdict = "INSUFFICIENT"
    if s["closed_trades"] >= 30:
        verdict = "EDGE_CONFIRMED" if s["expectancy_r"] > 0.2 else "REJECT"
    print(json.dumps({
        "report": s,
        "verdict": verdict,
        "closed_records": ctrl.state.closed_records,
    }, indent=2))
    return 0


def cmd_stop(args) -> int:
    ctrl = _controller(args)
    if ctrl.cfg.live and args.confirm != STOP_PHRASE:
        raise RuntimeError(
            f"live campaign stop requires --confirm {STOP_PHRASE!r}"
        )
    with ctrl.cycle_lock():
        result = _emergency_flatten(
            ctrl, args.reason or "OPERATOR_STOP",
        )
        export_campaign_snapshot(ctrl)
    print(json.dumps({
        "stopped": True,
        "result": result,
        "summary": ctrl.summary(),
    }, indent=2))
    return 0


def _add_common(p) -> None:
    p.add_argument("--id", default="brutal_pilot")
    p.add_argument("--risk", type=float, default=0.25)
    p.add_argument("--lev", type=int, default=2)
    p.add_argument("--max-trades", type=int, default=50, dest="max_trades")
    p.add_argument(
        "--max-entries-per-day", type=int, default=4,
        dest="max_entries_per_day",
    )
    p.add_argument("--max-days", type=float, default=21.0, dest="max_days")
    p.add_argument("--loss-budget-r", type=float, default=20.0, dest="loss_budget_r")
    p.add_argument("--freshness-sec", type=float, default=600.0, dest="freshness_sec")
    p.add_argument("--cooldown-sec", type=float, default=1800.0, dest="cooldown_sec")
    p.add_argument("--entry-min-r", type=float, default=-0.10, dest="entry_min_r")
    p.add_argument("--entry-max-r", type=float, default=0.25, dest="entry_max_r")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brutalctl", description="autonomous BRUTAL1000 live campaign")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="print the campaign contract + current candidates")
    _add_common(sp); sp.add_argument("--live", action="store_true"); sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("arm", help="persist campaign config (requires --confirm phrase)")
    _add_common(sp); sp.add_argument("--live", action="store_true"); sp.add_argument("--confirm", default=""); sp.set_defaults(func=cmd_arm)

    sp = sub.add_parser("status", help="print campaign summary + open positions")
    sp.add_argument("--id", default="brutal_pilot"); sp.set_defaults(func=cmd_status, live=None)

    sp = sub.add_parser("tick", help="run exactly one autonomous cycle")
    sp.add_argument("--id", default="brutal_pilot"); sp.set_defaults(func=cmd_tick, live=None)

    sp = sub.add_parser("run", help="run cycles until an end condition")
    sp.add_argument("--id", default="brutal_pilot")
    sp.add_argument("--interval", type=float, default=60.0); sp.add_argument("--max-seconds", type=float, default=0.0, dest="max_seconds")
    sp.set_defaults(func=cmd_run, live=None)

    sp = sub.add_parser("report", help="final metrics + verdict")
    sp.add_argument("--id", default="brutal_pilot"); sp.set_defaults(func=cmd_report, live=None)

    sp = sub.add_parser("stop", help="operator halt")
    sp.add_argument("--id", default="brutal_pilot")
    sp.add_argument("--reason", default="")
    sp.add_argument("--confirm", default="")
    sp.set_defaults(func=cmd_stop, live=None)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"brutalctl: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
