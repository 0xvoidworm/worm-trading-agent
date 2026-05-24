"""Hybrid strategy scaffold — 70% core / 30% opportunistic spot allocation.

Phase 1 = surface API (capital split + candidate hooks). Phase 2 = wire LLM
to populate `select_candidate()` from news + price + sentiment scan.

Design intent (per Worm directive 2026-05-23):
- 70% USDT idle: ammo for event-driven entries (can be parked di Binance
  Simple Earn ~3% APR if not in use). NOT auto-subscribed yet — operator
  controls earn cycle.
- 30% opportunistic: max 1-2 positions, event/narrative-driven entries.
- Trade frequency: 2-4/month (low frequency, high conviction).

NOT in Phase 1:
- LLM call for thesis generation
- Auto-execution loop (manual CLI trigger only)
- Real-time news ingest beyond existing daemon RSS
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


# Default split (overridable via cfg)
DEFAULT_CORE_PCT = 70.0
DEFAULT_OPPORTUNISTIC_PCT = 30.0


@dataclass
class CapitalAllocation:
    equity: float
    core_pct: float
    core_usdt: float
    opportunistic_pct: float
    opportunistic_usdt: float
    max_per_position_usdt: float       # 30% / max_concurrent
    max_concurrent: int

    def as_dict(self) -> dict:
        return {
            "equity": round(self.equity, 4),
            "core_pct": self.core_pct,
            "core_usdt": round(self.core_usdt, 4),
            "opportunistic_pct": self.opportunistic_pct,
            "opportunistic_usdt": round(self.opportunistic_usdt, 4),
            "max_per_position_usdt": round(self.max_per_position_usdt, 4),
            "max_concurrent": self.max_concurrent,
        }


@dataclass
class TradeCandidate:
    """Output of candidate scanner — proposal not commitment.

    `confidence` 0-10 (LLM-rated). `thesis` = freeform rationale.
    `kind` = "EVENT" | "NARRATIVE" | "MANUAL".
    """
    symbol: str
    side: str                # "BUY" only in spot Phase 1
    usdt_amount: float
    kind: str
    thesis: str
    confidence: float
    source_refs: list[str]   # URLs / IDs of news / tweet that inspired


# ─── allocation ────────────────────────────────────────────────────────────

def allocate_capital(
    equity: float,
    cfg: dict,
    core_pct: float = DEFAULT_CORE_PCT,
) -> CapitalAllocation:
    """Compute 70/30 split for current equity.

    cfg.portfolio.max_concurrent_trades caps the per-position chunk so we
    don't put 30% in one trade if the policy is 2 concurrent.
    """
    pcfg = cfg.get("portfolio") or {}
    max_concurrent = int(pcfg.get("max_concurrent_trades", 2))
    if core_pct < 0 or core_pct > 100:
        raise ValueError(f"core_pct must be 0-100, got {core_pct}")
    opp_pct = 100.0 - core_pct
    core_usdt = equity * core_pct / 100.0
    opp_usdt = equity * opp_pct / 100.0
    per_pos = (opp_usdt / max_concurrent) if max_concurrent > 0 else 0.0
    return CapitalAllocation(
        equity=equity,
        core_pct=core_pct,
        core_usdt=core_usdt,
        opportunistic_pct=opp_pct,
        opportunistic_usdt=opp_usdt,
        max_per_position_usdt=per_pos,
        max_concurrent=max_concurrent,
    )


# ─── candidate scanners (Phase 1 = stubs) ─────────────────────────────────

def propose_event_trade(
    storage,
    cfg: dict,
    allocation: CapitalAllocation,
    broker=None,
) -> Optional[TradeCandidate]:
    """Phase 2: scan recent news for Binance event keywords.

    Returns highest-scored EVENT candidate if any. Allocation determines
    usdt_amount = allocation.max_per_position_usdt. Caller decides whether
    to actually trade.
    """
    from .event_scanner import scan_news_events
    events = scan_news_events(storage, lookback_hours=6, limit=50)
    if not events:
        return None
    top = events[0]
    if top.score < 5.0:
        return None
    return TradeCandidate(
        symbol=top.symbol,
        side="BUY",
        usdt_amount=allocation.max_per_position_usdt,
        kind="EVENT",
        thesis=top.rationale,
        confidence=top.score,
        source_refs=[top.source_url] if top.source_url else [],
    )


def propose_narrative_trade(
    storage,
    cfg: dict,
    allocation: CapitalAllocation,
    broker=None,
) -> Optional[TradeCandidate]:
    """Phase 2: CoinGecko trending → Binance-tradeable filter.

    Requires broker for tradeable check. Returns highest-scored NARRATIVE
    candidate if any tradeable.
    """
    if broker is None:
        return None
    from .event_scanner import scan_narrative_trending
    narrative = scan_narrative_trending(broker)
    valid = [c for c in narrative if c.event_type != "ERROR" and c.score >= 5.0]
    if not valid:
        return None
    top = valid[0]
    return TradeCandidate(
        symbol=top.symbol,
        side="BUY",
        usdt_amount=allocation.max_per_position_usdt,
        kind="NARRATIVE",
        thesis=top.rationale,
        confidence=top.score,
        source_refs=[],
    )


def manual_candidate(
    symbol: str,
    usdt_amount: float,
    thesis: str = "manual operator decision",
    confidence: float = 5.0,
) -> TradeCandidate:
    """Wrap operator manual input as TradeCandidate (uniform interface)."""
    return TradeCandidate(
        symbol=symbol,
        side="BUY",
        usdt_amount=float(usdt_amount),
        kind="MANUAL",
        thesis=thesis,
        confidence=float(confidence),
        source_refs=[],
    )


# ─── helpers for Phase 2 LLM context ──────────────────────────────────────

def get_recent_news_context(
    storage,
    hours: int = 24,
    limit: int = 30,
) -> pd.DataFrame:
    """Recent news for LLM scan input (Phase 2). Empty df if daemon idle."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return storage._conn.execute(
        """
        SELECT published_at, source, title, url, symbols
        FROM news
        WHERE published_at >= ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [cutoff, limit],
    ).fetchdf()


def get_recent_price_context(
    storage,
    symbol: str,
    exchange: str = "bybit",
    timeframe: str = "1h",
    lookback_h: int = 168,
) -> pd.DataFrame:
    """Recent OHLCV for symbol — feed LLM untuk price-action context."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_h)
    return storage._conn.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE exchange=? AND symbol=? AND timeframe=? AND ts >= ?
        ORDER BY ts
        """,
        [exchange, symbol, timeframe, cutoff],
    ).fetchdf()
