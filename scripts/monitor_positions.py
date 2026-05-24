#!/usr/bin/env python3
"""Price alert monitor for open spot positions.

Poll loop:
- Read open positions from trade_journal (read_only; coexist with daemon writer)
- Fetch live ticker via ccxt
- Compute % vs entry
- Alert on TAKE_PROFIT / STOP_LOSS / TIME_STOP (signal change → loud)
- Write status + alert to data/alerts.log + stdout

Thresholds from config/pairs.yaml `exits` block:
  take_profit_pct  : default 5.0
  stop_loss_pct    : default 5.0
  max_hold_hours   : default 72

Usage:
    python scripts/monitor_positions.py            # loop, cycle 60s
    python scripts/monitor_positions.py --once     # single cycle then exit
    python scripts/monitor_positions.py --cycle 30 # custom interval

Background:
    nohup python scripts/monitor_positions.py > /dev/null 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.brokers import BinanceSpotBroker  # noqa: E402
from data_pipe.telegram import notify_trade  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
CFG_PATH = REPO_ROOT / "config" / "pairs.yaml"
ALERT_LOG = REPO_ROOT / "data" / "alerts.log"
STATE_FILE = REPO_ROOT / "data" / "monitor.state.json"


def fetch_open_positions(max_retries: int = 30, retry_sleep: float = 2.0) -> list[dict] | None:
    """Read open positions. Retry on DB lock contention (daemon write cycle).

    Returns None if can't acquire read lock after max_retries (caller skips cycle).
    """
    last_err = None
    for _ in range(max_retries):
        try:
            conn = duckdb.connect(str(DB_PATH), read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT id, pair, entry_price, size, ts
                    FROM trade_journal
                    WHERE status='OPEN'
                      AND direction IN ('LONG','SHORT')
                      AND entry_price IS NOT NULL AND size IS NOT NULL
                    ORDER BY ts
                    """
                ).fetchall()
            finally:
                conn.close()
            return [
                {
                    "id": int(r[0]),
                    "pair": str(r[1]),
                    "entry_price": float(r[2]),
                    "size": float(r[3]),
                    "ts": r[4],
                }
                for r in rows
            ]
        except duckdb.IOException as e:
            last_err = e
            time.sleep(retry_sleep)
    log_alert(f"[monitor] DB lock contention persists: {last_err}")
    return None


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def log_alert(line: str) -> None:
    print(line, flush=True)
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG.open("a") as f:
        f.write(line + "\n")


def cycle(broker: BinanceSpotBroker, cfg: dict, state: dict) -> dict:
    exits = cfg.get("exits") or {}
    tp_pct = float(exits.get("take_profit_pct", 5.0))
    sl_pct = float(exits.get("stop_loss_pct", 5.0))
    max_hold_h = float(exits.get("max_hold_hours", 72))

    positions = fetch_open_positions()
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    if positions is None:
        # Lock contention couldn't be resolved this cycle — skip, retry next
        return state
    if not positions:
        # Don't spam log if no positions — only log every 30 minutes
        last_idle = state.get("_last_idle_log")
        if not last_idle or (datetime.now(timezone.utc) - datetime.fromisoformat(last_idle)).total_seconds() > 1800:
            log_alert(f"[{now_s}] no open positions")
            state["_last_idle_log"] = datetime.now(timezone.utc).isoformat()
        return state

    for pos in positions:
        try:
            tk = broker.get_ticker(pos["pair"])
            price = float(tk.get("last") or 0)
        except Exception as e:
            log_alert(f"[{now_s}] ERR fetch {pos['pair']}: {type(e).__name__}: {e}")
            continue

        entry = pos["entry_price"]
        if entry <= 0:
            continue
        pct = (price - entry) / entry * 100
        unrealized = (price - entry) * pos["size"]

        ts = pos["ts"]
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600

        signal = "NONE"
        if pct >= tp_pct:
            signal = "TAKE_PROFIT"
        elif pct <= -sl_pct:
            signal = "STOP_LOSS"
        elif age_h >= max_hold_h:
            signal = "TIME_STOP"

        key = f"trade_{pos['id']}"
        prev_signal = (state.get(key) or {}).get("last_signal", "NONE")

        log_alert(
            f"[{now_s}] #{pos['id']} {pos['pair']:<14s} entry={entry:.4f} "
            f"now={price:.4f} pct={pct:+.2f}% unreal={unrealized:+.4f} "
            f"age={age_h:.1f}h sig={signal}"
        )

        if signal != "NONE" and signal != prev_signal:
            log_alert(
                f"[{now_s}] *** ALERT #{pos['id']} {pos['pair']} {signal}: "
                f"pct={pct:+.2f}% tp={tp_pct}% sl=-{sl_pct}% hold>={max_hold_h}h "
                f"-- consider: python scripts/trade_live.py close {pos['id']} --live ***"
            )
            # Fire Telegram alert (silent no-op if not configured)
            notify_trade(
                action=signal, symbol=pos["pair"],
                details={
                    "trade_id": pos["id"],
                    "entry": f"{entry:.6g}",
                    "now": f"{price:.6g}",
                    "pct": f"{pct:+.2f}%",
                    "unrealized": f"{unrealized:+.4f} USDT",
                    "age_h": f"{age_h:.1f}",
                    "action": f"close {pos['id']} --live OR check OCO",
                },
                urgent=True,
            )

        state[key] = {
            "pair": pos["pair"],
            "entry": entry,
            "last_price": price,
            "last_pct": pct,
            "last_unrealized": unrealized,
            "last_signal": signal,
            "last_check": now_s,
            "age_h": age_h,
        }

    return state


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Price alert monitor for open spot positions."
    )
    parser.add_argument("--cycle", type=int, default=60, help="poll interval seconds (default 60)")
    parser.add_argument("--once", action="store_true", help="single cycle then exit")
    args = parser.parse_args()

    cfg = yaml.safe_load(CFG_PATH.read_text()) or {}
    broker = BinanceSpotBroker()
    state = load_state()

    exits = cfg.get("exits") or {}
    log_alert(
        f"[monitor] started cycle={args.cycle}s "
        f"tp={exits.get('take_profit_pct', 5)}% "
        f"sl={exits.get('stop_loss_pct', 5)}% "
        f"max_hold={exits.get('max_hold_hours', 72)}h"
    )

    try:
        while True:
            state = cycle(broker, cfg, state)
            save_state(state)
            if args.once:
                return 0
            time.sleep(args.cycle)
    except KeyboardInterrupt:
        log_alert("[monitor] stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
