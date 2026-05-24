"""Broker abstraction for live spot trading.

Two broker variants:
  - BinanceSpotBroker  : real order placement via ccxt (LIVE money)
  - DryRunSpotBroker   : same auth + ticker fetch, but place_* returns mock
                         OrderResult with the live ticker price (no actual
                         order to Binance). Untuk smoke + Phase 1 validation.

Both reuse same credentials file ~/.secrets/binance.env.

NOTE: PaperBroker (data_pipe/paper_broker.py) is older — owns trade_journal
in-memory untuk backtest. Bedakan: PaperBroker writes synthetic trades; broker
modul ini place market orders to actual exchange (live) atau simulate via real
ticker (dry-run).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

import ccxt


DEFAULT_SECRETS_PATH = Path.home() / ".secrets" / "binance.env"


# ─── data types ────────────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """Outcome of place_market_buy / place_market_sell call.

    `dry_run=True` means no real order was sent to exchange — values are
    derived from live ticker for plumbing validation.
    """
    success: bool
    side: str               # "BUY" or "SELL"
    symbol: str
    requested_amount: float  # base coin if SELL, USDT if BUY (intent)
    filled_amount: float     # actual base coin received (BUY) or sold (SELL)
    avg_fill_price: float    # USDT per base coin
    notional_usdt: float     # filled_amount * avg_fill_price
    fee_usdt: float          # commission in USDT-equiv
    order_id: Optional[str]  # exchange order id (None for dry-run)
    raw: dict = field(default_factory=dict)
    dry_run: bool = False
    error: Optional[str] = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─── credential loader ─────────────────────────────────────────────────────

def load_binance_credentials(path: Path = DEFAULT_SECRETS_PATH) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"binance secrets not found: {path}")
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    if not env.get("BINANCE_API_KEY") or not env.get("BINANCE_API_SECRET"):
        raise ValueError("BINANCE_API_KEY or BINANCE_API_SECRET missing/empty")
    return env


# ─── broker protocol ───────────────────────────────────────────────────────

class Broker(Protocol):
    """Minimal interface untuk live/dry-run spot broker. Sebagai duck-type
    contract, bukan ABC supaya bisa di-monkeypatch di tests.
    """

    is_live: bool

    def place_market_buy(self, symbol: str, usdt_amount: float) -> OrderResult: ...
    def place_market_sell(self, symbol: str, base_amount: float) -> OrderResult: ...
    def get_ticker(self, symbol: str) -> dict: ...
    def fetch_spot_balance(self) -> dict: ...
    def fetch_open_orders(self, symbol: Optional[str] = None) -> list: ...


# ─── BinanceSpotBroker (LIVE) ──────────────────────────────────────────────

class BinanceSpotBroker:
    """LIVE Binance spot broker. Place real market orders.

    Strictly spot only — Bahrain region blocks futures, dan Phase 1 fokus spot.

    Order method choice:
      - Market BUY: use `createOrder` with type='market', quoteOrderQty=usdt
        (Binance lets buy with USDT amount directly, returns fill in base coin)
      - Market SELL: use `createOrder` with type='market', amount=base coin
    """

    is_live = True

    def __init__(self, secrets_path: Path = DEFAULT_SECRETS_PATH):
        env = load_binance_credentials(secrets_path)
        self._ex = ccxt.binance({
            "apiKey": env["BINANCE_API_KEY"],
            "secret": env["BINANCE_API_SECRET"],
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": "spot"},
        })
        self._markets_loaded = False

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._ex.load_markets()
            self._markets_loaded = True

    def get_ticker(self, symbol: str) -> dict:
        self._ensure_markets()
        return self._ex.fetch_ticker(symbol)

    def fetch_spot_balance(self) -> dict:
        bal = self._ex.fetch_balance({"type": "spot"})
        return {k: float(v) for k, v in (bal.get("total") or {}).items() if v and float(v) > 0}

    def fetch_open_orders(self, symbol: Optional[str] = None) -> list:
        return self._ex.fetch_open_orders(symbol) if symbol else self._ex.fetch_open_orders()

    def place_market_buy(self, symbol: str, usdt_amount: float) -> OrderResult:
        """LIVE: spend `usdt_amount` USDT, receive base coin at market price."""
        self._ensure_markets()
        try:
            order = self._ex.create_order(
                symbol=symbol,
                type="market",
                side="buy",
                amount=None,
                params={"quoteOrderQty": float(usdt_amount)},
            )
        except Exception as e:
            return OrderResult(
                success=False, side="BUY", symbol=symbol,
                requested_amount=float(usdt_amount), filled_amount=0.0,
                avg_fill_price=0.0, notional_usdt=0.0, fee_usdt=0.0,
                order_id=None, error=f"{type(e).__name__}: {e}",
            )
        return _parse_ccxt_order(order, side="BUY", requested=float(usdt_amount))

    def place_market_sell(self, symbol: str, base_amount: float) -> OrderResult:
        """LIVE: sell `base_amount` of base coin, receive USDT."""
        self._ensure_markets()
        try:
            order = self._ex.create_order(
                symbol=symbol,
                type="market",
                side="sell",
                amount=float(base_amount),
            )
        except Exception as e:
            return OrderResult(
                success=False, side="SELL", symbol=symbol,
                requested_amount=float(base_amount), filled_amount=0.0,
                avg_fill_price=0.0, notional_usdt=0.0, fee_usdt=0.0,
                order_id=None, error=f"{type(e).__name__}: {e}",
            )
        return _parse_ccxt_order(order, side="SELL", requested=float(base_amount))


# ─── DryRunSpotBroker ──────────────────────────────────────────────────────

class DryRunSpotBroker(BinanceSpotBroker):
    """Same auth + market data as live broker, but place_* synthesizes a
    mock OrderResult using live ticker. NO order goes to Binance.

    Useful for: plumbing validation, executor smoke, CI without exchange risk.
    """

    is_live = False

    def place_market_buy(self, symbol: str, usdt_amount: float) -> OrderResult:
        tk = self.get_ticker(symbol)
        price = float(tk.get("last") or tk.get("close") or 0)
        if price <= 0:
            return OrderResult(
                success=False, side="BUY", symbol=symbol,
                requested_amount=usdt_amount, filled_amount=0.0,
                avg_fill_price=0.0, notional_usdt=0.0, fee_usdt=0.0,
                order_id=None, dry_run=True,
                error=f"dry-run: no price for {symbol}",
            )
        filled = float(usdt_amount) / price
        fee = float(usdt_amount) * 0.001  # Binance spot maker/taker 0.1%
        return OrderResult(
            success=True, side="BUY", symbol=symbol,
            requested_amount=float(usdt_amount), filled_amount=filled,
            avg_fill_price=price, notional_usdt=float(usdt_amount),
            fee_usdt=fee, order_id=None, dry_run=True,
            raw={"source": "dry-run", "ticker_price": price},
        )

    def place_market_sell(self, symbol: str, base_amount: float) -> OrderResult:
        tk = self.get_ticker(symbol)
        price = float(tk.get("last") or tk.get("close") or 0)
        if price <= 0:
            return OrderResult(
                success=False, side="SELL", symbol=symbol,
                requested_amount=base_amount, filled_amount=0.0,
                avg_fill_price=0.0, notional_usdt=0.0, fee_usdt=0.0,
                order_id=None, dry_run=True,
                error=f"dry-run: no price for {symbol}",
            )
        notional = float(base_amount) * price
        fee = notional * 0.001
        return OrderResult(
            success=True, side="SELL", symbol=symbol,
            requested_amount=float(base_amount), filled_amount=float(base_amount),
            avg_fill_price=price, notional_usdt=notional,
            fee_usdt=fee, order_id=None, dry_run=True,
            raw={"source": "dry-run", "ticker_price": price},
        )


# ─── helpers ───────────────────────────────────────────────────────────────

def _parse_ccxt_order(order: dict, side: str, requested: float) -> OrderResult:
    """Translate ccxt order dict ke OrderResult dataclass."""
    symbol = order.get("symbol") or "?"
    filled = float(order.get("filled") or 0.0)
    avg_price = float(order.get("average") or order.get("price") or 0.0)
    cost = float(order.get("cost") or (filled * avg_price))
    fee_info = order.get("fee") or {}
    fee_cost = float(fee_info.get("cost") or 0.0) if isinstance(fee_info, dict) else 0.0
    return OrderResult(
        success=filled > 0,
        side=side,
        symbol=symbol,
        requested_amount=requested,
        filled_amount=filled,
        avg_fill_price=avg_price,
        notional_usdt=cost,
        fee_usdt=fee_cost,
        order_id=str(order.get("id") or ""),
        raw=order,
    )


def make_broker(live: bool = False, secrets_path: Path = DEFAULT_SECRETS_PATH) -> Broker:
    """Factory. Default dry-run (safe). Set `live=True` untuk real orders."""
    if live:
        return BinanceSpotBroker(secrets_path=secrets_path)
    return DryRunSpotBroker(secrets_path=secrets_path)
