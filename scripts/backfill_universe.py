#!/usr/bin/env python3
"""Universe expansion backfill — extend OHLCV coverage beyond config.tier1+2.

Takes explicit symbol list. Skips funding (spot-only operation).

Usage:
    python scripts/backfill_universe.py                    # default 18-symbol list
    python scripts/backfill_universe.py --days 365         # custom history
    python scripts/backfill_universe.py --symbols ADA,DOT,XYZ  # custom symbols
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.exchanges import get_exchange
from data_pipe.storage import Storage

# 18 selected symbols (Bybit perp tradeable, diverse sectors)
EXPANSION_SYMBOLS = [
    "ADA/USDT", "DOT/USDT", "TON/USDT", "SUI/USDT", "APT/USDT", "NEAR/USDT",
    "STX/USDT", "POL/USDT",
    "TAO/USDT", "FET/USDT", "WLD/USDT",
    "UNI/USDT", "AAVE/USDT", "LINK/USDT",
    "LDO/USDT",
    "ONDO/USDT",
    "DOGE/USDT", "PEPE/USDT",
]


def fetch_ohlcv_since(ex, symbol, timeframe, since_ms, tf_ms, now_ms, limit=1000):
    rows = []
    cur = since_ms
    while cur < now_ms:
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe, since=cur, limit=limit)
        except Exception as e:
            print(f"  fetch err: {type(e).__name__}: {e}")
            time.sleep(2)
            return rows
        if not batch:
            break
        for b in batch:
            rows.append({
                "exchange": ex.id,
                "symbol": symbol,
                "timeframe": timeframe,
                "ts": pd.Timestamp(b[0], unit="ms", tz="UTC").tz_convert(None),
                "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5],
            })
        last_ts = batch[-1][0]
        if last_ts <= cur:
            break
        cur = last_ts + tf_ms
        time.sleep(ex.rateLimit / 1000.0)
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=1095, help="history depth (default 3y)")
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--symbols", default=None, help="comma-separated override")
    p.add_argument("--exchange", default="bybit")
    args = p.parse_args()

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else EXPANSION_SYMBOLS
    )

    tf_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[args.timeframe]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    print(f"[backfill_universe] {len(symbols)} symbols x {args.days}d {args.timeframe} from {args.exchange}")

    ex = get_exchange(args.exchange)
    storage = Storage()
    grand_total = 0
    try:
        for i, sym in enumerate(symbols, 1):
            print(f"\n[{i}/{len(symbols)}] {sym}")
            t0 = time.time()
            rows = fetch_ohlcv_since(ex, sym, args.timeframe, since_ms, tf_ms, now_ms)
            if rows:
                storage.upsert_ohlcv(rows)
                grand_total += len(rows)
            elapsed = time.time() - t0
            print(f"  +{len(rows):,} rows in {elapsed:.1f}s")

        print(f"\n[backfill_universe] DONE - {grand_total:,} total rows")
    finally:
        storage.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
