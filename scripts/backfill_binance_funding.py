"""Step 20 — Backfill Binance perp funding rate history.

Self-contained one-shot script. Tidak mengubah production ingest_funding
(Bybit-specific). Writes ke same `funding_rate` table dengan exchange='binance'.

Binance USDT-M perp pakai `defaultType=future` (bukan 'swap' yang Bybit pakai).
Symbols mapped: BTC/USDT etc → BTCUSDT.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402


DEFAULT_DB = ROOT / "data" / "market.duckdb"


def fetch_binance_funding_paginated(
    ex: ccxt.Exchange,
    symbol: str,
    since_ms: int,
    end_ms: int,
    batch_limit: int = 1000,
) -> list[dict]:
    """Fetch funding history paginated. Binance returns max 1000/call.

    `since` cursor advances based on last fundingTime + 1ms to avoid dedup.
    """
    rows: list[dict] = []
    cursor = since_ms
    while cursor < end_ms:
        try:
            batch = ex.fetchFundingRateHistory(
                symbol, since=cursor, limit=batch_limit,
            )
        except Exception as e:
            print(f"  ERROR {symbol} at {cursor}: {e}", file=sys.stderr)
            time.sleep(2)
            continue
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1].get("timestamp")
        if last_ts is None or last_ts <= cursor:
            break
        cursor = last_ts + 1
        # Stop if we've reached end
        if last_ts >= end_ms:
            break
        time.sleep(ex.rateLimit / 1000.0 if hasattr(ex, "rateLimit") else 0.1)
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description="Backfill Binance perp funding rate history")
    p.add_argument("--days", type=int, default=1095, help="History days (default 3y)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="all", help="comma-separated or 'all'")
    args = p.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "pairs.yaml").read_text())
    if args.symbols == "all":
        symbols = list(cfg.get("tier1", [])) + list(cfg.get("tier2", []))
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]

    print(f"[binance-backfill] symbols: {symbols}")
    print(f"[binance-backfill] history: {args.days} days")

    ex = ccxt.binance({
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {"defaultType": "future"},
    })
    ex.load_markets()
    print(f"[binance-backfill] markets loaded")

    storage = Storage(db_path=Path(args.db), read_only=False)

    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=args.days)
    since_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)

    total_rows = 0
    for sym in symbols:
        # Binance perp symbol = `BTCUSDT` style or CCXT unified BTC/USDT:USDT
        # CCXT accepts unified BTC/USDT for futures with defaultType=future
        print(f"[binance-backfill] {sym} ...")
        t0 = time.time()
        try:
            raw_rows = fetch_binance_funding_paginated(
                ex, sym, since_ms, end_ms, batch_limit=1000,
            )
        except Exception as e:
            print(f"  failed: {e}")
            continue

        upsert_rows = []
        for r in raw_rows:
            ts_ms = r.get("timestamp")
            rate = r.get("fundingRate")
            mark = r.get("info", {}).get("markPrice")
            if ts_ms is None or rate is None:
                continue
            try:
                mark_f = float(mark) if mark is not None else None
            except (ValueError, TypeError):
                mark_f = None
            upsert_rows.append({
                "exchange": "binance",
                "symbol": sym,
                "ts": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None),
                "funding": float(rate),
                "mark_price": mark_f,
            })

        n = storage.upsert_funding(upsert_rows)
        total_rows += n
        elapsed = time.time() - t0
        print(f"  +{n} rows ({len(raw_rows)} fetched) · {elapsed:.1f}s")

    storage.close()
    print(f"[binance-backfill] DONE — total {total_rows} rows upserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
