"""Risk-based position sizing dengan 3-level caps.

Pipeline:
  risk_amount   = equity × risk_pct / 100
  risk_per_unit = entry_price × sl_pct / 100      (jarak SL absolut)
  raw_size      = risk_amount / risk_per_unit
  raw_notional  = raw_size × entry_price

Cap A — max_position_pct vs equity (per-trade)
Cap B — max_gross_exposure_pct vs equity (total open)
Cap C — max_concurrent_trades (count)

Caps berurutan: hard-block dulu (concurrent), lalu scale-down (position,
gross). Smallest constraint wins. Final size dibulatkan 6 desimal — cukup
presisi utk semua pair tier1/2 (BTC 8dp granularity, alt 6dp).
"""
from __future__ import annotations

from .portfolio import (
    DEFAULT_MAX_CONCURRENT_TRADES,
    DEFAULT_MAX_GROSS_EXPOSURE_PCT,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_RISK_PCT,
)


SIZE_DECIMALS = 6
DEFAULT_SL_PCT = 5.0


def _portfolio_cfg(cfg: dict) -> dict:
    return cfg.get("portfolio") or {}


def _skip(reason: str, *, risk_amount: float = 0.0) -> dict:
    return {
        "size": None,
        "notional": 0.0,
        "risk_amount": risk_amount,
        "reason_skip": reason,
    }


def calc_position_size(
    entry_price: float,
    sl_pct: float,
    direction: str,
    equity: float,
    current_exposure: dict,
    cfg: dict,
) -> dict:
    """Hitung size untuk satu trade. Return:
        {size, notional, risk_amount, reason_skip}
    `size` None kalau di-block oleh cap; `reason_skip` non-None hanya saat skip.
    """
    pcfg = _portfolio_cfg(cfg)
    risk_pct = float(pcfg.get("risk_pct_per_trade", DEFAULT_RISK_PCT))
    max_pos_pct = float(pcfg.get("max_position_pct", DEFAULT_MAX_POSITION_PCT))
    max_gross_pct = float(pcfg.get("max_gross_exposure_pct", DEFAULT_MAX_GROSS_EXPOSURE_PCT))
    max_concurrent = int(pcfg.get("max_concurrent_trades", DEFAULT_MAX_CONCURRENT_TRADES))

    # Input sanity
    if direction not in ("LONG", "SHORT"):
        return _skip(f"unsupported direction for sizing: {direction!r}")
    if entry_price is None or entry_price <= 0:
        return _skip(f"invalid entry_price: {entry_price!r}")
    if equity <= 0:
        return _skip(f"equity exhausted: {equity:.2f}")
    if sl_pct is None or sl_pct <= 0:
        return _skip(f"invalid sl_pct: {sl_pct!r}")

    current_n = int(current_exposure.get("count", 0))
    current_gross = float(current_exposure.get("gross_notional", 0.0))

    # ── Cap C — concurrent (early exit, no size makes sense) ────────────
    if current_n >= max_concurrent:
        return _skip(f"concurrent cap: {current_n}/{max_concurrent} open")

    # ── Risk-based raw size ─────────────────────────────────────────────
    risk_amount = equity * risk_pct / 100.0
    risk_per_unit = entry_price * sl_pct / 100.0
    if risk_per_unit <= 0:
        return _skip("risk_per_unit non-positive", risk_amount=risk_amount)

    raw_size = risk_amount / risk_per_unit
    raw_notional = raw_size * entry_price

    # ── Cap A — per-position notional ───────────────────────────────────
    max_position_notional = equity * max_pos_pct / 100.0
    if raw_notional > max_position_notional:
        scale = max_position_notional / raw_notional
        raw_size *= scale
        raw_notional *= scale

    # ── Cap B — gross exposure headroom ─────────────────────────────────
    max_gross_notional = equity * max_gross_pct / 100.0
    headroom = max_gross_notional - current_gross
    if headroom <= 0:
        return _skip(
            f"gross exposure cap reached "
            f"({current_gross:.2f}/{max_gross_notional:.2f})",
            risk_amount=risk_amount,
        )
    if raw_notional > headroom:
        scale = headroom / raw_notional
        raw_size *= scale
        raw_notional *= scale

    # ── Round + zero check ──────────────────────────────────────────────
    size = round(raw_size, SIZE_DECIMALS)
    if size <= 0:
        return _skip(
            f"size below minimum after caps (raw={raw_size:.10f})",
            risk_amount=risk_amount,
        )
    notional = round(size * entry_price, 2)
    return {
        "size": size,
        "notional": notional,
        "risk_amount": round(risk_amount, 2),
        "reason_skip": None,
    }
