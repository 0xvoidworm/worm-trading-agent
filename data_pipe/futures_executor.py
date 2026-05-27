"""Reusable futures execution + status — discretionary trading engine.

open_protected(): SATU panggilan buat trade — guard pre-check, set leverage,
market entry, pasang SL+TP (reduceOnly trigger), VERIFY proteksi nyangkut
(via stop=True), notify Telegram. Risk dikontrol SL.

status(): snapshot posisi + trigger orders buat babysit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from data_pipe.futures_broker import make_futures_broker


@dataclass
class ExecResult:
    ok: bool
    msg: str
    entry_price: float = 0.0
    sl_id: str = ""
    tp_id: str = ""
    protected: bool = False


def open_protected(
    symbol: str, side: str, amount: float, sl: float, tp: float,
    leverage: int = 3, live: bool = False,
    price_band: Optional[tuple] = None, min_bal: float = 10.0, note: str = "",
) -> ExecResult:
    """Buka posisi + proteksi. side='sell'(SHORT)/'buy'(LONG). sl/tp = harga absolut."""
    br = make_futures_broker(live=live)
    close_side = "buy" if side == "sell" else "sell"

    bal = br.fetch_futures_balance()
    mark = br.fetch_funding(symbol).mark_price
    if bal["free"] < min_bal:
        return ExecResult(False, f"wallet free {bal['free']:.2f} < {min_bal}")
    if price_band and not (price_band[0] <= mark <= price_band[1]):
        return ExecResult(False, f"mark {mark:.2f} di luar band {price_band} — re-spec")
    if br.fetch_positions(symbol):
        return ExecResult(False, "udah ada posisi di symbol ini")
    # sanity arah SL/TP (cegah fat-finger)
    if side == "sell" and not (sl > mark > tp):
        return ExecResult(False, f"SHORT butuh SL>{mark:.0f}>TP; dapet SL={sl} TP={tp}")
    if side == "buy" and not (tp > mark > sl):
        return ExecResult(False, f"LONG butuh TP>{mark:.0f}>SL; dapet SL={sl} TP={tp}")

    br.set_margin_mode(symbol, "ISOLATED")
    br.set_leverage(symbol, leverage)

    o = br.place_market_order(symbol, side, amount)
    if not o.success:
        return ExecResult(False, f"open gagal: {o.error}")
    sl_r = br.place_reduce_trigger(symbol, close_side, amount, sl, "STOP")
    tp_r = br.place_reduce_trigger(symbol, close_side, amount, tp, "TP")

    # VERIFY proteksi beneran nyangkut (conditional orders perlu stop=True)
    trig = br.fetch_trigger_orders(symbol)
    protected = len(trig) >= 2
    dir_ = "SHORT" if side == "sell" else "LONG"

    try:
        from data_pipe.telegram import notify_trade
        notify_trade("OPEN", symbol, {
            "dir": dir_, "entry": round(o.avg_fill_price, 2),
            "SL": sl, "TP": tp, "lev": f"{leverage}x",
            "protected": "YES" if protected else "CHECK!", "note": note,
        }, urgent=not protected)
    except Exception:
        pass

    msg = (f"{dir_} {amount} {symbol} @ {o.avg_fill_price:.2f} | SL {sl} | TP {tp} | "
           f"protected={protected} ({len(trig)} trigger orders) | "
           f"SL_id={sl_r.order_id} TP_id={tp_r.order_id}")
    return ExecResult(o.success, msg, o.avg_fill_price,
                      sl_r.order_id or "", tp_r.order_id or "", protected)


def status(symbol: Optional[str] = None, live: bool = False) -> dict:
    """Snapshot posisi + SL/TP trigger orders buat babysit."""
    br = make_futures_broker(live=live)
    bal = br.fetch_futures_balance()
    positions = []
    for p in br.fetch_positions(symbol):
        positions.append({
            "symbol": p.symbol, "side": p.side, "contracts": p.contracts,
            "entry": p.entry_price, "mark": p.mark_price,
            "uPnL": round(p.unrealized_pnl, 4),
            "liq": p.liq_price, "liq_dist_pct": round(p.liq_distance_pct, 1),
        })
    sym = symbol or (positions[0]["symbol"] if positions else None)
    trig = br.fetch_trigger_orders(sym) if sym else []
    triggers = [(t.get("type"), t.get("side"), t.get("stopPrice")) for t in trig]
    return {"wallet_usdt": bal, "positions": positions, "triggers": triggers}
