"""CCXT wrapper. Public endpoints only — no API key needed for market data.

Alasan pakai ccxt: unified API across exchanges. Kalau nanti switch Bybit→Binance,
tidak perlu rewrite logic, cukup ganti nama exchange.
"""
from __future__ import annotations

import ccxt
from typing import Any

from .retry import retry_network


_SUPPORTED = {"bybit", "binance", "okx", "kucoin"}


def get_exchange(name: str = "bybit", **kwargs: Any) -> ccxt.Exchange:
    """Instantiate a public ccxt exchange client.

    Default config:
      - rateLimit enforced (hindari ban)
      - timeout 15s (spec step-1 resilience — fail fast, retry di layer atas)
      - options.defaultType 'swap' untuk akses funding rate

    `load_markets()` dibungkus retry_network — first call tiap cycle sering
    kena DNS hiccup WSL2 setelah resume.
    """
    name = name.lower()
    if name not in _SUPPORTED:
        raise ValueError(f"Unsupported exchange: {name}. Supported: {_SUPPORTED}")
    klass = getattr(ccxt, name)
    config = {
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {"defaultType": "swap"},  # perpetual swap untuk funding
    }
    config.update(kwargs)
    ex = klass(config)
    retry_network(ex.load_markets, label=f"load_markets {name}")
    return ex
