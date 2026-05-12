#!/usr/bin/env python3
"""One-off backward backfill — fetch historis yang lebih lama dari data existing.

Existing `backfill_symbol` hanya resume forward dari `latest_ts`. Script ini
langsung set `since = now - days` dan biarkan upsert idempotent handle overlap.
Dipakai saat lo butuh extend window (misal dari 30d → 90d).

Usage:
    python scripts/backfill_historical.py --days 90
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.storage import Storage
from data_pipe.exchanges import get_exchange


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"


def fetch_ohlcv_since(ex, symbol: str, timeframe: str, since_ms: int, tf_ms: int,
                      now_ms: int, limit: int = 1000) -> list[dict]:
    """Pagination loop, fetch dari since_ms ke now_ms."""
    rows = []
    cur = since_ms
    while cur < now_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cur, limit=limit)
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


def fetch_funding_since(ex, symbol: str, since_ms: int, now_ms: int, limit: int = 200) -> list[dict]:
    perp = symbol if ":" in symbol else f"{symbol}:{symbol.split('/')[-1]}"
    rows = []
    cur = since_ms
    while cur < now_ms:
        try:
            batch = ex.fetch_funding_rate_history(perp, since=cur, limit=limit)
        except Exception as e:
            print(f"  funding fetch err: {e}")
            break
        if not batch:
            break
        for b in batch:
            if b.get("fundingRate") is None:
                continue
            rows.append({
                "exchange": ex.id,
                "symbol": symbol,
                "ts": pd.Timestamp(b["timestamp"], unit="ms", tz="UTC").tz_convert(None),
                "funding": float(b["fundingRate"]),
                "mark_price": float(b.get("markPrice") or 0) or None,
            })
        last_ts = batch[-1]["timestamp"]
        if last_ts <= cur:
            break
        cur = last_ts + 1
        time.sleep(ex.rateLimit / 1000.0)
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--skip-funding", action="store_true")
    args = p.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    exchange_name = cfg["exchange"]
    tf = cfg["timeframe"]
    symbols = cfg["tier1"] + cfg["tier2"]

    tf_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[tf]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    ex = get_exchange(exchange_name)
    storage = Storage()
    try:
        for sym in symbols:
            print(f"\n=== {sym} ===")
            t0 = time.time()
            try:
                ohlcv_rows = fetch_ohlcv_since(ex, sym, tf, since_ms, tf_ms, now_ms)
                if ohlcv_rows:
                    storage.upsert_ohlcv(ohlcv_rows)
                print(f"  ohlcv: +{len(ohlcv_rows)} rows · {time.time()-t0:.1f}s")
            except Exception as e:
                print(f"  ohlcv ERR: {e}")

            if not args.skip_funding:
                t1 = time.time()
                try:
                    fund_rows = fetch_funding_since(ex, sym, since_ms, now_ms)
                    if fund_rows:
                        storage.upsert_funding(fund_rows)
                    print(f"  funding: +{len(fund_rows)} rows · {time.time()-t1:.1f}s")
                except Exception as e:
                    print(f"  funding ERR: {e}")

        print("\n--- Stats ---")
        for k, v in storage.stats().items():
            print(f"  {k}: {v}")
    finally:
        storage.close()


if __name__ == "__main__":
    main()
