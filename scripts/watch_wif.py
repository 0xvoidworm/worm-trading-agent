#!/usr/bin/env python3
"""WIF/USDT:USDT setup watcher — READ-ONLY, ALERT-ONLY.

Pantau 2 trigger LONG disiplin (R:R ~1:3) + invalidation, ping Telegram pas nyala.
GAK auto-trade (auto-entry meme dari background = fragile spt monitor_limit). Pas alert,
Worm/Claude eksekusi MANUAL market+immediate-bracket (pola reliable).

Trigger (pakai bar 15m yang udah CLOSED, bukan yang lagi kebentuk):
  BREAKOUT : close > BREAKOUT_LVL + volume spike (> VOL_MULT x avg20)  -> long continuation
  PULLBACK : low 2h nyentuh <= SUPPORT_TAG + 15m reclaim (green & close>SUPPORT_RECLAIM)
  INVALID  : harga < BASE (support 0.1778 jebol) -> thesis long mati, stop watch
Hardening (pelajaran monitor_limit jalan BUTA 4.5j): fetch error >=ERR_ALERT_N berturut
-> TERIAK Telegram. Heartbeat rutin cuma ke log (gak spam Telegram).

Usage:
  test 1x:    PYTHONPATH=. .venv/bin/python -m scripts.watch_wif --once
  background: PYTHONPATH=. .venv/bin/python -m scripts.watch_wif --cycle 900
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from data_pipe.futures_broker import make_futures_broker
from data_pipe.telegram import send_message

WIB = timezone(timedelta(hours=7))
SYMBOL = "WIF/USDT:USDT"
BREAKOUT_LVL = 0.20       # reclaim+hold di atas ini + volume = breakout
SUPPORT_TAG = 0.1885      # low nyentuh <= ini = tag support
SUPPORT_RECLAIM = 0.189   # 15m close balik > ini = reclaim (support hold)
BASE = 0.1760             # < ini = support 0.1778 jebol, thesis mati
VOL_MULT = 1.5            # volume spike multiple
ERR_ALERT_N = 3


def _now() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S WIB")


def check(br) -> tuple[bool, str, str]:
    """Return (fired, kind, text). Trigger pakai bar 15m CLOSED terakhir (iloc[-2])."""
    d = pd.DataFrame(br._ex.fetch_ohlcv(SYMBOL, "15m", limit=30),
                     columns=["ts", "o", "h", "l", "c", "v"])
    price = float(d["c"].iloc[-1])              # bar forming ~ harga live
    closed_c = float(d["c"].iloc[-2])           # bar CLOSED terakhir
    closed_o = float(d["o"].iloc[-2])
    closed_v = float(d["v"].iloc[-2])
    vol_avg = float(d["v"].iloc[-22:-2].mean()) # avg 20 bar sebelum yg closed
    recent_low = float(d["l"].iloc[-10:-1].min())

    if price < BASE:
        return True, "INVALID", (
            f"[WIF WATCH] ❌ thesis MATI — harga {price:.4f} < base {BASE} "
            f"(support 0.1778 jebol). Long off, watch stop.")

    if closed_c > BREAKOUT_LVL and vol_avg > 0 and closed_v > VOL_MULT * vol_avg:
        entry, sl, tp = price, 0.193, 0.225
        rr = (tp - entry) / (entry - sl) if entry > sl else 0
        return True, "BREAKOUT", (
            f"[WIF WATCH] 🟢 BREAKOUT nyala! 15m close {closed_c:.4f} > {BREAKOUT_LVL} "
            f"+ vol {closed_v/vol_avg:.1f}x avg.\n"
            f"SETUP LONG: entry ~{entry:.4f} | SL 0.193 | TP 0.225 (R:R~{rr:.1f}) | 4-5x | risk ~$2.\n"
            f"Eksekusi market+bracket?")

    if recent_low <= SUPPORT_TAG and closed_c > SUPPORT_RECLAIM and closed_c > closed_o:
        entry, sl, tp = price, 0.1765, 0.225
        rr = (tp - entry) / (entry - sl) if entry > sl else 0
        return True, "PULLBACK", (
            f"[WIF WATCH] 🟢 PULLBACK-reclaim nyala! low {recent_low:.4f} tag support "
            f"+ 15m reclaim (close {closed_c:.4f} green).\n"
            f"SETUP LONG: entry ~{entry:.4f} | SL 0.1765 | TP 0.225 (R:R~{rr:.1f}) | 4-5x | risk ~$2.\n"
            f"Eksekusi market+bracket?")

    return False, "WAIT", (
        f"WIF {price:.4f} | brkout>{BREAKOUT_LVL} | pullback tag<={SUPPORT_TAG} "
        f"(recent-low {recent_low:.4f}) | base {BASE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, default=900, help="detik antar poll (default 15 min)")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    def log(m: str) -> None:
        print(f"[watch_wif {_now()}] {m}", flush=True)

    log(f"start {SYMBOL} | breakout>{BREAKOUT_LVL} pullback<={SUPPORT_TAG} base{BASE} | cycle {args.cycle}s")
    br = make_futures_broker(live=False)   # READ-ONLY (reads tetap live, place_* no-op)
    err = 0
    while True:
        try:
            fired, kind, text = check(br)
            err = 0
            log(f"{kind} :: {text.splitlines()[0]}")
            if fired:
                send_message(text, parse_mode=None, silent=False)
                log(f"TRIGGER {kind} -> alert terkirim, exit")
                return 0
            if args.once:
                send_message("[WIF WATCH test] " + text, parse_mode=None, silent=True)
                return 0
        except Exception as e:                       # noqa: BLE001
            err += 1
            log(f"POLL ERROR #{err}: {e!r}")
            if err % ERR_ALERT_N == 0:
                send_message(f"[WIF WATCH ERROR] poll gagal {err}x berturut: {e!r} — "
                             "masih hidup tapi BUTA, cek koneksi.", parse_mode=None, silent=False)
            if args.once:
                return 1
        time.sleep(args.cycle)


if __name__ == "__main__":
    sys.exit(main())
