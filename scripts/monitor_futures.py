#!/usr/bin/env python3
"""Auto check-in monitor untuk posisi FUTURES (READ-ONLY).

Poll status posisi tiap --cycle detik, kirim heartbeat ke Telegram:
  - rutin (CHOP)  -> silent (muncul di chat, gak buzz)
  - NEAR-SL/NEAR-TP -> buzz keras (urgent)
  - posisi CLOSED (SL/TP kena) -> final alert keras + exit
SL/TP auto-detect dari trigger orders live (adaptif kalau lo ubah).

HARDENING (pelajaran monitor_limit yg jalan BUTA 4.5 jam): kalau status()
error berturut-turut >= ERR_ALERT_N, monitor TERIAK ke Telegram. Gak pernah
diem walau lagi error.

Read-only: pakai futures_executor.status(live=False) -> gak pernah place order.

Usage:
  test 1x:     PYTHONPATH=. .venv/bin/python -m scripts.monitor_futures --once
  background:  PYTHONPATH=. .venv/bin/python -m scripts.monitor_futures --cycle 1500
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from data_pipe.futures_executor import status
from data_pipe.telegram import send_message

WIB = timezone(timedelta(hours=7))
NEAR_PCT = 0.6        # mark dalam 0.6% dari SL/TP -> alert keras
ERR_ALERT_N = 3       # error poll berturut sebelum teriak ke Telegram


def _now() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def _hhmm() -> str:
    return datetime.now(WIB).strftime("%H:%M WIB")


def parse_sl_tp(side: str, triggers):
    """SHORT (close=buy): SL = stop tertinggi, TP = stop terendah.
       LONG  (close=sell): SL = stop terendah, TP = stop tertinggi."""
    stops = sorted(t[2] for t in triggers if t and t[2] is not None)
    if len(stops) < 2:
        return None, None
    lo, hi = stops[0], stops[-1]
    return (hi, lo) if side.upper() == "SHORT" else (lo, hi)


def build_report(symbol: str, st: dict):
    """Return (text, state, urgent). state: CHOP / NEAR-SL / NEAR-TP / FLAT."""
    poss = [p for p in st["positions"] if p["symbol"] == symbol] or st["positions"]
    free = st["wallet_usdt"]["free"]
    if not poss:
        return (f"[CHECK-IN] {_hhmm()} {symbol}\n  posisi: TIDAK ADA (flat)\n"
                f"  free: ${free:.2f}", "FLAT", False)

    p = poss[0]
    side, mark, entry, upnl = p["side"], p["mark"], p["entry"], p["uPnL"]
    sl, tp = parse_sl_tp(side, st["triggers"])

    state, urgent = "CHOP", False
    if sl and abs(sl - mark) / mark * 100 <= NEAR_PCT:
        state, urgent = "NEAR-SL", True
    elif tp and abs(mark - tp) / mark * 100 <= NEAR_PCT:
        state, urgent = "NEAR-TP", True

    sign = "+" if upnl >= 0 else ""
    lines = [
        f"[CHECK-IN] {_hhmm()} {symbol}  ({state})",
        f"  {side} {p['contracts']} @ {entry}",
        f"  mark {mark:.1f}   uPnL {sign}${upnl:.2f}",
    ]
    if sl:
        lines.append(f"  SL {sl:.0f}  (jarak {abs(sl - mark) / mark * 100:.2f}%)")
    if tp:
        lines.append(f"  TP {tp:.0f}  (jarak {abs(mark - tp) / mark * 100:.2f}%)")
    lines.append(f"  liq {p['liq']:.0f} ({p['liq_dist_pct']:.0f}% jauh)   free ${free:.2f}")
    if state == "NEAR-SL":
        lines.append("  WARNING: mark deket SL — thesis short goyah, siap-siap.")
    elif state == "NEAR-TP":
        lines.append("  mark deket TP — siap profit, jangan greedy.")
    return ("\n".join(lines), state, urgent)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    ap.add_argument("--cycle", type=int, default=1500, help="detik antar poll (default 25 min)")
    ap.add_argument("--once", action="store_true", help="cek sekali (test) lalu exit")
    args = ap.parse_args()

    def log(m: str) -> None:
        print(f"[monitor_futures {_now()}] {m}", flush=True)

    log(f"start watch {args.symbol} | cycle {args.cycle}s | once={args.once}")
    had_pos = False
    err_streak = 0

    while True:
        try:
            st = status(args.symbol, live=False)   # READ-ONLY
            err_streak = 0
            text, state, urgent = build_report(args.symbol, st)
            log(f"{state} :: " + text.replace("\n", " | "))

            if state == "FLAT":
                if had_pos:
                    send_message("[CLOSED] " + args.symbol + " — posisi hilang, "
                                 "kemungkinan SL/TP kena. CEK!\n" + text,
                                 parse_mode=None, silent=False)
                    log("position CLOSED -> alert terkirim, exit")
                else:
                    send_message(text + "\n  (gak ada yg dimonitor -> exit)",
                                 parse_mode=None, silent=False)
                    log("no position -> exit")
                return 0

            had_pos = True
            # --once selalu buzz (biar test keliatan); rutin loop = silent
            send_message(text, parse_mode=None,
                         silent=(not urgent and not args.once))
        except Exception as e:                       # noqa: BLE001
            err_streak += 1
            log(f"POLL ERROR #{err_streak}: {e!r}")
            if err_streak % ERR_ALERT_N == 0:
                send_message(f"[MONITOR ERROR] {args.symbol} poll gagal "
                             f"{err_streak}x berturut: {e!r}\n"
                             "Monitor masih hidup tapi BUTA — cek koneksi/clock.",
                             parse_mode=None, silent=False)

        if args.once:
            return 0
        time.sleep(args.cycle)


if __name__ == "__main__":
    sys.exit(main())
