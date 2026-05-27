#!/usr/bin/env python3
"""Read-only smoke untuk futures_broker.py — verifikasi interface ccxt USDT-M.

Pakai DryRunFuturesBroker: reads tetap LIVE (balance/funding/positions),
tapi place_* disintesis (NO order ke exchange). NO set_leverage (mutasi state).

Usage:
    PYTHONPATH=. .venv/bin/python scripts/futures_smoke.py
"""
from __future__ import annotations

import sys

from data_pipe.futures_broker import make_futures_broker


SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "DOGE/USDT:USDT", "XRP/USDT:USDT",
]


def main() -> int:
    br = make_futures_broker(live=False)
    print(f"[futures-smoke] broker={type(br).__name__} is_live={br.is_live}")

    try:
        bal = br.fetch_futures_balance()
        print(f"[1] futures USDT: total={bal['total']:.4f} free={bal['free']:.4f} used={bal['used']:.4f}")
    except Exception as e:
        print(f"[1] futures balance FAIL: {type(e).__name__}: {e}")
        return 1

    try:
        print(f"[2] BTC mark price: {br.fetch_mark_price('BTC/USDT:USDT'):,.2f}")
    except Exception as e:
        print(f"[2] mark price FAIL: {type(e).__name__}: {e}")

    print("[3] funding (carry candidate preview, sorted desc):")
    rows = []
    for s in SYMBOLS:
        try:
            fi = br.fetch_funding(s)
            rows.append((s, fi.funding_rate_bps, fi.annualized_pct()))
        except Exception as e:
            print(f"      {s}: FAIL {type(e).__name__}: {e}")
    for s, bps, ann in sorted(rows, key=lambda r: -r[1]):
        print(f"      {s:18s} funding={bps:+.4f} bps/8h  ann≈{ann:+.2f}%")

    try:
        pos = br.fetch_positions()
        print(f"[4] open futures positions: {len(pos)}")
        for p in pos:
            print(f"      {p.symbol} {p.side} x{p.contracts} liq={p.liq_price} dist={p.liq_distance_pct:.1f}%")
    except Exception as e:
        print(f"[4] positions FAIL: {type(e).__name__}: {e}")

    try:
        f = br.get_market_filters("BTC/USDT:USDT")
        print(f"[5] BTC filters: min_qty={f['min_qty']} min_notional={f['min_notional']} step={f['step_size']}")
    except Exception as e:
        print(f"[5] filters FAIL: {type(e).__name__}: {e}")

    try:
        r = br.place_market_order("BTC/USDT:USDT", "sell", 0.01)
        print(f"[6] dry-run order: success={r.success} dry_run={r.dry_run} notional={r.notional_usdt:.2f} fee={r.fee_usdt:.4f}")
    except Exception as e:
        print(f"[6] dry-run order FAIL: {type(e).__name__}: {e}")

    print("[SMOKE DONE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
