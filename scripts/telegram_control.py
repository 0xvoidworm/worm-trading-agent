#!/usr/bin/env python3
"""Telegram control bot — bidirectional trading remote.

Long-poll TELEGRAM_BOT_TOKEN (bot trading DEDICATED @wantrax665_bot, terpisah dari
TikTok bot biar gak rebutan getUpdates). HANYA respon ke TELEGRAM_CHAT_ID (authorized).
Command DETERMINISTIK — eksekusi lewat futures_executor guards, BUKAN LLM-asal-trade.

Commands:
  /help            list
  /status /s       posisi + wallet + uPnL
  /price [SYM]     mark price (default BTC)
  /wif             WIF watch state + jarak trigger
  /go              eksekusi WIF LONG kalau trigger live (breakout/pullback), else NOLAK
  /close SYM       tutup posisi SYM (market reduceOnly) + cancel leftover trigger
  /flat            tutup SEMUA posisi + cancel semua order
LIVE (/go /close /flat) cuma dari authorized chat. Posisi concurrent cap 2 (BTC + 1) —
operator yang jaga, bukan auto.

Run: PYTHONPATH=. .venv/bin/python -m scripts.telegram_control
"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from data_pipe.telegram import _load_env
from data_pipe.futures_broker import make_futures_broker
from data_pipe.futures_executor import open_protected
import scripts.watch_wif as ww

WIB = timezone(timedelta(hours=7))
RISK_USD = 2.0          # risk per trade via SL (dollar)
LEVERAGE = 5            # Worm minta longgar; broker ceiling 5; liq tetep dijaga SL
POLL_TIMEOUT = 30

HELP = ("🤖 Trading control bot\n"
        "/status (/s) — posisi + wallet\n"
        "/price [SYM] — mark price\n"
        "/wif — WIF watch state\n"
        "/go — eksekusi WIF long kalau trigger live\n"
        "/close SYM — tutup posisi + cancel trigger\n"
        "/flat — tutup SEMUA\n"
        "/help — ini")


def _now() -> str:
    return datetime.now(WIB).strftime("%H:%M:%S WIB")


def log(m: str) -> None:
    print(f"[tg_control {_now()}] {m}", flush=True)


def norm(sym: str) -> str:
    sym = sym.upper()
    return sym if "/" in sym else f"{sym}/USDT:USDT"


def fmt_status(br) -> str:
    bal = br.fetch_futures_balance()
    pos = br.fetch_positions()
    out = [f"WALLET ${bal['total']:.2f} (free ${bal['free']:.2f})"]
    if not pos:
        out.append("posisi: FLAT")
    for p in pos:
        out.append(f"{p.symbol} {p.side} {p.contracts} @ {p.entry_price:.6g}\n"
                   f"  mark {p.mark_price:.6g}  uPnL ${p.unrealized_pnl:+.2f}  liq {p.liq_price:.6g}")
    return "\n".join(out)


def fmt_price(br, arg) -> str:
    sym = norm(arg) if arg else "BTC/USDT:USDT"
    return f"{sym} mark {br.fetch_funding(sym).mark_price:.6g}"


def wif_eval(br):
    """(kind|None, price, sl, tp). Pakai konstanta watch_wif (dedup ringan)."""
    d = pd.DataFrame(br._ex.fetch_ohlcv(ww.SYMBOL, "15m", limit=30),
                     columns=["ts", "o", "h", "l", "c", "v"])
    price = float(d.c.iloc[-1]); cc = float(d.c.iloc[-2]); co = float(d.o.iloc[-2])
    cv = float(d.v.iloc[-2]); va = float(d.v.iloc[-22:-2].mean()); rl = float(d.l.iloc[-10:-1].min())
    if price < ww.BASE:
        return ("INVALID", price, None, None)
    if cc > ww.BREAKOUT_LVL and va > 0 and cv > ww.VOL_MULT * va:
        return ("BREAKOUT", price, 0.193, 0.225)
    if rl <= ww.SUPPORT_TAG and cc > ww.SUPPORT_RECLAIM and cc > co:
        return ("PULLBACK", price, 0.1765, 0.225)
    return (None, price, None, None)


def cmd_go(br) -> str:
    kind, price, sl, tp = wif_eval(br)
    if kind in (None, "INVALID"):
        return (f"WIF {price:.4f}: gak ada trigger live ({kind or 'WAIT'}). "
                f"GAK gua eksekusi — disiplin, tunggu pitch (breakout>0.20 / pullback 0.188).")
    risk_dist = price - sl
    if risk_dist <= 0:
        return f"WIF setup invalid (price {price} <= sl {sl})"
    amount = RISK_USD / risk_dist
    if amount * price < 5:                       # min notional ~$5
        amount = 5.0 / price
    amount = float(br._ex.amount_to_precision(ww.SYMBOL, amount))
    r = open_protected(ww.SYMBOL, "buy", amount, sl, tp, leverage=LEVERAGE, live=True,
                       price_band=(ww.BASE, 0.23), note=f"WIF {kind} via /go")
    return (f"[/go {kind}] {'OK' if r.ok else 'GAGAL'}\n{r.msg}")


def cmd_close(br, sym) -> str:
    pos = [p for p in br.fetch_positions(sym) if p.symbol == sym]
    if not pos:
        return f"{sym}: gak ada posisi"
    p = pos[0]
    cs = "sell" if p.side == "LONG" else "buy"
    o = br.place_market_order(sym, cs, p.contracts, reduce_only=True)
    br.cancel_all_orders(sym)
    return f"{sym} CLOSE {p.side} {p.contracts}: {'OK' if o.success else o.error}. triggers cancelled."


def cmd_flat(br) -> str:
    pos = br.fetch_positions()
    if not pos:
        return "udah FLAT"
    msgs = []
    for p in pos:
        cs = "sell" if p.side == "LONG" else "buy"
        o = br.place_market_order(p.symbol, cs, p.contracts, reduce_only=True)
        br.cancel_all_orders(p.symbol)
        msgs.append(f"{p.symbol}: {'OK' if o.success else o.error}")
    return "FLAT ALL:\n" + "\n".join(msgs)


def handle(br, text, send) -> None:
    parts = text.split()
    cmd = parts[0].lower().lstrip("/")
    arg = parts[1] if len(parts) > 1 else None
    try:
        if cmd in ("start", "help"):
            send(HELP)
        elif cmd in ("status", "s"):
            send(fmt_status(br))
        elif cmd == "price":
            send(fmt_price(br, arg))
        elif cmd == "wif":
            send(ww.check(br)[2])
        elif cmd == "go":
            send(cmd_go(br))
        elif cmd == "close":
            send(cmd_close(br, norm(arg)) if arg else "usage: /close SYM (mis: /close btc)")
        elif cmd == "flat":
            send(cmd_flat(br))
        else:
            send(f"unknown: /{cmd}. /help")
    except Exception as e:                           # noqa: BLE001
        send(f"ERR /{cmd}: {e!r}")
        log("handle err: " + traceback.format_exc())


def main() -> int:
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat = str(env.get("TELEGRAM_CHAT_ID") or "")
    if not token:
        sys.exit("no TELEGRAM_BOT_TOKEN di ~/.secrets/telegram.env")
    base = f"https://api.telegram.org/bot{token}"

    def send(t: str) -> None:
        try:
            requests.post(f"{base}/sendMessage",
                          json={"chat_id": chat, "text": t, "disable_web_page_preview": True},
                          timeout=15)
        except Exception as e:                       # noqa: BLE001
            log(f"send err: {e!r}")

    log(f"start control bot | authorized chat {chat or '(unset!)'}")
    br = make_futures_broker(live=True)
    send("🤖 control bot ONLINE. /help buat commands.")
    offset = None
    errs = 0
    while True:
        try:
            params = {"timeout": POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{base}/getUpdates", params=params, timeout=POLL_TIMEOUT + 10).json()
            errs = 0
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                m = upd.get("message") or {}
                cid = str((m.get("chat") or {}).get("id") or "")
                text = (m.get("text") or "").strip()
                if cid != chat:
                    log(f"IGNORE non-authorized chat {cid}: {text[:30]}")
                    continue
                if not text:
                    continue
                log(f"cmd from {cid}: {text}")
                handle(br, text, send)
        except Exception as e:                       # noqa: BLE001
            errs += 1
            log(f"loop err #{errs}: {e!r}")
            if errs % 5 == 0:
                send(f"[control bot] loop error {errs}x: {e!r} — masih idup, retry.")
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
