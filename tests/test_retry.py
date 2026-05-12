"""Tests untuk data_pipe.retry — retry exponential backoff.

Fokus:
  - Transient network error (DNS/ConnectionError) di-retry sampai sukses
  - Semua attempt gagal → raise error terakhir
  - Non-retryable (ValueError, ccxt.ExchangeError) langsung propagate
  - Delay mengikuti pattern base*2^(attempt-1), pakai mock sleep
"""
from __future__ import annotations

import socket

import ccxt
import pytest
import requests

from data_pipe.retry import is_retryable, retry_network


class _Counter:
    def __init__(self):
        self.n = 0
        self.delays: list[float] = []

    def fake_sleep(self, d: float):
        self.delays.append(d)


def _dns_err():
    return socket.gaierror("Temporary failure in name resolution")


def _conn_err():
    return requests.exceptions.ConnectionError("NameResolutionError('Failed to resolve')")


def _ccxt_net_err():
    return ccxt.NetworkError("bybit timeout")


def test_retry_success_first_attempt():
    c = _Counter()
    def fn():
        c.n += 1
        return "ok"
    assert retry_network(fn, sleep=c.fake_sleep) == "ok"
    assert c.n == 1
    assert c.delays == []  # no sleep kalau sukses pertama


def test_retry_recovers_after_two_failures():
    c = _Counter()
    def fn():
        c.n += 1
        if c.n < 3:
            raise _dns_err()
        return "ok"
    assert retry_network(fn, sleep=c.fake_sleep) == "ok"
    assert c.n == 3
    # backoff 2s, 4s (setelah attempt 1 & 2)
    assert c.delays == [2.0, 4.0]


def test_retry_exhausts_all_attempts_and_raises():
    c = _Counter()
    def fn():
        c.n += 1
        raise _dns_err()
    with pytest.raises(socket.gaierror):
        retry_network(fn, sleep=c.fake_sleep)
    assert c.n == 3
    # sleep antara attempt 1-2 dan 2-3, tidak sleep setelah final
    assert c.delays == [2.0, 4.0]


def test_retry_respects_ccxt_network_error():
    c = _Counter()
    def fn():
        c.n += 1
        if c.n < 2:
            raise _ccxt_net_err()
        return 42
    assert retry_network(fn, sleep=c.fake_sleep) == 42
    assert c.n == 2


def test_retry_non_retryable_propagates_immediately():
    c = _Counter()
    def fn():
        c.n += 1
        raise ValueError("bad arg")
    with pytest.raises(ValueError):
        retry_network(fn, sleep=c.fake_sleep)
    assert c.n == 1
    assert c.delays == []


def test_retry_does_not_retry_ccxt_exchange_error():
    """4xx / business error bukan transient — jangan retry."""
    c = _Counter()
    def fn():
        c.n += 1
        raise ccxt.ExchangeError("invalid symbol")
    with pytest.raises(ccxt.ExchangeError):
        retry_network(fn, sleep=c.fake_sleep)
    assert c.n == 1


def test_retry_custom_attempts_and_base_delay():
    c = _Counter()
    def fn():
        c.n += 1
        raise _conn_err()
    with pytest.raises(requests.exceptions.ConnectionError):
        retry_network(fn, max_attempts=4, base_delay=1.0, sleep=c.fake_sleep)
    assert c.n == 4
    # 1, 2, 4 (setelah 1,2,3)
    assert c.delays == [1.0, 2.0, 4.0]


def test_retry_logger_captures_warnings():
    msgs: list[str] = []
    c = _Counter()
    def fn():
        c.n += 1
        if c.n < 3:
            raise _dns_err()
        return "ok"
    retry_network(fn, label="test", logger=msgs.append, sleep=c.fake_sleep)
    assert len(msgs) == 2
    assert all("test attempt" in m for m in msgs)
    assert "retry in" in msgs[0]


def test_is_retryable_covers_wrapped_dns():
    # Generic OSError dengan message DNS → retryable via string match
    e = OSError("Temporary failure in name resolution")
    assert is_retryable(e)


def test_is_retryable_rejects_business_errors():
    assert not is_retryable(ValueError("x"))
    assert not is_retryable(KeyError("x"))
    assert not is_retryable(ccxt.ExchangeError("bad"))
