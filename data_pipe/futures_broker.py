"""Broker abstraction untuk live USDT-M futures trading.

Mirror dari `brokers.py` (spot) tapi buat Binance USDT-M perpetual futures.
Dipakai:
  - funding-carry short leg (long spot via BinanceSpotBroker + short perp di sini)
  - pairs-neutral (long perp A / short perp B)

Dua varian:
  - BinanceFuturesBroker : real order (LIVE money) via ccxt defaultType=future
  - DryRunFuturesBroker  : auth + market/funding reads sama, place_* disintesis
                           dari mark price (NO order ke exchange)

Reuse credential loader dari `brokers.py` (~/.secrets/binance.env).

PENTING — format simbol: ccxt USDT-M perp pakai suffix settle, mis.
`"BTC/USDT:USDT"` (bukan `"BTC/USDT"` yang itu spot). Strategy layer wajib
kirim simbol perp yang bener.

SAFETY: `SAFETY_MAX_LEVERAGE` = hard ceiling broker-level (anti fat-finger).
Strategy cap (2x) di-enforce di risk_guard; broker NOLAK leverage > ceiling
berapa pun yang diminta. Margin mode default ISOLATED (rugi terbatas ke
margin posisi, gak nyeret saldo lain).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

import ccxt

from .brokers import DEFAULT_SECRETS_PATH, load_binance_credentials


SAFETY_MAX_LEVERAGE = 10         # bumped 5→10 (2026-05-28, experiment E1 — Worm consent)
DEFAULT_MARGIN_MODE = "ISOLATED"


# ─── data types ────────────────────────────────────────────────────────────

@dataclass
class FuturesOrderResult:
    """Outcome place_market_order. `dry_run=True` = disintesis dari mark price."""
    success: bool
    side: str                 # "buy" / "sell"
    symbol: str
    requested_amount: float   # base coin (contracts)
    filled_amount: float
    avg_fill_price: float
    notional_usdt: float
    fee_usdt: float
    reduce_only: bool
    order_id: Optional[str]
    raw: dict = field(default_factory=dict)
    dry_run: bool = False
    error: Optional[str] = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FuturesPosition:
    """Snapshot satu posisi futures (open)."""
    symbol: str
    side: str                 # "LONG" / "SHORT"
    contracts: float          # positif; arah ada di `side`
    entry_price: float
    mark_price: float
    liq_price: float
    leverage: float
    notional_usdt: float
    unrealized_pnl: float
    margin_usdt: float        # initial/isolated margin
    raw: dict = field(default_factory=dict)

    @property
    def liq_distance_pct(self) -> float:
        """Jarak mark→likuidasi dalam %. inf kalau gak ada liq price."""
        if self.liq_price <= 0 or self.mark_price <= 0:
            return float("inf")
        return abs(self.mark_price - self.liq_price) / self.mark_price * 100.0


@dataclass
class FundingInfo:
    """State funding rate satu simbol perp."""
    symbol: str
    funding_rate: float           # per interval (0.0001 = 0.01%)
    mark_price: float
    next_funding_ts: Optional[datetime]
    interval_hours: int = 8

    @property
    def funding_rate_bps(self) -> float:
        return self.funding_rate * 10_000.0

    def annualized_pct(self) -> float:
        """Estimasi funding tahunan kalau rate konstan (3 interval/hari × 365)."""
        intervals_per_year = (24 / self.interval_hours) * 365
        return self.funding_rate * intervals_per_year * 100.0


# ─── broker protocol ───────────────────────────────────────────────────────

class FuturesBroker(Protocol):
    is_live: bool

    def set_leverage(self, symbol: str, leverage: int) -> bool: ...
    def set_margin_mode(self, symbol: str, mode: str = DEFAULT_MARGIN_MODE) -> bool: ...
    def place_market_order(
        self, symbol: str, side: str, amount: float, reduce_only: bool = False,
    ) -> FuturesOrderResult: ...
    def fetch_positions(self, symbol: Optional[str] = None) -> list[FuturesPosition]: ...
    def fetch_funding(self, symbol: str) -> FundingInfo: ...
    def fetch_mark_price(self, symbol: str) -> float: ...
    def fetch_futures_balance(self) -> dict: ...


# ─── BinanceFuturesBroker (LIVE) ───────────────────────────────────────────

class BinanceFuturesBroker:
    """LIVE Binance USDT-M futures broker. Place real market orders."""

    is_live = True

    def __init__(self, secrets_path: Path = DEFAULT_SECRETS_PATH):
        env = load_binance_credentials(secrets_path)
        self._ex = ccxt.binance({
            "apiKey": env["BINANCE_API_KEY"],
            "secret": env["BINANCE_API_SECRET"],
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": "future", "adjustForTimeDifference": True},   # USDT-M perpetual
        })
        self._markets_loaded = False

    def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            self._ex.load_markets()
            self._markets_loaded = True

    # ── reads ──────────────────────────────────────────────────────────────

    def fetch_futures_balance(self) -> dict:
        """USDT balance di wallet futures: {total, free, used}."""
        bal = self._ex.fetch_balance()
        usdt = bal.get("USDT", {}) or {}
        return {
            "total": float(usdt.get("total") or 0.0),
            "free": float(usdt.get("free") or 0.0),
            "used": float(usdt.get("used") or 0.0),
        }

    def fetch_mark_price(self, symbol: str) -> float:
        self._ensure_markets()
        t = self._ex.fetch_ticker(symbol)
        info = t.get("info") or {}
        mark = info.get("markPrice") or t.get("last") or t.get("close")
        return float(mark or 0.0)

    def fetch_funding(self, symbol: str) -> FundingInfo:
        self._ensure_markets()
        fr = self._ex.fetch_funding_rate(symbol)
        rate = float(fr.get("fundingRate") or 0.0)
        mark = float(fr.get("markPrice") or 0.0)
        ts_ms = fr.get("fundingTimestamp") or fr.get("nextFundingTimestamp")
        next_ts = (
            datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
            if ts_ms else None
        )
        return FundingInfo(
            symbol=symbol, funding_rate=rate, mark_price=mark, next_funding_ts=next_ts,
        )

    def fetch_positions(self, symbol: Optional[str] = None) -> list[FuturesPosition]:
        self._ensure_markets()
        raw = self._ex.fetch_positions([symbol] if symbol else None)
        out: list[FuturesPosition] = []
        for p in raw:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0:
                continue
            side = (p.get("side") or "").upper()
            if side not in ("LONG", "SHORT"):
                side = "LONG" if contracts > 0 else "SHORT"
            out.append(FuturesPosition(
                symbol=p.get("symbol") or symbol or "?",
                side=side,
                contracts=abs(contracts),
                entry_price=float(p.get("entryPrice") or 0.0),
                mark_price=float(p.get("markPrice") or 0.0),
                liq_price=float(p.get("liquidationPrice") or 0.0),
                leverage=float(p.get("leverage") or 0.0),
                notional_usdt=float(p.get("notional") or 0.0),
                unrealized_pnl=float(p.get("unrealizedPnl") or 0.0),
                margin_usdt=float(p.get("initialMargin") or p.get("collateral") or 0.0),
                raw=p,
            ))
        return out

    def fetch_trigger_orders(self, symbol: Optional[str] = None) -> list:
        """Conditional/trigger orders (SL/TP). Binance misahin dari open orders
        biasa → WAJIB params stop=True (fetch_open_orders default gak nampilin)."""
        self._ensure_markets()
        try:
            return self._ex.fetch_open_orders(symbol, params={"stop": True})
        except Exception:
            return []

    def get_market_filters(self, symbol: str) -> dict:
        self._ensure_markets()
        mk = self._ex.market(symbol)
        limits = mk.get("limits") or {}
        amt = limits.get("amount") or {}
        cost = limits.get("cost") or {}
        return {
            "min_qty": float(amt.get("min") or 0.0),
            "step_size": float((mk.get("precision") or {}).get("amount") or 0.0),
            "min_notional": float(cost.get("min") or 0.0),
        }

    # ── account state mutations ──────────────────────────────────────────────

    def get_multi_assets_mode(self) -> bool:
        """True kalau akun Multi-Assets Mode (margin cross-shared; ISOLATED unavailable)."""
        try:
            r = self._ex.fapiprivate_get_multiassetsmargin()
            return str(r.get("multiAssetsMargin")).lower() == "true"
        except Exception:
            return False

    def disable_multi_assets_mode(self) -> bool:
        """Matiin Multi-Assets Mode (HANYA bisa pas akun flat) supaya ISOLATED bisa dipakai."""
        try:
            self._ex.fapiprivate_post_multiassetsmargin({"multiAssetsMargin": "false"})
            return True
        except Exception as e:
            print(f"[margin] disable multi-assets gagal: {type(e).__name__}: {str(e)[:140]}")
            return False

    def set_margin_mode(self, symbol: str, mode: str = DEFAULT_MARGIN_MODE) -> bool:
        """Set ISOLATED/CROSS. Handle Multi-Assets Mode (isolated unavailable) +
        surface error asli. -4046 (no need to change) = OK."""
        self._ensure_markets()
        if self.get_multi_assets_mode():
            if mode.upper() == "ISOLATED":
                print("[margin] Multi-Assets Mode ON → margin cross-shared, ISOLATED "
                      "unavailable. OK — risk dikontrol SL, bukan ring-fence margin.")
            return True
        try:
            self._ex.set_margin_mode(mode.lower(), symbol)
            return True
        except Exception as e:
            if "4046" in str(e) or "No need to change" in str(e):
                return True
            print(f"[margin] set {mode} gagal: {type(e).__name__}: {str(e)[:140]}")
            return False

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage simbol. NOLAK kalau > SAFETY_MAX_LEVERAGE (fat-finger guard)."""
        if leverage > SAFETY_MAX_LEVERAGE:
            raise ValueError(
                f"leverage {leverage} > SAFETY_MAX_LEVERAGE {SAFETY_MAX_LEVERAGE}"
            )
        self._ensure_markets()
        try:
            self._ex.set_leverage(int(leverage), symbol)
            return True
        except Exception:
            return False

    # ── pre-flight ───────────────────────────────────────────────────────────

    def check_order_feasible(self, symbol: str, amount: float, price: float) -> Optional[str]:
        f = self.get_market_filters(symbol)
        if f["min_qty"] > 0 and amount < f["min_qty"]:
            return f"amount {amount} < min_qty {f['min_qty']}"
        notional = amount * price
        if f["min_notional"] > 0 and notional < f["min_notional"]:
            return (
                f"notional {notional:.2f} < min_notional {f['min_notional']:.2f} "
                f"(amount={amount}, mark={price})"
            )
        return None

    # ── orders ───────────────────────────────────────────────────────────────

    def place_market_order(
        self, symbol: str, side: str, amount: float, reduce_only: bool = False,
    ) -> FuturesOrderResult:
        """LIVE market order. side='buy' (long/cover) | 'sell' (short/reduce-long).

        reduce_only=True buat nutup posisi (gak bisa flip ke arah lawan).
        """
        self._ensure_markets()
        side = side.lower()
        if side not in ("buy", "sell"):
            return _fail_order(symbol, side, amount, reduce_only, f"invalid side {side}")
        mark = self.fetch_mark_price(symbol)
        if not reduce_only:  # entry: cek min notional. close: skip (boleh < min_notional)
            err = self.check_order_feasible(symbol, amount, mark)
            if err:
                return _fail_order(symbol, side, amount, reduce_only, f"pre-flight: {err}")
        try:
            params = {"reduceOnly": True} if reduce_only else {}
            order = self._ex.create_order(symbol, "market", side, float(amount), params=params)
        except Exception as e:
            return _fail_order(symbol, side, amount, reduce_only, f"{type(e).__name__}: {e}")
        return _parse_ccxt_futures_order(order, side, float(amount), reduce_only)

    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float,
        reduce_only: bool = False,
    ) -> FuturesOrderResult:
        """Limit order (resting). side 'buy'/'sell'."""
        self._ensure_markets()
        try:
            params = {"reduceOnly": True} if reduce_only else {}
            order = self._ex.create_order(
                symbol, "limit", side.lower(), float(amount), float(price), params)
        except Exception as e:
            return _fail_order(symbol, side, amount, reduce_only, f"{type(e).__name__}: {e}")
        res = _parse_ccxt_futures_order(order, side.lower(), float(amount), reduce_only)
        res.success = bool(order.get("id"))  # resting limit: filled=0 tapi sukses kalau diterima
        return res

    def place_reduce_trigger(
        self, symbol: str, side: str, amount: float, stop_price: float, kind: str,
    ) -> FuturesOrderResult:
        """Pasang reduceOnly trigger order proteksi posisi.

        kind: 'STOP' (stop-loss → STOP_MARKET) | 'TP' (take-profit → TAKE_PROFIT_MARKET).
        side: sisi PENUTUP ('buy' nutup short, 'sell' nutup long).
        """
        self._ensure_markets()
        otype = "STOP_MARKET" if kind.upper() == "STOP" else "TAKE_PROFIT_MARKET"
        try:
            order = self._ex.create_order(
                symbol, otype, side.lower(), float(amount), None,
                {"stopPrice": float(stop_price), "reduceOnly": True},
            )
        except Exception as e:
            return _fail_order(symbol, side, amount, True, f"{kind}: {type(e).__name__}: {e}")
        return FuturesOrderResult(
            success=True, side=side.lower(), symbol=symbol,
            requested_amount=float(amount), filled_amount=0.0,
            avg_fill_price=float(stop_price), notional_usdt=0.0, fee_usdt=0.0,
            reduce_only=True, order_id=str(order.get("id") or ""), raw=order,
        )

    def cancel_order(self, symbol: str, order_id: str, conditional: bool = False) -> bool:
        """Cancel satu order by id. SL/TP dari place_reduce_trigger = CONDITIONAL/algo
        order Binance (algoId, subsystem terpisah) → set conditional=True biar ccxt route
        ke endpoint algo (params stop=True). Kalau enggak → -2011 'Unknown order sent'
        (endpoint order reguler gak kenal algoId). True = sukses."""
        self._ensure_markets()
        try:
            self._ex.cancel_order(str(order_id), symbol, params={"stop": True} if conditional else {})
            return True
        except Exception as e:
            print(f"[cancel] order {order_id} gagal: {type(e).__name__}: {str(e)[:140]}")
            return False

    def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel SEMUA open order symbol — REGULER + CONDITIONAL (SL/TP algo). True = full sukses.
        ⚠️ Caller WAJIB pastiin posisi flat dulu kalau gak mau strip proteksi posisi hidup.
        ⚠️ SL/TP reduceOnly TIDAK auto-cancel pas posisi tutup (bukan OCO atomik) → leg sisa
        (mis. SL nyangkut setelah TP fire) WAJIB dibersihin lewat sini."""
        self._ensure_markets()
        ok = True
        try:
            self._ex.cancel_all_orders(symbol)  # order reguler (DELETE allOpenOrders)
        except Exception as e:
            if "-2011" not in str(e) and "Unknown order" not in str(e):  # -2011 = gak ada order = OK
                print(f"[cancel] cancel_all reguler {symbol} gagal: {type(e).__name__}: {str(e)[:140]}")
                ok = False
        try:
            for o in self._ex.fetch_open_orders(symbol, params={"stop": True}):  # conditional/algo
                if not self.cancel_order(symbol, o.get("id"), conditional=True):
                    ok = False
        except Exception as e:
            print(f"[cancel] cancel_all conditional {symbol} gagal: {type(e).__name__}: {str(e)[:140]}")
            ok = False
        return ok


# ─── DryRunFuturesBroker ────────────────────────────────────────────────────

class DryRunFuturesBroker(BinanceFuturesBroker):
    """Auth + market/funding reads sama (live), tapi place_* sintesis dari mark
    price + set_leverage/margin_mode jadi no-op. NO order ke Binance.
    """

    is_live = False

    def cancel_order(self, symbol: str, order_id: str, conditional: bool = False) -> bool:
        return True  # dry-run: no-op, JANGAN sentuh exchange

    def cancel_all_orders(self, symbol: str) -> bool:
        return True  # dry-run: no-op, JANGAN sentuh exchange

    def place_reduce_trigger(self, symbol, side, amount, stop_price, kind):
        return FuturesOrderResult(
            success=True, side=str(side).lower(), symbol=symbol,
            requested_amount=float(amount), filled_amount=0.0,
            avg_fill_price=float(stop_price), notional_usdt=0.0, fee_usdt=0.0,
            reduce_only=True, order_id=None, dry_run=True,
            raw={"kind": kind, "stop_price": float(stop_price)},
        )

    def place_limit_order(self, symbol, side, amount, price, reduce_only=False):
        return FuturesOrderResult(
            success=True, side=str(side).lower(), symbol=symbol,
            requested_amount=float(amount), filled_amount=0.0,
            avg_fill_price=float(price), notional_usdt=float(amount) * float(price),
            fee_usdt=0.0, reduce_only=reduce_only, order_id=None, dry_run=True,
            raw={"limit_price": float(price)},
        )

    def set_margin_mode(self, symbol: str, mode: str = DEFAULT_MARGIN_MODE) -> bool:
        return True

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        if leverage > SAFETY_MAX_LEVERAGE:
            raise ValueError(
                f"leverage {leverage} > SAFETY_MAX_LEVERAGE {SAFETY_MAX_LEVERAGE}"
            )
        return True

    def place_market_order(
        self, symbol: str, side: str, amount: float, reduce_only: bool = False,
    ) -> FuturesOrderResult:
        side = side.lower()
        if side not in ("buy", "sell"):
            return _fail_order(symbol, side, amount, reduce_only, f"invalid side {side}")
        mark = self.fetch_mark_price(symbol)
        if mark <= 0:
            return _fail_order(symbol, side, amount, reduce_only, f"dry-run: no mark for {symbol}")
        notional = float(amount) * mark
        fee = notional * 0.0004  # perp taker 0.04%
        return FuturesOrderResult(
            success=True, side=side, symbol=symbol,
            requested_amount=float(amount), filled_amount=float(amount),
            avg_fill_price=mark, notional_usdt=notional, fee_usdt=fee,
            reduce_only=reduce_only, order_id=None, dry_run=True,
            raw={"source": "dry-run", "mark_price": mark},
        )


# ─── helpers ─────────────────────────────────────────────────────────────────

def _fail_order(
    symbol: str, side: str, amount: float, reduce_only: bool, error: str,
) -> FuturesOrderResult:
    return FuturesOrderResult(
        success=False, side=side, symbol=symbol, requested_amount=float(amount),
        filled_amount=0.0, avg_fill_price=0.0, notional_usdt=0.0, fee_usdt=0.0,
        reduce_only=reduce_only, order_id=None, error=error,
    )


def _parse_ccxt_futures_order(
    order: dict, side: str, requested: float, reduce_only: bool,
) -> FuturesOrderResult:
    filled = float(order.get("filled") or 0.0)
    avg_price = float(order.get("average") or order.get("price") or 0.0)
    cost = float(order.get("cost") or (filled * avg_price))
    fee_info = order.get("fee") or {}
    fee_cost = float(fee_info.get("cost") or 0.0) if isinstance(fee_info, dict) else 0.0
    return FuturesOrderResult(
        success=filled > 0,
        side=side,
        symbol=order.get("symbol") or "?",
        requested_amount=requested,
        filled_amount=filled,
        avg_fill_price=avg_price,
        notional_usdt=cost,
        fee_usdt=fee_cost,
        reduce_only=reduce_only,
        order_id=str(order.get("id") or ""),
        raw=order,
    )


def make_futures_broker(
    live: bool = False, secrets_path: Path = DEFAULT_SECRETS_PATH,
) -> FuturesBroker:
    """Factory. Default dry-run (aman). live=True untuk real orders."""
    if live:
        return BinanceFuturesBroker(secrets_path=secrets_path)
    return DryRunFuturesBroker(secrets_path=secrets_path)
