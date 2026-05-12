#!/usr/bin/env python3
"""Backfill historical OHLCV + funding rate.

Usage:
    python scripts/backfill.py                # pakai config default
    python scripts/backfill.py --days 90      # override history window
    python scripts/backfill.py --symbol BTC/USDT  # 1 symbol saja
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Allow running via `python scripts/backfill.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage
from data_pipe.ingest_ohlcv import backfill_symbol
from data_pipe.ingest_funding import backfill_funding


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=None, help="days of history")
    parser.add_argument("--symbol", type=str, default=None, help="single symbol override")
    parser.add_argument("--skip-funding", action="store_true")
    parser.add_argument("--skip-ohlcv", action="store_true")
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    days = args.days or cfg.get("history_days", 60)
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    symbols = [args.symbol] if args.symbol else (cfg["tier1"] + cfg["tier2"])

    storage = Storage()
    try:
        for sym in symbols:
            print(f"\n=== {sym} ===")
            if not args.skip_ohlcv:
                try:
                    backfill_symbol(storage, exchange, sym, tf, days=days)
                except Exception as e:
                    print(f"  ohlcv error: {e}")
            if not args.skip_funding:
                try:
                    backfill_funding(storage, exchange, sym, days=days)
                except Exception as e:
                    print(f"  funding error: {e}")

        print("\n--- Stats ---")
        stats = storage.stats()
        for k, v in stats.items():
            print(f"  {k}: {v}")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
