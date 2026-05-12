"""Historical OHLCV ingest via ccxt.

Loop fetch dengan pagination karena ccxt batch max 1000 bar. Kita kejar dari
`since` sampai sekarang, chunk by chunk, simpan ke DuckDB idempotent.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd
from rich.console import Console

from .exchanges import get_exchange
from .retry import retry_network
from .storage import Storage

# ExchangeError (4xx/business) tidak di-retry — caller salah argumen. NetworkError
# sudah dihandle di retry_network layer.
_CCXT_BIZ_ERRORS = (ccxt.ExchangeError,)


console = Console()


_TF_TO_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def backfill_symbol(
    storage: Storage,
    exchange_name: str,
    symbol: str,
    timeframe: str = "1h",
    days: int = 60,
    batch_limit: int = 1000,
) -> int:
    """Fetch historical OHLCV and upsert. Returns rows written."""
    ex = get_exchange(exchange_name)
    if not ex.has.get("fetchOHLCV"):
        raise RuntimeError(f"{exchange_name} does not support fetchOHLCV")

    tf_ms = _TF_TO_MS[timeframe]
    # Resume from last stored ts kalau ada, else go back `days`
    last = storage.latest_ohlcv_ts(exchange_name, symbol, timeframe)
    if last is not None:
        since = int(pd.Timestamp(last).timestamp() * 1000) + tf_ms
    else:
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    total = 0

    while since < now_ms:
        try:
            batch = retry_network(
                lambda: ex.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_limit),
                label=f"ohlcv {symbol}",
            )
        except _CCXT_BIZ_ERRORS as e:
            # 4xx/business error — bukan urusan retry. Stop symbol ini, caller
            # (daemon) sudah log warn dan lanjut symbol berikutnya.
            console.print(f"[yellow]fetch error {symbol}: {e}[/yellow]")
            raise

        if not batch:
            break

        rows = [
            {
                "exchange": exchange_name,
                "symbol": symbol,
                "timeframe": timeframe,
                "ts": pd.Timestamp(b[0], unit="ms", tz="UTC").tz_convert(None),
                "open": b[1],
                "high": b[2],
                "low": b[3],
                "close": b[4],
                "volume": b[5],
            }
            for b in batch
        ]
        storage.upsert_ohlcv(rows)
        total += len(rows)

        last_ts = batch[-1][0]
        if last_ts <= since:
            # no progress, break to avoid infinite loop
            break
        since = last_ts + tf_ms
        time.sleep(ex.rateLimit / 1000.0)

    console.log(f"[green]{symbol} {timeframe}: +{total} rows[/green]")
    return total
