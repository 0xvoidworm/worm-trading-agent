#!/usr/bin/env python3
"""Daily trading summary → Telegram. Cron-friendly.

Sends concise daily snapshot to operator's Telegram chat:
- Wallet snapshot + equity
- Today's PnL + benchmark vs BTC hold
- Open positions + OCO status
- Validated signal candidates currently outside band
- Current BTC regime + pairs allowed
- Service health (daemon last cycle, monitors alive)

Under 4096 char limit. Plain text format (no Markdown parse errors).

Usage:
    python scripts/daily_summary_telegram.py            # send now
    python scripts/daily_summary_telegram.py --dry      # print to stdout only

Cron (8 AM WIB = 01:00 UTC):
    0 1 * * * cd /home/<user>/trading_agent && \\
              /home/<user>/trading_agent/.venv/bin/python \\
              scripts/daily_summary_telegram.py \\
              >> /home/<user>/trading_agent/data/cron_daily.log 2>&1
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.telegram import send_message  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
CFG_PATH = REPO_ROOT / "config" / "pairs.yaml"
HEARTBEAT_PATH = REPO_ROOT / "data" / "daemon.heartbeat"


class ROStorage:
    def __init__(self, path):
        import time
        for _ in range(30):
            try:
                self._conn = duckdb.connect(str(path), read_only=True)
                return
            except Exception:
                time.sleep(2)
        raise RuntimeError("DB lock")
    def close(self):
        try: self._conn.close()
        except Exception: pass


def fetch_wallet_total(broker) -> tuple[float, int]:
    """Return (total_usd, n_assets)."""
    try:
        bal = broker.fetch_spot_balance()
        total = 0.0
        for asset, amount in bal.items():
            if asset == "USDT":
                total += amount
            else:
                try:
                    tk = broker.get_ticker(f"{asset}/USDT")
                    total += amount * float(tk.get("last") or 0)
                except Exception:
                    pass
        return total, len(bal)
    except Exception as e:
        return 0.0, 0


def daemon_status() -> str:
    if not HEARTBEAT_PATH.exists():
        return "UNKNOWN"
    try:
        hb = yaml.safe_load(HEARTBEAT_PATH.read_text())
        last_str = hb.get("last_cycle_ts")
        if not last_str:
            return "UNKNOWN"
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age_min < 10:
            return f"ALIVE ({age_min:.0f}m ago)"
        if age_min < 60:
            return f"STALE ({age_min:.0f}m ago)"
        return f"DEAD ({age_min/60:.1f}h ago)"
    except Exception:
        return "ERROR"


def build_summary() -> str:
    from data_pipe.analytics import (
        compute_all_windows, portfolio_snapshot, fetch_open_trades,
    )
    from data_pipe.benchmark import compute_benchmark
    from data_pipe.brokers import BinanceSpotBroker
    from data_pipe.regime_hmm import get_current_regime
    from data_pipe.spot_pairs import (
        PAIR_BACKTEST_VERDICT, VALIDATED_VERDICTS, is_pair_allowed_in_regime,
        compute_pair_zsignal,
    )

    cfg = yaml.safe_load(CFG_PATH.read_text()) or {}
    storage = ROStorage(DB_PATH)
    try:
        snapshot = portfolio_snapshot(storage, cfg)
        windows = compute_all_windows(storage)
        benchmark = compute_benchmark(storage, snapshot["starting_capital"])
        open_df = fetch_open_trades(storage)
        regime_snap = get_current_regime(storage, use_cached=True)
        # Validated signals state
        validated_states = []
        for (pa, pb), v in PAIR_BACKTEST_VERDICT.items():
            if v not in VALIDATED_VERDICTS:
                continue
            sig = compute_pair_zsignal(storage, pa, pb)
            if sig is None:
                continue
            allowed = (regime_snap and is_pair_allowed_in_regime(pa, pb, regime_snap.regime))
            validated_states.append({
                "pair": f"{pa}/{pb}".replace("/USDT", ""),
                "z": sig.z,
                "band": sig.direction,
                "allowed": allowed,
            })
    finally:
        storage.close()

    try:
        broker = BinanceSpotBroker()
        wallet_usd, n_assets = fetch_wallet_total(broker)
    except Exception:
        wallet_usd, n_assets = 0.0, 0

    # Build summary
    now_wib = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M WIB")
    lines = []
    lines.append(f"=== TRADING AGENT DAILY ===")
    lines.append(f"{now_wib}")
    lines.append(f"")
    lines.append(f"[WALLET]")
    lines.append(f"  Total: ${wallet_usd:.2f} ({n_assets} assets)")
    lines.append(f"  Starting cap: ${snapshot['starting_capital']:.2f}")
    lines.append(f"  Equity realized: ${snapshot['equity_realized']:.4f}")
    lines.append(f"  Unrealized: {snapshot['unrealized_pnl']:+.4f}")
    lines.append(f"  Drawdown: {snapshot['drawdown_pct_vs_start']:.2f}% (floor 50%)")
    lines.append(f"")

    # Today + 7d PnL
    today = windows.get("24h")
    w7d = windows.get("7d")
    lines.append(f"[PERFORMANCE]")
    if today:
        lines.append(f"  24h: {today.n_closed} closed, PnL {today.total_pnl:+.4f} (wr {today.win_rate_pct:.0f}%)")
    if w7d:
        lines.append(f"  7d:  {w7d.n_closed} closed, PnL {w7d.total_pnl:+.4f} (wr {w7d.win_rate_pct:.0f}%)")

    # Benchmark vs BTC
    if benchmark:
        d = benchmark.to_dict()
        verdict = "BEAT BTC" if d['alpha_pct'] > 0 else ("LOSE BTC" if d['alpha_pct'] < -1 else "~flat")
        lines.append(f"  Agent: {d['agent_return_pct']:+.2f}%  BTC: {d['btc_return_pct']:+.2f}%")
        lines.append(f"  ALPHA: {d['alpha_pct']:+.2f}% ({verdict})")
    lines.append(f"")

    # Open positions
    lines.append(f"[POSITIONS] {len(open_df)} open")
    if not open_df.empty:
        try:
            broker = BinanceSpotBroker()
            for _, row in open_df.iterrows():
                pair = str(row["pair"])
                entry = float(row["entry_price"]) if row["entry_price"] else 0
                size = float(row["size"]) if row["size"] else 0
                try:
                    tk = broker.get_ticker(pair)
                    cur = float(tk.get("last") or 0)
                    pct = ((cur - entry) / entry * 100) if entry else 0
                    unrealized = (cur - entry) * size
                    lines.append(f"  #{row['id']} {pair} {pct:+.2f}% ({unrealized:+.4f})")
                except Exception:
                    lines.append(f"  #{row['id']} {pair} (price fetch err)")
        except Exception:
            pass
    lines.append(f"")

    # Regime + signal state
    if regime_snap:
        lines.append(f"[REGIME] BTC: {regime_snap.regime} ({regime_snap.confidence:.0%})")
    if validated_states:
        validated_states.sort(key=lambda s: -abs(s["z"]))
        # Show top 5 + any outside band
        outside = [s for s in validated_states if s["band"] != "NEUTRAL"]
        if outside:
            lines.append(f"  >> SIGNAL CROSS detected:")
            for s in outside:
                gate = "OK" if s["allowed"] else "BLOCKED"
                lines.append(f"    {s['pair']} z={s['z']:+.2f} ({gate})")
        lines.append(f"  Top 3 z-scores (validated):")
        for s in validated_states[:3]:
            gate = "✓" if s["allowed"] else "✗regime"
            lines.append(f"    {s['pair']} z={s['z']:+.2f} {gate}")
    lines.append(f"")

    # Service health
    lines.append(f"[SERVICES]")
    lines.append(f"  daemon: {daemon_status()}")

    text = "\n".join(lines)
    # Truncate to 4096 char Telegram limit
    if len(text) > 4000:
        text = text[:3990] + "\n... (truncated)"
    return text


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry", action="store_true", help="print to stdout only")
    args = p.parse_args()

    summary = build_summary()
    if args.dry:
        print(summary)
        return 0

    print(summary)
    print()
    ok = send_message(summary, parse_mode=None, silent=False)
    print(f"telegram delivered: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
