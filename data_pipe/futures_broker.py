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
import os
from pathlib import Path
from typing import Optional, Protocol

import ccxt

from .brokers import DEFAULT_SECRETS_PATH, load_binance_credentials


SAFETY_MAX_LEVERAGE = 5          # parked 2026-07-06: backstop restored after E1
DEFAULT_MARGIN_MODE = "ISOLATED"
LIVE_MAX_RISK_PCT = 0.5          # one-ticket pilot ceiling; --force must not bypass


class LivePreflightError(RuntimeError):
    """Live entry blocked because Binance account/key state is not hardened."""


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
        client_order_id: Optional[str] = None,
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

    def fetch_api_restrictions(self) -> dict:
        """Binance API-key permissions, without exposing key/secret values."""
        self._ensure_markets()
        for name in ("sapiGetAccountApiRestrictions", "sapi_get_account_apirestrictions"):
            fn = getattr(self._ex, name, None)
            if fn:
                return fn({})
        raise RuntimeError("Binance API restriction endpoint unavailable in ccxt")

    def fetch_account_trading_status(self) -> dict:
        """Return Binance account lock/trading status."""
        self._ensure_markets()
        for name in ("sapiGetAccountApiTradingStatus", "sapi_get_account_apitradingstatus"):
            fn = getattr(self._ex, name, None)
            if fn:
                return fn({})
        raise RuntimeError("Binance account trading-status endpoint unavailable in ccxt")

    def assert_live_entry_ready(self, requested_risk_pct: float) -> dict:
        """Fail closed before opening risk.

        Account-side API permissions can only be changed in Binance. This guard
        makes the local executor refuse entries until the key is IP-restricted
        and confirms that futures trading is enabled while withdrawals remain
        disabled.
        """
        if requested_risk_pct <= 0:
            raise LivePreflightError("risk_pct must be > 0")
        if requested_risk_pct > LIVE_MAX_RISK_PCT:
            raise LivePreflightError(
                f"risk {requested_risk_pct:.4g}% > live pilot ceiling "
                f"{LIVE_MAX_RISK_PCT:.4g}%"
            )

        restrictions = self.fetch_api_restrictions()
        status = self.fetch_account_trading_status()
        status_data = status.get("data") or {}
        allow_unrestricted = os.environ.get("TX_ALLOW_UNRESTRICTED_API") == "1"
        blockers: list[str] = []

        if not restrictions.get("enableReading"):
            blockers.append("API reading disabled")
        if not restrictions.get("enableFutures"):
            blockers.append("API futures permission disabled")
        if restrictions.get("enableSpotAndMarginTrading"):
            blockers.append(
                "spot/margin trading enabled on the futures executor key"
            )
        if restrictions.get("enableWithdrawals"):
            blockers.append("API withdrawals enabled")
        if restrictions.get("enableMargin"):
            blockers.append("API margin permission enabled")
        if restrictions.get("enableInternalTransfer"):
            blockers.append("API internal-transfer permission enabled")
        if restrictions.get("permitsUniversalTransfer"):
            blockers.append("API universal-transfer permission enabled")
        if not restrictions.get("ipRestrict") and not allow_unrestricted:
            blockers.append(
                "API IP whitelist disabled; enable current stable egress IP in Binance"
            )
        if status_data.get("isLocked"):
            blockers.append("Binance account trading status is locked")
        if blockers:
            raise LivePreflightError("; ".join(blockers))

        return {
            "ip_restrict": bool(restrictions.get("ipRestrict")),
            "reading": bool(restrictions.get("enableReading")),
            "futures": bool(restrictions.get("enableFutures")),
            "spot_margin": bool(restrictions.get("enableSpotAndMarginTrading")),
            "margin": bool(restrictions.get("enableMargin")),
            "withdrawals": bool(restrictions.get("enableWithdrawals")),
            "internal_transfer": bool(restrictions.get("enableInternalTransfer")),
            "universal_transfer": bool(restrictions.get("permitsUniversalTransfer")),
            "account_locked": bool(status_data.get("isLocked")),
            "risk_pct": float(requested_risk_pct),
            "risk_ceiling_pct": LIVE_MAX_RISK_PCT,
            "unrestricted_api_override": allow_unrestricted,
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
        """Conditional/trigger orders (SL/TP) from Binance's algo endpoint."""
        self._ensure_markets()
        return self._ex.fetch_open_orders(symbol, params={"trigger": True})

    def fetch_all_open_orders(self) -> dict[str, list]:
        """All regular + conditional USDT-M Futures orders.

        Uses Binance Futures raw endpoints intentionally: CCXT refuses a
        symbol-less unified fetch unless a warning option is disabled.
        """
        self._ensure_markets()
        regular = self._ex.fapiPrivateGetOpenOrders({})
        conditional = self._ex.fapiPrivateGetOpenAlgoOrders({})
        return {
            "regular": regular if isinstance(regular, list) else [],
            "conditional": (
                conditional if isinstance(conditional, list) else []
            ),
        }

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
                print("[margin] Multi-Assets Mode ON → ISOLATED unavailable; refusing "
                      "autonomous/live entry to avoid cross-shared margin drift.")
                return False
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
        client_order_id: Optional[str] = None,
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
            if client_order_id:
                params["clientOrderId"] = str(client_order_id)
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
        client_order_id: Optional[str] = None,
    ) -> FuturesOrderResult:
        """Pasang reduceOnly trigger order proteksi posisi.

        kind: 'STOP' (stop-loss → STOP_MARKET) | 'TP' (take-profit → TAKE_PROFIT_MARKET).
        side: sisi PENUTUP ('buy' nutup short, 'sell' nutup long).
        """
        self._ensure_markets()
        otype = "STOP_MARKET" if kind.upper() == "STOP" else "TAKE_PROFIT_MARKET"
        try:
            params = {
                "triggerPrice": float(stop_price),
                "reduceOnly": True,
                "workingType": "MARK_PRICE",
                "priceProtect": True,
            }
            if client_order_id:
                params["clientAlgoId"] = str(client_order_id)
            order = self._ex.create_order(
                symbol, otype, side.lower(), float(amount), None,
                params,
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
        """Cancel one regular order or Binance conditional/algo order by ID."""
        self._ensure_markets()
        try:
            self._ex.cancel_order(
                str(order_id),
                symbol,
                params={"trigger": True} if conditional else {},
            )
            return True
        except Exception as e:
            print(f"[cancel] order {order_id} gagal: {type(e).__name__}: {str(e)[:140]}")
            return False

    def fetch_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
        conditional: bool = False,
    ) -> Optional[dict]:
        """Find a submitted order by deterministic client ID.

        Used after network timeouts/restarts to resolve whether Binance accepted
        the order before the controller considers any retry.
        """
        self._ensure_markets()
        try:
            return self._ex.fetch_order(
                "",
                symbol,
                params=(
                    {
                        "clientAlgoId": str(client_order_id),
                        "trigger": True,
                    }
                    if conditional
                    else {"clientOrderId": str(client_order_id)}
                ),
            )
        except Exception as e:
            msg = str(e)
            if "-2013" in msg or "Order does not exist" in msg or "Unknown order" in msg:
                return None
            raise

    def fetch_trigger_execution(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Optional[dict]:
        """Resolve a conditional order to its actual executed futures order.

        Binance's algo-order endpoint may expose ``actualOrderId`` after the
        trigger fires. Query that regular order to obtain fill status, average,
        and fees. If it has not fired yet, return the conditional order itself.
        """
        algo = self.fetch_order_by_client_id(
            symbol, client_order_id, conditional=True,
        )
        if algo is None:
            return None
        info = algo.get("info") or {}
        actual_id = (
            info.get("actualOrderId")
            or info.get("orderId")
            or algo.get("actualOrderId")
        )
        if actual_id not in (None, "", 0, "0"):
            try:
                return self._ex.fetch_order(str(actual_id), symbol)
            except Exception:
                # The algo record is still useful for status reconciliation.
                pass
        return algo

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
            for o in self._ex.fetch_open_orders(symbol, params={"trigger": True}):  # conditional/algo
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

    def __init__(self, secrets_path: Path = DEFAULT_SECRETS_PATH):
        super().__init__(secrets_path=secrets_path)
        self._dry_positions: dict[str, FuturesPosition] = {}
        self._dry_triggers: dict[str, list[dict]] = {}
        self._dry_orders_by_client_id: dict[str, dict] = {}

    def fetch_positions(self, symbol: Optional[str] = None) -> list[FuturesPosition]:
        if symbol is not None:
            pos = self._dry_positions.get(symbol)
            return [pos] if pos is not None else []
        return list(self._dry_positions.values())

    def fetch_trigger_orders(self, symbol: Optional[str] = None) -> list:
        if symbol is not None:
            return list(self._dry_triggers.get(symbol, []))
        return [
            row
            for rows in self._dry_triggers.values()
            for row in rows
        ]

    def fetch_all_open_orders(self) -> dict[str, list]:
        return {
            "regular": [],
            "conditional": self.fetch_trigger_orders(),
        }

    def cancel_order(self, symbol: str, order_id: str, conditional: bool = False) -> bool:
        rows = self._dry_triggers.get(symbol, [])
        self._dry_triggers[symbol] = [
            row for row in rows if str(row.get("id")) != str(order_id)
        ]
        return True  # dry-run: no-op, JANGAN sentuh exchange

    def cancel_all_orders(self, symbol: str) -> bool:
        self._dry_triggers[symbol] = []
        return True  # dry-run: no-op, JANGAN sentuh exchange

    def place_reduce_trigger(
        self, symbol, side, amount, stop_price, kind, client_order_id=None,
    ):
        order_id = client_order_id or (
            f"dry-{str(kind).lower()}-"
            f"{len(self._dry_triggers.get(symbol, [])) + 1}"
        )
        order_type = (
            "STOP_MARKET"
            if str(kind).upper() == "STOP"
            else "TAKE_PROFIT_MARKET"
        )
        row = {
            "id": str(order_id),
            "type": order_type,
            "side": str(side).lower(),
            "amount": float(amount),
            "stopPrice": float(stop_price),
            "reduceOnly": True,
            "info": {
                "origType": order_type,
                "stopPrice": str(stop_price),
            },
        }
        self._dry_triggers.setdefault(symbol, []).append(row)
        if client_order_id:
            self._dry_orders_by_client_id[str(client_order_id)] = dict(row)
        return FuturesOrderResult(
            success=True, side=str(side).lower(), symbol=symbol,
            requested_amount=float(amount), filled_amount=0.0,
            avg_fill_price=float(stop_price), notional_usdt=0.0, fee_usdt=0.0,
            reduce_only=True, order_id=str(order_id), dry_run=True,
            raw={
                "kind": kind,
                "stop_price": float(stop_price),
                "clientOrderId": client_order_id,
            },
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
        client_order_id: Optional[str] = None,
    ) -> FuturesOrderResult:
        side = side.lower()
        if side not in ("buy", "sell"):
            return _fail_order(symbol, side, amount, reduce_only, f"invalid side {side}")
        mark = self.fetch_mark_price(symbol)
        if mark <= 0:
            return _fail_order(symbol, side, amount, reduce_only, f"dry-run: no mark for {symbol}")
        if reduce_only:
            self._dry_positions.pop(symbol, None)
        else:
            position_side = "LONG" if side == "buy" else "SHORT"
            self._dry_positions[symbol] = FuturesPosition(
                symbol=symbol,
                side=position_side,
                contracts=float(amount),
                entry_price=mark,
                mark_price=mark,
                liq_price=0.0,
                leverage=0.0,
                notional_usdt=float(amount) * mark,
                unrealized_pnl=0.0,
                margin_usdt=0.0,
                raw={"source": "dry-run"},
            )
        notional = float(amount) * mark
        fee = notional * 0.0004  # perp taker 0.04%
        order_id = client_order_id or (
            f"dry-market-{len(self._dry_orders_by_client_id) + 1}"
        )
        if client_order_id:
            self._dry_orders_by_client_id[str(client_order_id)] = {
                "id": str(order_id),
                "clientOrderId": str(client_order_id),
                "status": "closed",
                "symbol": symbol,
                "side": side,
                "amount": float(amount),
                "average": mark,
                "reduceOnly": reduce_only,
            }
        return FuturesOrderResult(
            success=True, side=side, symbol=symbol,
            requested_amount=float(amount), filled_amount=float(amount),
            avg_fill_price=mark, notional_usdt=notional, fee_usdt=fee,
            reduce_only=reduce_only, order_id=str(order_id), dry_run=True,
            raw={
                "source": "dry-run",
                "mark_price": mark,
                "clientOrderId": client_order_id,
            },
        )

    def fetch_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
        conditional: bool = False,
    ) -> Optional[dict]:
        row = self._dry_orders_by_client_id.get(str(client_order_id))
        return dict(row) if row is not None else None

    def fetch_trigger_execution(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Optional[dict]:
        return self.fetch_order_by_client_id(
            symbol, client_order_id, conditional=True,
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
