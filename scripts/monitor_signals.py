#!/usr/bin/env python3
"""Long-running monitor for validated spot pair z-score signals.

Polls every cycle (default 300s). For each VALIDATED pair (from
data_pipe.spot_pairs.PAIR_BACKTEST_VERDICT), compute current z-score.

Alert when:
- z fresh crosses below -threshold → BUY_A signal
- z fresh crosses above +threshold → BUY_B signal
- z reverts to 0 from outside band → REVERT alert

Cross detection state in data/monitor_signals.state.json.

Telegram fires on cross (urgent).
Log to data/alerts.log + stdout.

Usage:
    python scripts/monitor_signals.py
    python scripts/monitor_signals.py --once
    python scripts/monitor_signals.py --threshold 1.8
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.spot_pairs import (  # noqa: E402
    PAIR_BACKTEST_VERDICT,
    VALIDATED_VERDICTS,
    compute_pair_zsignal,
)
from data_pipe.telegram import send_message  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
STATE_FILE = REPO_ROOT / "data" / "monitor_signals.state.json"
ALERT_LOG = REPO_ROOT / "data" / "alerts.log"

DEFAULT_THRESHOLD = 2.0
DEFAULT_CYCLE_SEC = 300


class ROStorage:
    def __init__(self, path):
        for _ in range(30):
            try:
                self._conn = duckdb.connect(str(path), read_only=True)
                return
            except Exception:
                time.sleep(2)
        raise RuntimeError("Could not acquire DB read lock after 60s")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


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


def classify_band(z: float, threshold: float) -> str:
    if z >= threshold:
        return "ABOVE_HIGH"
    if z <= -threshold:
        return "BELOW_LOW"
    return "INSIDE"


def _run_auto_trade_cycle(threshold: float) -> None:
    """Phase 4 auto-trade: invoke auto_trader.run_auto_cycle.

    Money-moving path: unit files may exist while disabled, so require a fresh
    runtime arm before any live auto-trade can execute.
    """
    import os
    if os.environ.get("AUTO_TRADE_ARM") != "1":
        log_alert("[auto_trade] BLOCKED — set AUTO_TRADE_ARM=1 only after explicit live re-arm")
        return
    import yaml
    from pathlib import Path as _Path
    from data_pipe.auto_trader import run_auto_cycle
    from data_pipe.brokers import BinanceSpotBroker
    from data_pipe.storage import Storage

    cfg_path = REPO_ROOT / "config" / "pairs.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    data_dir = REPO_ROOT / "data"

    # Storage needs WRITE lock (will retry kalau daemon active)
    try:
        storage = Storage(str(DB_PATH))
    except Exception as e:
        log_alert(f"[auto_trade] Storage lock failed: {e} — skip this cycle")
        return
    try:
        broker = BinanceSpotBroker()
        results = run_auto_cycle(storage, cfg, data_dir, broker)
        for r in results:
            dec = r.get("decision", {})
            action = dec.get("action", "?")
            reason = dec.get("reason", "")[:100]
            log_alert(f"[auto_trade] {action} — {reason}")
            for o in r.get("outputs", []):
                log_alert(f"           [{o.get('step')}] {o.get('action')} {o.get('symbol') or ''} {o.get('reason', '')[:80]}")
    finally:
        storage.close()


def cycle(threshold: float, state: dict, auto_trade: bool = False) -> dict:
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    storage = ROStorage(DB_PATH)
    try:
        for (pa, pb), verdict in PAIR_BACKTEST_VERDICT.items():
            sig = compute_pair_zsignal(storage, pa, pb, z_threshold=threshold)
            if sig is None:
                continue
            band = classify_band(sig.z, threshold)
            key = f"{pa}__{pb}"
            prev = (state.get(key) or {}).get("band", "INSIDE")

            # Always log status (succinct)
            verdict_tag = "VAL" if verdict in VALIDATED_VERDICTS else ("NEG" if verdict == "STABLE_NEGATIVE" else "INC")
            log_alert(
                f"[{now_s}] sig {pa}/{pb} z={sig.z:+.2f} band={band} verdict={verdict_tag}"
            )

            # Cross detection
            if band != prev and band != "INSIDE":
                # Fresh entry to outer band — this is the trigger
                target = pa if band == "BELOW_LOW" else pb
                other = pb if band == "BELOW_LOW" else pa
                conviction = "HIGH" if verdict in VALIDATED_VERDICTS else (
                    "ANTI-EDGE" if verdict == "STABLE_NEGATIVE" else "LOW"
                )

                if verdict == "STABLE_NEGATIVE":
                    msg_action = "AVOID — historical anti-edge"
                else:
                    msg_action = f"BUY {target} (sell-side won't enter — spot long-only)"

                log_alert(
                    f"[{now_s}] *** SIGNAL CROSS {pa}/{pb} z={sig.z:+.2f} → {band} "
                    f"verdict={verdict} conviction={conviction} action={msg_action} ***"
                )
                # Telegram
                send_message(
                    f"*[SPOT PAIR SIGNAL]* `{pa}` vs `{pb}`\n"
                    f"  z-score: `{sig.z:+.2f}` ({band})\n"
                    f"  backtest verdict: `{verdict}`\n"
                    f"  conviction: *{conviction}*\n"
                    f"  action: {msg_action}\n"
                    f"  cli: `python scripts/trade_live.py open {target} 6 "
                    f"--thesis \"z-signal {pa}/{pb} {sig.z:+.2f} {verdict}\" --protect --live`",
                    silent=False,
                )

            elif band == "INSIDE" and prev != "INSIDE":
                # Revert from outer band to inside → potential exit signal
                log_alert(
                    f"[{now_s}] *** SIGNAL REVERT {pa}/{pb} z={sig.z:+.2f} back to INSIDE ***"
                )
                send_message(
                    f"*[Z REVERT]* `{pa}` vs `{pb}` z={sig.z:+.2f} back inside band.\n"
                    f"If position open from earlier signal, consider closing.",
                    silent=True,
                )

            state[key] = {
                "pair_a": pa, "pair_b": pb,
                "z": float(sig.z), "band": band,
                "verdict": verdict,
                "last_check": now_s,
            }
    finally:
        # CLOSE read_only first — auto-trade pass needs WRITE lock
        storage.close()

    # Auto-trade pass — write Storage. Run AFTER ROStorage closed.
    if auto_trade:
        _run_auto_trade_cycle(threshold)

    return state


def main() -> int:
    p = argparse.ArgumentParser(description="Spot pair signal monitor")
    p.add_argument("--cycle", type=int, default=DEFAULT_CYCLE_SEC)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--once", action="store_true")
    p.add_argument("--auto-trade", action="store_true",
                   help="Phase 4: auto-execute decisions via auto_trader (REAL MONEY)")
    args = p.parse_args()

    log_alert(
        f"[monitor_signals] started cycle={args.cycle}s threshold=±{args.threshold} "
        f"auto_trade={args.auto_trade} "
        f"validated_pairs={[f'{a}-{b}' for (a,b),v in PAIR_BACKTEST_VERDICT.items() if v in VALIDATED_VERDICTS]}"
    )

    state = load_state()
    try:
        while True:
            try:
                state = cycle(args.threshold, state, auto_trade=args.auto_trade)
                save_state(state)
            except Exception as e:
                log_alert(f"[monitor_signals] cycle ERR: {type(e).__name__}: {e}")
            if args.once:
                return 0
            time.sleep(args.cycle)
    except KeyboardInterrupt:
        log_alert("[monitor_signals] stopped by user")
        return 0


if __name__ == "__main__":
    sys.exit(main())
