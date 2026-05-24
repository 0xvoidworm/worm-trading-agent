#!/usr/bin/env python3
"""Performance analytics CLI.

Usage:
    python scripts/analytics.py                  # full report
    python scripts/analytics.py --json           # JSON output
    python scripts/analytics.py --window 7d      # single window only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
CFG_PATH = REPO_ROOT / "config" / "pairs.yaml"


class ROStorage:
    def __init__(self, path):
        for _ in range(20):
            try:
                self._conn = duckdb.connect(str(path), read_only=True); return
            except Exception:
                import time; time.sleep(2)
        raise RuntimeError("Could not acquire DB read lock")
    def close(self):
        self._conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Performance analytics")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--window", choices=["all", "24h", "7d", "30d"], help="single window")
    p.add_argument("--equity-curve", action="store_true", help="print equity curve table")
    args = p.parse_args()

    from data_pipe.analytics import (
        compute_all_windows, compute_equity_curve,
        fetch_closed_trades, fetch_open_trades, portfolio_snapshot,
    )

    cfg = yaml.safe_load(CFG_PATH.read_text()) or {}
    storage = ROStorage(DB_PATH)
    try:
        windows = compute_all_windows(storage)
        snapshot = portfolio_snapshot(storage, cfg)
        equity = compute_equity_curve(storage, snapshot["starting_capital"])
        open_df = fetch_open_trades(storage)
    finally:
        storage.close()

    if args.json:
        out = {
            "snapshot": snapshot,
            "windows": {k: v.as_dict() for k, v in windows.items()},
            "open_trades": len(open_df),
            "equity_curve_points": len(equity),
        }
        print(json.dumps(out, indent=2, default=str))
        return 0

    print()
    print("=" * 70)
    print("PORTFOLIO SNAPSHOT")
    print("=" * 70)
    for k, v in snapshot.items():
        print(f"  {k:>28s}: {v}")

    print()
    print("=" * 70)
    print(f"OPEN POSITIONS ({len(open_df)})")
    print("=" * 70)
    if open_df.empty:
        print("  (no open trades)")
    else:
        for _, row in open_df.iterrows():
            notional = float(row["entry_price"]) * float(row["size"])
            print(f"  #{row['id']:>3} {row['pair']:<14s} {row['direction']:<5s} "
                  f"entry={row['entry_price']:.6g} size={row['size']:.6g} "
                  f"notional=${notional:.4f}")

    windows_to_show = [args.window] if args.window else ["all", "30d", "7d", "24h"]
    for wlabel in windows_to_show:
        w = windows.get(wlabel)
        if w is None:
            continue
        print()
        print("=" * 70)
        print(f"WINDOW: {wlabel.upper()}")
        print("=" * 70)
        d = w.as_dict()
        if d["n_closed"] == 0:
            print("  (no closed trades in this window)")
            continue
        print(f"  closed         : {d['n_closed']}")
        print(f"  wins / losses  : {d['n_wins']} / {d['n_losses']}  ({d['win_rate_pct']:.1f}% win rate)")
        print(f"  total PnL      : {d['total_pnl']:+.4f} USDT")
        print(f"  avg PnL/trade  : {d['avg_pnl']:+.4f}")
        print(f"  median PnL     : {d['median_pnl']:+.4f}")
        print(f"  best / worst   : {d['best_pnl']:+.4f} / {d['worst_pnl']:+.4f}")
        print(f"  avg hold       : {d['avg_hold_hours']:.2f}h")
        if d["by_symbol"]:
            print(f"  by symbol      :")
            for sym, pnl in sorted(d["by_symbol"].items(), key=lambda x: -x[1]):
                print(f"     {sym:<14s} {pnl:+.4f}")
        if d["by_kind"]:
            print(f"  by kind        :")
            for kind, pnl in sorted(d["by_kind"].items(), key=lambda x: -x[1]):
                print(f"     {kind:<14s} {pnl:+.4f}")

    if args.equity_curve:
        print()
        print("=" * 70)
        print(f"EQUITY CURVE (last 20 points)")
        print("=" * 70)
        if equity.empty:
            print("  (no closed trades yet)")
        else:
            for _, row in equity.tail(20).iterrows():
                ts = row["exit_ts"]
                ts_s = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
                print(f"  {ts_s}  {row['pair']:<14s} pnl={row['pnl']:+.4f}  cum={row['cum_pnl']:+.4f}  eq=${row['equity']:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
