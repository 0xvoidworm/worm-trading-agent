"""Retry helper untuk flaky network call.

Filosofi:
  - Retry HANYA untuk transport error (DNS, connection refused, timeout).
  - JANGAN retry untuk business error (HTTP 4xx / ccxt.ExchangeError) — caller
    yang salah, retry cuma bikin noise + rate limit.
  - Exponential backoff: base * 2^(attempt-1). Default 2s/4s (3 attempt total).
  - Log tiap retry sebagai [warn], bukan exception yang menghentikan cycle.
"""
from __future__ import annotations

import socket
import time
from typing import Callable, TypeVar

import ccxt
import requests

T = TypeVar("T")


# Error yang kita retry. Urutan tidak penting — isinstance check.
# NOTE: ccxt.NetworkError superclass untuk RequestTimeout, ExchangeNotAvailable,
#       DDoSProtection, dll — semua transport-ish. ccxt.ExchangeError TIDAK
#       diretry (business error).
RETRYABLE_ERRORS: tuple = (
    socket.gaierror,
    socket.timeout,
    ConnectionError,
    TimeoutError,
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ReadTimeout,
)


def is_retryable(exc: BaseException) -> bool:
    """Cek apakah exception layak di-retry."""
    if isinstance(exc, RETRYABLE_ERRORS):
        return True
    # Beberapa library wrap DNS failure di generic OSError. Cek string match
    # sebagai fallback.
    msg = str(exc).lower()
    return any(s in msg for s in (
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
        "connection refused",
        "connection reset",
    ))


def retry_network(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    label: str = "call",
    logger: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn() dengan exponential backoff untuk network error.

    Delay pattern (default): 2s, 4s, 8s (tapi karena max_attempts=3 hanya
    sleep 2 kali — setelah attempt 1 & 2). Raise error terakhir kalau semua
    attempt gagal atau error non-retryable.
    """
    last_err: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — kita re-classify & re-raise
            if not is_retryable(e):
                raise
            last_err = e
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            msg = (
                f"[warn] {label} attempt {attempt}/{max_attempts} failed "
                f"({type(e).__name__}): retry in {delay:.0f}s"
            )
            if logger:
                logger(msg)
            else:
                print(msg, flush=True)
            sleep(delay)
    assert last_err is not None
    raise last_err
