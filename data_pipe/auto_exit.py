"""Auto-exit advisor — deterministic EXIT verdict dari trade OPEN.

4 trigger, urut prioritas (paling dulu fire menang per-trade):
    A. Stop-loss   — unrealized_pnl_pct ≤ -SL_PCT
    B. Regime-flip — REGIME_CHANGE terbaru utk pair (≤ window_hours) arah
                     OPPOSITE dari trade dan t-stat ≥ EXIT_REGIME_T (6.0).
                     Threshold lebih rendah dari ENTRY (8.0) — exits move
                     faster than entries.
    C. Take-profit — unrealized_pnl_pct ≥ TP_PCT
    D. Time-stop   — age trade ≥ MAX_HOLD_HOURS

Cross-trade resolution (kalau >1 trade fire):
  Sort (priority asc, |pnl_pct| desc) → ambil top.

PAIR direction & entry_price=None di-skip (tidak ada PnL semantic yg bisa
diandalkan). Caller boleh exit manual via journal CLI.

Output schema sama dengan auto_advisor:
    {summary, verdict="EXIT", trade_id, thesis}
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .auto_advisor import _latest_close, _regime_direction, parse_confidence
from .storage import Storage


# Defaults; override via cfg["exits"]
DEFAULT_SL_PCT = 5.0
DEFAULT_TP_PCT = 5.0
DEFAULT_MAX_HOLD_HOURS = 72
DEFAULT_REGIME_T = 6.0
DEFAULT_WINDOW_HOURS = 4

# Lower = higher priority.
_PRI_SL = 0
_PRI_REGIME = 1
_PRI_TP = 2
_PRI_TIME = 3


def _opposite(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


def _summary_from(trigger: str, direction: str) -> str:
    """Map trigger+direction → market summary label.

    SL/REGIME = bukti arah berbalik → bias OPPOSITE direction.
    TP        = arah trade tercapai → bias SAME direction.
    TIME      = no conviction either way → Chaos.
    """
    if trigger == "TIME":
        return "Chaos"
    if trigger == "TP":
        return "Bullish" if direction == "LONG" else "Bearish"
    # SL atau REGIME
    return "Bearish" if direction == "LONG" else "Bullish"


def _evaluate_trade(
    storage: Storage,
    trade_row,
    cfg: dict,
    exits_cfg: dict,
    now_utc: pd.Timestamp,
) -> Optional[dict]:
    """Evaluate satu trade. Return dict trigger info atau None.

    Trigger order = prioritas; first-hit menang per-trade.
    """
    direction = trade_row["direction"]
    if direction not in ("LONG", "SHORT"):
        return None  # PAIR di-skip
    entry = trade_row["entry_price"]
    if entry is None or pd.isna(entry):
        return None  # tidak bisa hitung PnL tanpa entry

    pair = str(trade_row["pair"])
    trade_id = int(trade_row["id"])
    open_ts = pd.Timestamp(trade_row["ts"])

    exchange = cfg.get("exchange", "bybit")
    tf = cfg.get("timeframe", "1h")
    last_close = _latest_close(storage, exchange, pair, tf)

    sign = 1 if direction == "LONG" else -1
    pnl_pct: Optional[float] = None
    if last_close is not None:
        pnl_pct = ((float(last_close) - float(entry)) / float(entry)) * 100.0 * sign

    age_h = (now_utc - open_ts).total_seconds() / 3600.0

    # ── Trigger A — SL ──────────────────────────────────────────────
    sl_pct = float(exits_cfg.get("stop_loss_pct", DEFAULT_SL_PCT))
    if pnl_pct is not None and pnl_pct <= -sl_pct:
        return {
            "trade_id": trade_id, "direction": direction, "pair": pair,
            "trigger": "SL", "priority": _PRI_SL,
            "pnl_pct": pnl_pct,
            "reason": f"SL hit {pnl_pct:+.2f}%",
        }

    # ── Trigger B — regime flip ─────────────────────────────────────
    win_h = int(exits_cfg.get("window_hours", DEFAULT_WINDOW_HOURS))
    rt = float(exits_cfg.get("regime_flip_t_threshold", DEFAULT_REGIME_T))
    sig_df = storage.get_signals(
        limit=10, event_type="REGIME_CHANGE", symbol=pair, since_hours=win_h,
    )
    if not sig_df.empty:
        latest = sig_df.iloc[0]  # get_signals returns DESC ts
        latest_payload = str(latest.get("payload") or "")
        latest_dir = _regime_direction(latest_payload)
        latest_t = parse_confidence(latest_payload, "REGIME_CHANGE")
        if (
            latest_dir is not None
            and latest_dir == _opposite(direction)
            and latest_t is not None
            and latest_t >= rt
        ):
            new_label = "trending_down" if latest_dir == "SHORT" else "trending_up"
            return {
                "trade_id": trade_id, "direction": direction, "pair": pair,
                "trigger": "REGIME", "priority": _PRI_REGIME,
                "pnl_pct": pnl_pct if pnl_pct is not None else 0.0,
                "reason": f"regime flipped to {new_label} t={latest_t:.2f}",
            }

    # ── Trigger C — TP ──────────────────────────────────────────────
    tp_pct = float(exits_cfg.get("take_profit_pct", DEFAULT_TP_PCT))
    if pnl_pct is not None and pnl_pct >= tp_pct:
        return {
            "trade_id": trade_id, "direction": direction, "pair": pair,
            "trigger": "TP", "priority": _PRI_TP,
            "pnl_pct": pnl_pct,
            "reason": f"TP hit {pnl_pct:+.2f}%",
        }

    # ── Trigger D — time-stop ───────────────────────────────────────
    max_h = float(exits_cfg.get("max_hold_hours", DEFAULT_MAX_HOLD_HOURS))
    if age_h >= max_h:
        return {
            "trade_id": trade_id, "direction": direction, "pair": pair,
            "trigger": "TIME", "priority": _PRI_TIME,
            "pnl_pct": pnl_pct if pnl_pct is not None else 0.0,
            "reason": f"time-stop {age_h:.1f}h ≥ {max_h:.0f}h",
        }

    return None


def check_exits(
    storage: Storage,
    cfg: dict,
    now_utc: datetime | None = None,
) -> Optional[dict]:
    """Cek semua OPEN trade. Return EXIT verdict utk top-priority hit, atau None."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    elif getattr(now_utc, "tzinfo", None) is not None:
        now_utc = now_utc.replace(tzinfo=None)
    now_ts = pd.Timestamp(now_utc)

    exits_cfg = cfg.get("exits") or {}

    open_df = storage.get_trades(status="OPEN")
    if open_df.empty:
        return None

    fired: list[dict] = []
    for _, row in open_df.iterrows():
        ev = _evaluate_trade(storage, row, cfg, exits_cfg, now_ts)
        if ev:
            fired.append(ev)

    if not fired:
        return None

    # priority asc, |pnl_pct| desc
    fired.sort(key=lambda e: (e["priority"], -abs(e["pnl_pct"])))
    best = fired[0]

    summary = _summary_from(best["trigger"], best["direction"])
    thesis = (
        f"EXIT trade #{best['trade_id']} {best['pair']} {best['direction']}: "
        f"{best['reason']}"
    )[:200]

    return {
        "summary": summary,
        "verdict": "EXIT",
        "trade_id": int(best["trade_id"]),
        "thesis": thesis,
    }
