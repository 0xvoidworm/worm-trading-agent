#!/usr/bin/env python3
"""Monitor resting LIMIT entry → on fill pasang SL+TP + Telegram. Timeout→cancel.

Jalanin di BACKGROUND. Params per-trade di constants. Begitu limit kefill,
SL & TP langsung dipasang (no protection gap), notif Telegram, lalu exit.

Usage (background): PYTHONPATH=. .venv/bin/python -m scripts.monitor_limit
"""
from __future__ import annotations

import sys
import time

from data_pipe.futures_broker import make_futures_broker

SYMBOL = "WIF/USDT:USDT"
CLOSE_SIDE = "sell"        # LONG → close = sell
AMOUNT = 215.0
SL = 0.1765
TP = 0.2040
POLL_S = 45
MAX_MIN = 720             # 12 jam, lalu cancel limit


def main() -> int:
    br = make_futures_broker(live=True)
    print(f"[monitor_limit] watch {SYMBOL} fill | poll {POLL_S}s | max {MAX_MIN}min", flush=True)
    waited = 0
    while waited < MAX_MIN * 60:
        try:
            pos = br.fetch_positions(SYMBOL)
        except Exception as e:
            print(f"[monitor_limit] poll err: {e}", flush=True)
            pos = []
        if pos:
            p = pos[0]
            print(f"[monitor_limit] FILLED: {p.side} x{p.contracts} @ {p.entry_price}", flush=True)
            sl = br.place_reduce_trigger(SYMBOL, CLOSE_SIDE, AMOUNT, SL, "STOP")
            tp = br.place_reduce_trigger(SYMBOL, CLOSE_SIDE, AMOUNT, TP, "TP")
            trig = br.fetch_trigger_orders(SYMBOL)
            protected = len(trig) >= 2
            print(f"[monitor_limit] SL ok={sl.success} TP ok={tp.success} "
                  f"protected={protected} ({len(trig)} triggers)", flush=True)
            try:
                from data_pipe.telegram import notify_trade
                notify_trade("OPEN", SYMBOL, {
                    "dir": "LONG (limit filled)", "entry": round(p.entry_price, 4),
                    "SL": SL, "TP": TP, "protected": "YES" if protected else "CHECK!",
                }, urgent=True)
            except Exception:
                pass
            return 0
        time.sleep(POLL_S)
        waited += POLL_S
    print("[monitor_limit] TIMEOUT — cancel resting limit", flush=True)
    try:
        br._ex.cancel_all_orders(SYMBOL)
        print("[monitor_limit] cancelled", flush=True)
    except Exception as e:
        print(f"[monitor_limit] cancel err: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
