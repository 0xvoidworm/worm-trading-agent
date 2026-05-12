"""Portfolio accounting — equity & exposure dari trade_journal.

Single source of truth utk:
  - get_equity        : starting capital + realized PnL (exclude TEST_ARTIFACT)
  - get_open_exposure : gross notional + per-pair breakdown dari OPEN trades
  - get_account_state : composer dengan cap thresholds dari cfg

Defaults konservatif kalau cfg.portfolio missing — supaya unit test lama
(yang gak passing portfolio block) tetap dapat angka masuk akal.

PAIR direction trade di-skip (notional ambiguous; mereka pakai spread,
bukan single entry × size). MVP fokus LONG/SHORT.
"""
from __future__ import annotations

from .storage import Storage


DEFAULT_STARTING_CAPITAL = 10000.0
DEFAULT_RISK_PCT = 1.0
DEFAULT_MAX_POSITION_PCT = 25.0
DEFAULT_MAX_GROSS_EXPOSURE_PCT = 100.0
DEFAULT_MAX_CONCURRENT_TRADES = 3

TEST_ARTIFACT_PREFIX = "[TEST_ARTIFACT"
# DuckDB LIKE: '[' literal, '%' wildcard
_TEST_LIKE_PATTERN = TEST_ARTIFACT_PREFIX + "%"


def _portfolio_cfg(cfg: dict) -> dict:
    return cfg.get("portfolio") or {}


def get_equity(storage: Storage, cfg: dict) -> float:
    """Equity = starting_capital + sum realized PnL (CLOSED & non-test trades)."""
    pcfg = _portfolio_cfg(cfg)
    starting = float(pcfg.get("starting_capital", DEFAULT_STARTING_CAPITAL))
    row = storage._conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL "
        "  AND (thesis IS NULL OR thesis NOT LIKE ?)",
        [_TEST_LIKE_PATTERN],
    ).fetchone()
    realized = float(row[0]) if row else 0.0
    return starting + realized


def get_open_exposure(storage: Storage) -> dict:
    """Aggregate notional dari OPEN LONG/SHORT trades.

    Return:
      {
        "gross_notional": float,
        "count": int,
        "by_pair": {pair: notional},
      }
    """
    rows = storage._conn.execute(
        "SELECT pair, entry_price, size FROM trade_journal "
        "WHERE status='OPEN' AND direction IN ('LONG','SHORT') "
        "  AND entry_price IS NOT NULL AND size IS NOT NULL"
    ).fetchall()
    by_pair: dict[str, float] = {}
    gross = 0.0
    for pair, entry, size in rows:
        notional = abs(float(entry) * float(size))
        by_pair[str(pair)] = by_pair.get(str(pair), 0.0) + notional
        gross += notional
    return {
        "gross_notional": gross,
        "count": len(rows),
        "by_pair": by_pair,
    }


def get_account_state(storage: Storage, cfg: dict) -> dict:
    """Compose equity + exposure + caps untuk satu snapshot decision.

    `headroom` = sisa gross budget. `headroom_pct` = headroom / equity * 100.
    Caps yang dimunculkan dipakai di sizing & executor recheck.
    """
    pcfg = _portfolio_cfg(cfg)
    equity = get_equity(storage, cfg)
    exposure = get_open_exposure(storage)

    max_position_pct = float(pcfg.get("max_position_pct", DEFAULT_MAX_POSITION_PCT))
    max_gross_pct = float(pcfg.get("max_gross_exposure_pct", DEFAULT_MAX_GROSS_EXPOSURE_PCT))
    max_concurrent = int(pcfg.get("max_concurrent_trades", DEFAULT_MAX_CONCURRENT_TRADES))

    max_gross_notional = equity * max_gross_pct / 100.0
    headroom = max(max_gross_notional - exposure["gross_notional"], 0.0)
    gross_pct = (exposure["gross_notional"] / equity * 100.0) if equity > 0 else 0.0
    headroom_pct = (headroom / equity * 100.0) if equity > 0 else 0.0

    return {
        "equity": equity,
        "gross_notional": exposure["gross_notional"],
        "gross_pct": gross_pct,
        "n_open": exposure["count"],
        "max_concurrent": max_concurrent,
        "headroom": headroom,
        "headroom_pct": headroom_pct,
        "max_position_pct": max_position_pct,
        "max_gross_pct": max_gross_pct,
        "by_pair": exposure["by_pair"],
    }
