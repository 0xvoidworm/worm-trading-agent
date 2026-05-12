"""Funding rate ingest untuk perpetual swaps.

Bybit/Binance return funding rate history via fetch_funding_rate_history().
Interval biasanya 8 jam. Rate dalam decimal (0.0001 = 0.01% per 8h).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from rich.console import Console

from .exchanges import get_exchange
from .retry import retry_network
from .storage import Storage


console = Console()


def backfill_funding(
    storage: Storage,
    exchange_name: str,
    symbol: str,
    days: int = 60,
    batch_limit: int = 200,
) -> int:
    ex = get_exchange(exchange_name)
    if not ex.has.get("fetchFundingRateHistory"):
        console.print(f"[yellow]{exchange_name} no fundingRateHistory support[/yellow]")
        return 0

    # Convert unified symbol to perp format expected by fetchFundingRateHistory.
    # Bybit/Binance USDT-linear perps use "BASE/USDT:USDT" notation in ccxt.
    perp_symbol = symbol if ":" in symbol else f"{symbol}:{symbol.split('/')[-1]}"

    last = storage.latest_funding_ts(exchange_name, symbol)
    if last is not None:
        since = int(pd.Timestamp(last).timestamp() * 1000) + 1
    else:
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    total = 0

    while since < now_ms:
        try:
            batch = retry_network(
                lambda: ex.fetch_funding_rate_history(perp_symbol, since=since, limit=batch_limit),
                label=f"funding {symbol}",
            )
        except Exception as e:
            # Network exhausted OR business error — either way, stop untuk symbol
            # ini. Caller sudah log warn dan lanjut.
            console.print(f"[yellow]funding fetch err {symbol}: {e}[/yellow]")
            break

        if not batch:
            break

        rows = [
            {
                "exchange": exchange_name,
                "symbol": symbol,
                "ts": pd.Timestamp(b["timestamp"], unit="ms", tz="UTC").tz_convert(None),
                "funding": float(b["fundingRate"]),
                "mark_price": float(b.get("markPrice") or 0) or None,
            }
            for b in batch
            if b.get("fundingRate") is not None
        ]
        storage.upsert_funding(rows)
        total += len(rows)

        last_ts = batch[-1]["timestamp"]
        if last_ts <= since:
            break
        since = last_ts + 1
        time.sleep(ex.rateLimit / 1000.0)

    console.log(f"[green]{symbol} funding: +{total} rows[/green]")
    return total
