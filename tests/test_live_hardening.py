from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.futures_broker import (
    LIVE_MAX_RISK_PCT,
    BinanceFuturesBroker,
    LivePreflightError,
)
from scripts import tx


def _broker(restrictions: dict, locked: bool = False) -> BinanceFuturesBroker:
    broker = object.__new__(BinanceFuturesBroker)
    broker.fetch_api_restrictions = lambda: restrictions
    broker.fetch_account_trading_status = lambda: {"data": {"isLocked": locked}}
    return broker


def test_live_preflight_requires_ip_whitelist(monkeypatch):
    monkeypatch.delenv("TX_ALLOW_UNRESTRICTED_API", raising=False)
    broker = _broker({
        "ipRestrict": False,
        "enableReading": True,
        "enableFutures": True,
        "enableSpotAndMarginTrading": False,
        "enableMargin": False,
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "permitsUniversalTransfer": False,
    })
    with pytest.raises(LivePreflightError, match="IP whitelist"):
        broker.assert_live_entry_ready(0.25)


def test_live_preflight_rejects_withdraw_permission(monkeypatch):
    monkeypatch.delenv("TX_ALLOW_UNRESTRICTED_API", raising=False)
    broker = _broker({
        "ipRestrict": True,
        "enableReading": True,
        "enableFutures": True,
        "enableSpotAndMarginTrading": False,
        "enableMargin": False,
        "enableWithdrawals": True,
        "enableInternalTransfer": False,
        "permitsUniversalTransfer": False,
    })
    with pytest.raises(LivePreflightError, match="withdrawals enabled"):
        broker.assert_live_entry_ready(0.25)


def test_live_preflight_accepts_hardened_key(monkeypatch):
    monkeypatch.delenv("TX_ALLOW_UNRESTRICTED_API", raising=False)
    broker = _broker({
        "ipRestrict": True,
        "enableReading": True,
        "enableFutures": True,
        "enableSpotAndMarginTrading": False,
        "enableMargin": False,
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "permitsUniversalTransfer": False,
    })
    result = broker.assert_live_entry_ready(LIVE_MAX_RISK_PCT)
    assert result["ip_restrict"] is True
    assert result["withdrawals"] is False


def test_live_preflight_rejects_extra_transfer_permissions(monkeypatch):
    monkeypatch.delenv("TX_ALLOW_UNRESTRICTED_API", raising=False)
    broker = _broker({
        "ipRestrict": True,
        "enableReading": True,
        "enableFutures": True,
        "enableSpotAndMarginTrading": False,
        "enableMargin": False,
        "enableWithdrawals": False,
        "enableInternalTransfer": False,
        "permitsUniversalTransfer": True,
    })
    with pytest.raises(LivePreflightError, match="universal-transfer"):
        broker.assert_live_entry_ready(0.25)


def test_risk_preflight_hard_ceiling_ignores_force():
    broker = SimpleNamespace(fetch_futures_balance=lambda: {"total": 100.0})
    msg = tx._risk_preflight(
        broker, "BTC/USDT:USDT", qty=1.0, entry=100.0, sl=99.0, force=True,
    )
    assert "hard live-pilot ceiling" in msg


def test_risk_preflight_allows_half_percent():
    broker = SimpleNamespace(fetch_futures_balance=lambda: {"total": 100.0})
    assert tx._risk_preflight(
        broker, "BTC/USDT:USDT", qty=0.5, entry=100.0, sl=99.0, force=False,
    ) is None


class _AlgoExchange:
    def __init__(self):
        self.created = []
        self.fetched = []
        self.cancelled = []

    def create_order(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return {"id": "algo-1"}

    def fetch_open_orders(self, *args, **kwargs):
        self.fetched.append((args, kwargs))
        return []

    def fetch_order(self, *args, **kwargs):
        self.fetched.append((args, kwargs))
        return {"id": "algo-1", "status": "open"}

    def cancel_order(self, *args, **kwargs):
        self.cancelled.append((args, kwargs))
        return {"id": "algo-1"}


def _algo_broker():
    broker = object.__new__(BinanceFuturesBroker)
    broker._ex = _AlgoExchange()
    broker._markets_loaded = True
    return broker


def test_trigger_order_uses_explicit_algo_fields():
    broker = _algo_broker()
    result = broker.place_reduce_trigger(
        "BTC/USDT:USDT", "sell", 0.01, 100.0, "STOP", "cid-stop",
    )
    assert result.success is True
    args, _ = broker._ex.created[0]
    params = args[5]
    assert args[1] == "STOP_MARKET"
    assert params == {
        "triggerPrice": 100.0,
        "reduceOnly": True,
        "workingType": "MARK_PRICE",
        "priceProtect": True,
        "clientAlgoId": "cid-stop",
    }


def test_trigger_queries_route_to_algo_endpoint():
    broker = _algo_broker()
    broker.fetch_trigger_orders("BTC/USDT:USDT")
    assert broker._ex.fetched[-1][1] == {"params": {"trigger": True}}
    broker.fetch_order_by_client_id(
        "BTC/USDT:USDT", "cid-stop", conditional=True,
    )
    assert broker._ex.fetched[-1][1] == {
        "params": {"clientAlgoId": "cid-stop", "trigger": True},
    }
    broker.cancel_order("BTC/USDT:USDT", "algo-1", conditional=True)
    assert broker._ex.cancelled[-1][1] == {"params": {"trigger": True}}
