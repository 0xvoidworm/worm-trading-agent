#!/usr/bin/env python3
"""Eksekusi LIVE Setup #1 — SHORT BTC + SL/TP. Self-check + abort guards.

REAL MONEY. Pre-flight: balance funded, price in band, no existing position.
Lalu: margin ISOLATED + leverage → market short → SL (STOP) → TP (TAKE_PROFIT).

Usage: PYTHONPATH=. .venv/bin/python -m scripts.exec_setup1
"""
from __future__ import annotations

import sys

from data_pipe.futures_broker import make_futures_broker

SYMBOL = "BTC/USDT:USDT"
SIDE = "sell"              # SHORT
AMOUNT = 0.001            # ~$77 notional
LEVERAGE = 3
SL = 78650.0
TP = 75300.0
PRICE_BAND = (76000.0, 79000.0)   # abort kalau price udah lari jauh
MIN_BAL = 10.0


def main() -> int:
    br = make_futures_broker(live=True)   # LIVE — real orders

    bal = br.fetch_futures_balance()
    fi = br.fetch_funding(SYMBOL)
    mark = fi.mark_price
    print(f"[pre] wallet free={bal['free']:.2f} USDT | mark={mark:.2f} | funding={fi.funding_rate_bps:+.2f}bp")

    if bal["free"] < MIN_BAL:
        print(f"ABORT: wallet free {bal['free']:.2f} < {MIN_BAL} — fund dulu")
        return 1
    if not (PRICE_BAND[0] <= mark <= PRICE_BAND[1]):
        print(f"ABORT: mark {mark:.2f} di luar band {PRICE_BAND} — re-spec setup")
        return 1
    if br.fetch_positions(SYMBOL):
        print("ABORT: udah ada posisi terbuka di symbol ini")
        return 1

    # margin mode + leverage
    print("set ISOLATED:", br.set_margin_mode(SYMBOL, "ISOLATED"))
    print(f"set leverage {LEVERAGE}x:", br.set_leverage(SYMBOL, LEVERAGE))

    # open short
    o = br.place_market_order(SYMBOL, SIDE, AMOUNT)
    print(f"OPEN short: ok={o.success} fill={o.filled_amount} @ {o.avg_fill_price} "
          f"notional={o.notional_usdt:.2f} fee={o.fee_usdt:.4f} err={o.error}")
    if not o.success:
        print("ABORT: open gagal — gak pasang SL/TP")
        return 1

    # protective (closing side = buy untuk nutup short)
    sl = br.place_reduce_trigger(SYMBOL, "buy", AMOUNT, SL, "STOP")
    print(f"SL  @ {SL}: ok={sl.success} id={sl.order_id} err={sl.error}")
    tp = br.place_reduce_trigger(SYMBOL, "buy", AMOUNT, TP, "TP")
    print(f"TP  @ {TP}: ok={tp.success} id={tp.order_id} err={tp.error}")

    for p in br.fetch_positions(SYMBOL):
        print(f"POSITION: {p.side} x{p.contracts} entry={p.entry_price} "
              f"liq={p.liq_price} liq_dist={p.liq_distance_pct:.1f}% uPnL={p.unrealized_pnl}")

    try:
        from data_pipe.telegram import notify_trade
        notify_trade("OPEN", "BTC SHORT",
                     f"0.001 @ {o.avg_fill_price:.0f} | SL {SL:.0f} TP {TP:.0f} | "
                     f"{LEVERAGE}x iso | risk ~$1.2 (setup#1 fade)", urgent=False)
        print("telegram: sent")
    except Exception as e:
        print("telegram skip:", e)

    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
