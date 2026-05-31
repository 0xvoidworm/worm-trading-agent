#!/usr/bin/env python3
"""E1 — Scaled-Up Bounce Fade: SHORT BTC limit @75.9k, 10x lev ~90% margin.

Flow:
  1. Pre-flight (balance, no existing position, price guard)
  2. Set leverage 10x cross
  3. Place limit SHORT 0.006 BTC @ 75900
  4. Poll fill (10s, max 24h)
  5. On fill → SL 76800 + TP 73500 (reduce-only) + Telegram
  6. On timeout → cancel limit

Usage: PYTHONPATH=. .venv/bin/python -m scripts.exec_e1
"""
from __future__ import annotations
import sys, time

from data_pipe.futures_broker import make_futures_broker

SYMBOL = "BTC/USDT:USDT"
SIDE = "sell"
AMOUNT = 0.006
LEVERAGE = 10
ENTRY = 75900.0
SL = 76800.0
TP = 73500.0
POLL_S = 10
MAX_MIN = 1440
PRICE_GUARD_MAX = 75800.0


def main() -> int:
    br = make_futures_broker(live=True)
    bal = br.fetch_futures_balance()
    fi = br.fetch_funding(SYMBOL)
    mark = fi.mark_price
    needed = AMOUNT * ENTRY / LEVERAGE
    print(f"[e1 pre] wallet={bal['free']:.2f}  mark={mark:.2f}  margin_needed={needed:.2f}  funding={fi.funding_rate_bps:+.2f}bp", flush=True)

    if bal["free"] < needed * 1.02:
        print(f"ABORT: wallet {bal['free']:.2f} < needed {needed*1.02:.2f}"); return 1
    if mark > PRICE_GUARD_MAX:
        print(f"ABORT: mark {mark:.0f} sudah > guard {PRICE_GUARD_MAX} (bounce udah jalan)"); return 1
    if br.fetch_positions(SYMBOL):
        print("ABORT: posisi BTC udah ada"); return 1

    print(f"set leverage {LEVERAGE}x:", br.set_leverage(SYMBOL, LEVERAGE), flush=True)

    o = br.place_limit_order(SYMBOL, SIDE, AMOUNT, ENTRY)
    print(f"LIMIT SHORT placed: ok={o.success} id={o.order_id} qty={AMOUNT} @ {ENTRY} err={o.error}", flush=True)
    if not o.success:
        return 1

    print(f"[e1] watch fill | poll {POLL_S}s | max {MAX_MIN}min", flush=True)
    waited = 0
    while waited < MAX_MIN * 60:
        try:
            pos = br.fetch_positions(SYMBOL)
        except Exception as e:
            print(f"poll err: {e}", flush=True); pos = []
        if pos:
            p = pos[0]
            print(f"FILLED: {p.side} qty={p.qty} @ {p.entry_price}", flush=True)
            sl = br.place_reduce_trigger(SYMBOL, "buy", AMOUNT, SL, "STOP")
            tp = br.place_reduce_trigger(SYMBOL, "buy", AMOUNT, TP, "TP")
            print(f"SL @ {SL}: ok={sl.success} id={sl.order_id} err={sl.error}", flush=True)
            print(f"TP @ {TP}: ok={tp.success} id={tp.order_id} err={tp.error}", flush=True)
            try:
                from data_pipe.telegram import notify_trade
                notify_trade("OPEN", SYMBOL, {
                    "dir": "SHORT E1 (10x scaled-up)",
                    "entry": p.entry_price, "qty": AMOUNT,
                    "SL": SL, "TP": TP,
                    "risk": round((SL-p.entry_price)*AMOUNT, 2),
                    "reward": round((p.entry_price-TP)*AMOUNT, 2),
                }, urgent=True)
            except Exception as e:
                print(f"telegram skip: {e}", flush=True)
            return 0
        time.sleep(POLL_S); waited += POLL_S

    print("TIMEOUT 24h — cancel resting limit", flush=True)
    try:
        br._ex.cancel_all_orders(SYMBOL); print("cancelled OK", flush=True)
    except Exception as e:
        print(f"cancel err: {e}", flush=True)
    try:
        from data_pipe.telegram import notify_trade
        notify_trade("CANCEL", SYMBOL, {"reason": "E1 limit timeout 24h, no fill"}, urgent=False)
    except Exception: pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
