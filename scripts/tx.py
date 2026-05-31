#!/usr/bin/env python3
"""tx — Claude's trading execution CLI for trading_agent.

Reuses data_pipe.futures_broker. Single-command ops for the moves
Claude (or Worm) does repeatedly: status / price / scan / size / open /
close / cancel / log / trail / breakeven.

Cross-turn audit trail at ~/.txec/session.jsonl so Claude can reconstruct
state after compaction.

Conventions:
- Symbols accept short ('INJ', 'BTC') or full ('INJ/USDT:USDT')
- Sides accept long/short or buy/sell
- --json for machine output; default is compact human format
- Risk pre-flight: warns if combined risk > 15% wallet; --force overrides
- close cancels ALL triggers for the symbol (orphan auto-cleanup)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# Ensure data_pipe importable when invoked from anywhere
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from data_pipe.futures_broker import BinanceFuturesBroker  # noqa: E402

LOG_DIR = Path.home() / ".txec"
LOG_FILE = LOG_DIR / "session.jsonl"
LOG_DIR.mkdir(exist_ok=True)


# ─── Helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _wib() -> str:
    return time.strftime("%H:%M", time.localtime())


def sym_full(s: str) -> str:
    """Accept 'INJ' or 'INJ/USDT:USDT', return canonical futures form."""
    s = s.upper().strip()
    if "/" in s and ":" in s:
        return s
    if "USDT" in s and ":" not in s:
        base = s.replace("/USDT", "").replace("USDT", "")
        return f"{base}/USDT:USDT"
    return f"{s}/USDT:USDT"


def side_close(side: str) -> str:
    """Closing side for a position."""
    return "buy" if side.lower() in ("short", "sell") else "sell"


def side_open(side: str) -> str:
    """Opening side: long→buy, short→sell."""
    s = side.lower()
    if s in ("long", "buy"):
        return "buy"
    if s in ("short", "sell"):
        return "sell"
    raise ValueError(f"unknown side: {side}")


def log_event(cmd: str, **kw) -> None:
    rec = {"ts": _now_iso(), "cmd": cmd, **kw}
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _emit(payload, args):
    if getattr(args, "json", False):
        print(json.dumps(payload, default=str, indent=2))
    else:
        # Human format handled by caller; payload may already be string
        if isinstance(payload, str):
            print(payload)
        else:
            print(json.dumps(payload, default=str, indent=2))


def _broker() -> BinanceFuturesBroker:
    b = BinanceFuturesBroker()
    b._ensure_markets()
    return b


# ─── Commands ───────────────────────────────────────────────────────────

def cmd_status(args):
    b = _broker()
    bal = b.fetch_futures_balance()
    positions = b.fetch_positions()
    triggers_per_sym = {}
    for p in positions:
        sym = p.symbol
        triggers_per_sym.setdefault(sym, b.fetch_trigger_orders(sym))
    # also collect triggers for symbols without position (orphans)
    if args.symbol:
        sym = sym_full(args.symbol)
        if sym not in triggers_per_sym:
            triggers_per_sym[sym] = b.fetch_trigger_orders(sym)

    if args.json:
        out = {
            "ts_wib": _wib(),
            "wallet": {k: bal[k] for k in ("free", "used", "total") if k in bal},
            "positions": [asdict(p) for p in positions],
            "triggers": {s: t for s, t in triggers_per_sym.items()},
        }
        print(json.dumps(out, default=str, indent=2))
        return

    print(f"=== STATUS {_wib()} WIB ===")
    print(f"WALLET  free=${bal['free']:.2f}  used=${bal['used']:.2f}  total=${bal['total']:.2f}")
    if not positions:
        print("POSITIONS  (none)")
    else:
        print(f"POSITIONS  ({len(positions)})")
        for p in positions:
            mark = p.mark_price
            entry = p.entry_price
            delta_pct = (mark / entry - 1) * 100 * (1 if p.side == "LONG" else -1)
            print(f"  {p.symbol:24} {p.side:5} qty={p.contracts:>9} entry=${entry:.4f} "
                  f"mark=${mark:.4f}  {delta_pct:+5.2f}%  uPnL=${p.unrealized_pnl:+.3f} "
                  f"margin=${p.margin_usdt:.2f}")
    total_triggers = sum(len(v) for v in triggers_per_sym.values())
    print(f"TRIGGERS  ({total_triggers})")
    for sym, trigs in triggers_per_sym.items():
        for t in trigs:
            sp = t.get("stopPrice") or t.get("info", {}).get("stopPrice")
            ro = t.get("reduceOnly")
            tag = "ORPHAN" if not any(p.symbol == sym for p in positions) else ""
            print(f"  {sym:24} {t.get('type'):20} {t.get('side'):4} "
                  f"stop=${sp} qty={t.get('amount')} ro={ro} id={t.get('id')} {tag}")


def cmd_price(args):
    b = _broker()
    sym = sym_full(args.symbol)
    mark = b.fetch_mark_price(sym)
    fund = b.fetch_funding(sym)
    out = {
        "symbol": sym, "mark": mark,
        "funding_bps_8h": fund.funding_rate_bps,
        "funding_annual_pct": float(fund.funding_rate_bps) * 3 * 365 / 100,
    }
    if args.json:
        print(json.dumps(out, default=str, indent=2))
    else:
        print(f"{sym}  mark=${mark:.6g}  funding={fund.funding_rate_bps:+.2f}bps/8h "
              f"(annual {out['funding_annual_pct']:+.0f}%)")


def cmd_scan(args):
    """Lightweight scan — top movers + funding extremes (crypto-native filter)."""
    import urllib.request
    STOCK = {"USAR","V","HD","SNDK","FLNC","NBIS","SOXL","CRWV","BRKB","BEAT","BSB",
             "CL","BZ","MU","XAU","XAG","PAYP","VIC","LAB","DIS","PYPL","TSLA","AAPL",
             "NVDA","GOOGL","META","AMZN","SBUX","MCD","NATGAS","QCOM","MSFT","INTC",
             "ORCL","TSM","WDC","HIGH","FLUID","GUA","UB","DOGES"}
    def is_crypto(sym):
        base = sym.removesuffix("USDT")
        return base not in STOCK

    data = json.loads(urllib.request.urlopen(
        "https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10).read())
    fund_data = json.loads(urllib.request.urlopen(
        "https://fapi.binance.com/fapi/v1/premiumIndex", timeout=10).read())
    funding = {d["symbol"]: float(d["lastFundingRate"]) * 100 for d in fund_data}
    min_vol = args.min_vol_m * 1e6
    filt = [d for d in data if d["symbol"].endswith("USDT") and is_crypto(d["symbol"])
            and float(d["quoteVolume"]) > min_vol]
    filt.sort(key=lambda x: float(x["priceChangePercent"]), reverse=True)

    def fmt_row(d):
        last = float(d["lastPrice"]); lo = float(d["lowPrice"]); hi = float(d["highPrice"])
        pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
        fr = funding.get(d["symbol"], 0)
        return (f"{d['symbol']:14} {float(d['priceChangePercent']):>+7.2f}%  "
                f"last={last:>12.5g} vol={float(d['quoteVolume'])/1e6:>6.0f}M  "
                f"pos={pos:>5.1f}%  fnd={fr:>+7.4f}%")
    n = args.top
    if args.json:
        out = []
        for d in filt[:n] + filt[-n:]:
            last = float(d["lastPrice"]); lo = float(d["lowPrice"]); hi = float(d["highPrice"])
            pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
            out.append({"symbol": d["symbol"], "chg_pct": float(d["priceChangePercent"]),
                        "last": last, "vol_m": float(d["quoteVolume"]) / 1e6,
                        "pos_24h_pct": pos, "funding_pct_8h": funding.get(d["symbol"], 0)})
        print(json.dumps(out, indent=2))
        return
    print(f"=== TOP {n} GAINERS 24h (crypto-native, vol > {args.min_vol_m}M) ===")
    for d in filt[:n]:
        print("  " + fmt_row(d))
    print(f"\n=== TOP {n} LOSERS 24h ===")
    for d in filt[-n:][::-1]:
        print("  " + fmt_row(d))


def cmd_size(args):
    """Compute size from entry/SL and risk %."""
    b = _broker()
    sym = sym_full(args.symbol)
    bal = b.fetch_futures_balance()
    wallet = bal["total"]
    risk_pct = args.risk / 100
    risk_dollar = wallet * risk_pct
    sl_dist = abs(args.entry - args.sl)
    if sl_dist <= 0:
        print("error: entry == SL", file=sys.stderr); sys.exit(1)
    raw_qty = risk_dollar / sl_dist
    # snap to step
    mf = b.get_market_filters(sym)
    step = mf["step_size"]
    qty = (raw_qty // step) * step
    if qty < mf["min_qty"]:
        qty = mf["min_qty"]
    notional = qty * args.entry
    margin = notional / args.lev
    actual_risk = qty * sl_dist
    actual_risk_pct = actual_risk / wallet * 100
    out = {
        "symbol": sym, "wallet": wallet, "risk_target_pct": args.risk,
        "entry": args.entry, "sl": args.sl, "sl_dist": sl_dist,
        "raw_qty": raw_qty, "qty_snapped": qty,
        "notional": notional, "margin_at_lev": margin, "lev": args.lev,
        "actual_risk_usd": actual_risk, "actual_risk_pct": actual_risk_pct,
    }
    if args.tp:
        reward = qty * abs(args.tp - args.entry)
        out["tp"] = args.tp; out["reward_usd"] = reward
        out["rr"] = reward / actual_risk if actual_risk else None
    if args.json:
        print(json.dumps(out, indent=2)); return
    print(f"{sym}  wallet=${wallet:.2f}  risk_target={args.risk}% (${risk_dollar:.2f})")
    print(f"  entry=${args.entry}  SL=${args.sl}  dist=${sl_dist:.4f}")
    print(f"  qty={qty} (raw {raw_qty:.3f}, step {step})  notional=${notional:.2f}  margin@{args.lev}x=${margin:.2f}")
    print(f"  actual_risk=${actual_risk:.2f} ({actual_risk_pct:.2f}%)")
    if args.tp:
        print(f"  TP=${args.tp}  reward=${out['reward_usd']:.2f}  R/R 1:{out['rr']:.2f}")


def _risk_preflight(b: BinanceFuturesBroker, sym: str, qty: float, entry: float,
                    sl: Optional[float], force: bool) -> Optional[str]:
    """Return warning string if risk exceeds policy. None if OK."""
    if sl is None:
        return None
    bal = b.fetch_futures_balance()
    wallet = bal["total"]
    new_risk = qty * abs(entry - sl)
    new_pct = new_risk / wallet * 100
    if new_pct > 15 and not force:
        return (f"RISK PREFLIGHT: planned risk ${new_risk:.2f} = "
                f"{new_pct:.1f}% wallet (>15%). Use --force to override.")
    return None


def cmd_open(args):
    b = _broker()
    sym = sym_full(args.symbol)
    side = side_open(args.side)
    qty = args.qty

    # set leverage if specified
    if args.lev:
        b.set_leverage(sym, args.lev)

    mark = b.fetch_mark_price(sym)

    # auto-size from risk if --risk given and no qty (or qty=0)
    if (qty is None or qty <= 0) and args.risk and args.sl:
        bal = b.fetch_futures_balance()
        sl_dist = abs(mark - args.sl)
        risk_dollar = bal["total"] * args.risk / 100
        raw_qty = risk_dollar / sl_dist
        mf = b.get_market_filters(sym)
        step = mf["step_size"]
        qty = max((raw_qty // step) * step, mf["min_qty"])

    if not qty:
        print("error: --qty or (--risk + --sl) required", file=sys.stderr); sys.exit(1)

    warn = _risk_preflight(b, sym, qty, mark, args.sl, args.force)
    if warn:
        print(warn, file=sys.stderr); sys.exit(2)

    bal_before = b.fetch_futures_balance()

    print(f"=== OPEN {side.upper()} {qty} {sym} (mark ${mark}) ===")
    entry = b.place_market_order(sym, side, qty)
    if not entry.success:
        print(f"  ENTRY FAILED: {entry.raw}", file=sys.stderr); sys.exit(1)
    print(f"  filled={entry.filled_amount} avg=${entry.avg_fill_price} notional=${entry.notional_usdt:.2f}")

    close_side = side_close("LONG" if side == "buy" else "SHORT")
    sl_id = tp_id = None
    if args.sl:
        sl_ord = b.place_reduce_trigger(sym, close_side, qty, args.sl, "STOP")
        sl_id = sl_ord.order_id if sl_ord.success else None
        print(f"  SL @ ${args.sl}: success={sl_ord.success} id={sl_id}")
    if args.tp:
        tp_ord = b.place_reduce_trigger(sym, close_side, qty, args.tp, "TP")
        tp_id = tp_ord.order_id if tp_ord.success else None
        print(f"  TP @ ${args.tp}: success={tp_ord.success} id={tp_id}")

    bal_after = b.fetch_futures_balance()
    log_event("open", symbol=sym, side=side.upper(), qty=qty,
              entry_avg=entry.avg_fill_price, sl=args.sl, tp=args.tp,
              lev=args.lev, sl_id=sl_id, tp_id=tp_id,
              wallet_before=bal_before, wallet_after=bal_after)
    print(f"  wallet: ${bal_before['total']:.2f} → ${bal_after['total']:.2f}")


def cmd_close(args):
    """Market-close position + cancel ALL triggers for symbol (orphan cleanup)."""
    b = _broker()
    sym = sym_full(args.symbol)
    positions = b.fetch_positions(sym)
    bal_before = b.fetch_futures_balance()

    closed_pnl = None
    if not positions:
        print(f"{sym}: no position; will only cleanup triggers")
    for p in positions:
        side = side_close(p.side)
        qty = abs(p.contracts)
        print(f"=== CLOSE {p.side} {qty} {sym} (mark ${p.mark_price}) ===")
        close = b.place_market_order(sym, side, qty, reduce_only=True)
        if not close.success:
            print(f"  CLOSE FAILED: {close.raw}", file=sys.stderr)
        else:
            closed_pnl = p.unrealized_pnl
            print(f"  filled={close.filled_amount} avg=${close.avg_fill_price}")

    # Orphan cleanup — cancel ALL triggers for this symbol
    trigs = b.fetch_trigger_orders(sym)
    cancelled = []
    for t in trigs:
        tid = t.get("id")
        ok = b.cancel_order(sym, str(tid), conditional=True)
        cancelled.append((tid, ok))
        print(f"  cancel trigger {tid}: {ok}")

    bal_after = b.fetch_futures_balance()
    log_event("close", symbol=sym,
              positions=[asdict(p) for p in positions],
              closed_pnl=closed_pnl,
              cancelled_triggers=cancelled,
              wallet_before=bal_before, wallet_after=bal_after)
    print(f"  wallet: ${bal_before['total']:.2f} → ${bal_after['total']:.2f} "
          f"(Δ={bal_after['total']-bal_before['total']:+.3f})")


def cmd_cancel(args):
    """Cancel triggers — specific id or ALL for symbol."""
    b = _broker()
    sym = sym_full(args.symbol)
    if args.id:
        ok = b.cancel_order(sym, args.id, conditional=True)
        print(f"cancel {args.id}: {ok}")
        log_event("cancel", symbol=sym, order_id=args.id, ok=ok)
        return
    trigs = b.fetch_trigger_orders(sym)
    out = []
    for t in trigs:
        tid = t.get("id")
        ok = b.cancel_order(sym, str(tid), conditional=True)
        out.append((tid, ok))
        print(f"  cancel {tid}: {ok}")
    log_event("cancel_all", symbol=sym, cancelled=out)
    print(f"cancelled {sum(1 for _,o in out if o)}/{len(out)}")


def cmd_trail(args):
    """Trail SL: cancel existing STOP_MARKET, place new at given price."""
    b = _broker()
    sym = sym_full(args.symbol)
    positions = b.fetch_positions(sym)
    if not positions:
        print(f"{sym}: no position", file=sys.stderr); sys.exit(1)
    p = positions[0]
    qty = abs(p.contracts)
    close_side = side_close(p.side)

    # cancel existing STOP_MARKET triggers (keep TP)
    trigs = b.fetch_trigger_orders(sym)
    for t in trigs:
        if t.get("type", "").lower() == "stop_market" or "STOP" in t.get("info", {}).get("origType", ""):
            b.cancel_order(sym, str(t.get("id")), conditional=True)
            print(f"  cancelled old SL {t.get('id')}")
    sl_ord = b.place_reduce_trigger(sym, close_side, qty, args.price, "STOP")
    print(f"  new SL @ ${args.price}: success={sl_ord.success} id={sl_ord.order_id}")
    log_event("trail", symbol=sym, new_sl=args.price, ok=sl_ord.success,
              new_sl_id=sl_ord.order_id)


def cmd_pnl(args):
    """Realized P&L summary from Binance income endpoint + open uPnL.

    Aggregates: REALIZED_PNL (trade), COMMISSION (fees), FUNDING_FEE (perp funding).
    """
    b = _broker()
    params = {"limit": args.limit}
    if args.symbol:
        sym = sym_full(args.symbol)
        # Binance income endpoint takes plain ticker like 'BTCUSDT', not unified format
        params["symbol"] = sym.replace("/USDT:USDT", "USDT")
    if args.days is not None:
        start_ms = int((time.time() - args.days * 86400) * 1000)
        params["startTime"] = start_ms

    income_types = ["REALIZED_PNL", "COMMISSION", "FUNDING_FEE"]
    by_type = {t: 0.0 for t in income_types}
    by_symbol = {}  # {sym: {type: total}}
    rows = []
    for itype in income_types:
        q = dict(params); q["incomeType"] = itype
        try:
            data = b._ex.fapiPrivateGetIncome(q)
        except Exception as e:
            print(f"  fetch {itype} err: {e}", file=sys.stderr); continue
        for r in data:
            amt = float(r["income"])
            sym = r.get("symbol", "")
            by_type[itype] += amt
            by_symbol.setdefault(sym, {t: 0.0 for t in income_types})
            by_symbol[sym][itype] += amt
            rows.append({"type": itype, "symbol": sym, "income": amt, "time": r.get("time")})

    # also fetch open positions for uPnL
    positions = b.fetch_positions()
    open_upnl = sum(p.unrealized_pnl for p in positions)
    bal = b.fetch_futures_balance()

    realized_net = sum(by_type.values())
    total_pnl = realized_net + open_upnl

    if args.json:
        out = {
            "window_days": args.days,
            "symbol_filter": args.symbol,
            "wallet_total": bal["total"],
            "realized_by_type": by_type,
            "realized_by_symbol": by_symbol,
            "open_positions": [asdict(p) for p in positions],
            "open_upnl": open_upnl,
            "realized_net": realized_net,
            "total_pnl_realized_plus_open": total_pnl,
        }
        print(json.dumps(out, default=str, indent=2)); return

    window = f"last {args.days}d" if args.days is not None else "all-time"
    sym_tag = f" [{args.symbol}]" if args.symbol else ""
    print(f"=== PNL{sym_tag} {window} ===")
    print(f"  wallet now            : ${bal['total']:.2f}")
    print(f"  realized PnL          : ${by_type['REALIZED_PNL']:+.4f}")
    print(f"  commissions           : ${by_type['COMMISSION']:+.4f}")
    print(f"  funding fees          : ${by_type['FUNDING_FEE']:+.4f}")
    print(f"  ----------------------")
    print(f"  realized NET          : ${realized_net:+.4f}")
    print(f"  open positions uPnL   : ${open_upnl:+.4f}  ({len(positions)} pos)")
    print(f"  TOTAL (realized+open) : ${total_pnl:+.4f}")
    if by_symbol and not args.symbol:
        print(f"\n  per-symbol realized net:")
        for sym, totals in sorted(by_symbol.items(),
                                   key=lambda kv: sum(kv[1].values())):
            net = sum(totals.values())
            print(f"    {sym:14} pnl=${totals['REALIZED_PNL']:+.3f} "
                  f"fee=${totals['COMMISSION']:+.3f} fund=${totals['FUNDING_FEE']:+.3f} "
                  f"net=${net:+.3f}")


def cmd_watch(args):
    """Foreground price watch — exits on trigger.

    Use `nohup tx watch SYM --above X --max-mins 240 &> /tmp/watch.log &`
    for background. Trigger writes to ~/.txec/alerts.log + session log.
    """
    b = _broker()
    sym = sym_full(args.symbol)
    above = args.above
    below = args.below
    if above is None and below is None:
        print("error: provide --above and/or --below", file=sys.stderr); sys.exit(1)

    interval = max(5, args.interval)  # min 5s to avoid hammer
    timeout_s = args.max_mins * 60
    started = time.time()

    bands = []
    if above is not None: bands.append(f"above=${above}")
    if below is not None: bands.append(f"below=${below}")
    print(f"WATCH {sym} | {' '.join(bands)} | interval={interval}s timeout={args.max_mins}min")

    last_print = 0.0
    while True:
        elapsed = time.time() - started
        if elapsed > timeout_s:
            print(f"\nWATCH timeout after {args.max_mins}min — no trigger ({sym} last=${mark})")
            sys.exit(1)
        try:
            mark = b.fetch_mark_price(sym)
        except Exception as e:
            print(f"\n  fetch error: {e}; retry in {interval}s")
            time.sleep(interval); continue

        hit_above = above is not None and mark >= above
        hit_below = below is not None and mark <= below
        if hit_above or hit_below:
            kind = "ABOVE" if hit_above else "BELOW"
            threshold = above if hit_above else below
            msg = f"ALERT {sym} {kind} ${threshold}: mark=${mark} at {_now_iso()}"
            print(f"\n*** {msg} ***")
            alert_log = LOG_DIR / "alerts.log"
            with alert_log.open("a") as f:
                f.write(msg + "\n")
            log_event("watch_triggered", symbol=sym, kind=kind,
                      threshold=threshold, mark=mark)
            sys.exit(0)

        # periodic status print (every 60s) so background log isn't silent
        if time.time() - last_print >= 60 or last_print == 0:
            print(f"  [{elapsed/60:>5.1f}min] mark=${mark:.6g}")
            last_print = time.time()

        time.sleep(interval)


def cmd_log(args):
    if not LOG_FILE.exists():
        print("(no log yet)"); return
    lines = LOG_FILE.read_text().strip().split("\n")
    tail = lines[-args.n:]
    if args.json:
        print(json.dumps([json.loads(l) for l in tail], indent=2)); return
    for line in tail:
        r = json.loads(line)
        ts = r.get("ts", "")[:19]
        cmd = r.get("cmd", "?")
        sym = r.get("symbol", "")
        extras = []
        if "side" in r: extras.append(r["side"])
        if "qty" in r: extras.append(f"qty={r['qty']}")
        if "entry_avg" in r: extras.append(f"entry=${r['entry_avg']}")
        if "closed_pnl" in r and r["closed_pnl"] is not None:
            extras.append(f"uPnL=${r['closed_pnl']:+.3f}")
        wb, wa = r.get("wallet_before", {}), r.get("wallet_after", {})
        if wb and wa:
            extras.append(f"wallet ${wb.get('total',0):.2f}→${wa.get('total',0):.2f}")
        print(f"  {ts}  {cmd:8} {sym:24} {' '.join(extras)}")


# ─── Parser ─────────────────────────────────────────────────────────────

def _add_json(s):
    s.add_argument("--json", action="store_true", help="machine-readable output")


def build_parser():
    p = argparse.ArgumentParser(prog="tx", description="Claude's trading execution CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="balance + positions + triggers")
    s.add_argument("symbol", nargs="?", help="optional symbol to include even if no position")
    _add_json(s); s.set_defaults(func=cmd_status)

    s = sub.add_parser("price", help="mark + funding")
    s.add_argument("symbol")
    _add_json(s); s.set_defaults(func=cmd_price)

    s = sub.add_parser("scan", help="market scan: gainers/losers/funding (crypto-native)")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--min-vol-m", type=float, default=50.0, dest="min_vol_m",
                   help="min 24h vol in M USDT")
    _add_json(s); s.set_defaults(func=cmd_scan)

    s = sub.add_parser("size", help="compute qty from entry/SL/risk%")
    s.add_argument("symbol")
    s.add_argument("--entry", type=float, required=True)
    s.add_argument("--sl", type=float, required=True)
    s.add_argument("--risk", type=float, default=5.0, help="risk %% of wallet")
    s.add_argument("--lev", type=int, default=5)
    s.add_argument("--tp", type=float, help="optional TP for R/R calc")
    _add_json(s); s.set_defaults(func=cmd_size)

    s = sub.add_parser("open", help="market entry + brackets")
    s.add_argument("symbol")
    s.add_argument("side", choices=["long","short","buy","sell"])
    s.add_argument("--qty", type=float, help="explicit qty (or use --risk)")
    s.add_argument("--sl", type=float, help="stop-loss price")
    s.add_argument("--tp", type=float, help="take-profit price")
    s.add_argument("--lev", type=int, help="set leverage (5x default account)")
    s.add_argument("--risk", type=float, help="auto-size: risk %% of wallet (needs --sl)")
    s.add_argument("--force", action="store_true", help="bypass risk preflight")
    _add_json(s); s.set_defaults(func=cmd_open)

    s = sub.add_parser("close", help="market close + ORPHAN cleanup all triggers")
    s.add_argument("symbol")
    _add_json(s); s.set_defaults(func=cmd_close)

    s = sub.add_parser("cancel", help="cancel triggers (specific id or all for symbol)")
    s.add_argument("symbol")
    s.add_argument("--id", help="specific order id; else cancel all triggers for symbol")
    _add_json(s); s.set_defaults(func=cmd_cancel)

    s = sub.add_parser("trail", help="move SL to new price (cancel old, place new)")
    s.add_argument("symbol")
    s.add_argument("price", type=float)
    _add_json(s); s.set_defaults(func=cmd_trail)

    s = sub.add_parser("log", help="recent session events")
    s.add_argument("-n", type=int, default=20)
    _add_json(s); s.set_defaults(func=cmd_log)

    s = sub.add_parser("pnl", help="realized + open P&L summary")
    s.add_argument("symbol", nargs="?", help="optional symbol filter")
    s.add_argument("--days", type=int, help="lookback window days (default: all-time)")
    s.add_argument("--limit", type=int, default=200, help="max income rows per type")
    _add_json(s); s.set_defaults(func=cmd_pnl)

    s = sub.add_parser("watch", help="foreground price alert (use & for bg)")
    s.add_argument("symbol")
    s.add_argument("--above", type=float, help="alert if mark >= this")
    s.add_argument("--below", type=float, help="alert if mark <= this")
    s.add_argument("--interval", type=int, default=30, help="poll seconds (min 5)")
    s.add_argument("--max-mins", type=int, default=240, dest="max_mins",
                   help="max watch duration in minutes")
    _add_json(s); s.set_defaults(func=cmd_watch)
    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
