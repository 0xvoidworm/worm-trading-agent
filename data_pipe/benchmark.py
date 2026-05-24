"""Performance benchmark vs BTC hold reference.

Honest accountability — am I generating alpha vs simply holding BTC?

Methodology:
1. start_ts = first non-test trade entry in trade_journal
2. agent_return = cumulative realized PnL / starting_capital
3. btc_return = (btc_close_now - btc_close_at_start) / btc_close_at_start
4. alpha = agent_return - btc_return

Caveats:
- Realized PnL only (unrealized excluded)
- Friction (fees + slippage) already in realized PnL
- BTC close from local DB (Bybit perp ≈ Binance spot within bps)
- Time-weighted comparison: same N days held
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd


@dataclass
class BenchmarkResult:
    start_ts: datetime
    end_ts: datetime
    days_elapsed: float
    starting_capital: float
    agent_equity_realized: float
    agent_return_pct: float
    btc_start_price: float
    btc_end_price: float
    btc_return_pct: float
    alpha_pct: float          # agent - btc
    n_closed_trades: int
    realized_pnl_usdt: float

    def to_dict(self) -> dict:
        return {
            "start_ts": self.start_ts.isoformat() if hasattr(self.start_ts, "isoformat") else str(self.start_ts),
            "end_ts": self.end_ts.isoformat() if hasattr(self.end_ts, "isoformat") else str(self.end_ts),
            "days_elapsed": round(self.days_elapsed, 2),
            "starting_capital": round(self.starting_capital, 4),
            "agent_equity_realized": round(self.agent_equity_realized, 4),
            "agent_return_pct": round(self.agent_return_pct, 3),
            "btc_start_price": round(self.btc_start_price, 2),
            "btc_end_price": round(self.btc_end_price, 2),
            "btc_return_pct": round(self.btc_return_pct, 3),
            "alpha_pct": round(self.alpha_pct, 3),
            "n_closed_trades": self.n_closed_trades,
            "realized_pnl_usdt": round(self.realized_pnl_usdt, 4),
        }


def compute_benchmark(
    storage,
    starting_capital: float = 50.0,
    exchange: str = "bybit",
    timeframe: str = "1h",
) -> Optional[BenchmarkResult]:
    """Compute agent return vs BTC hold over same period.

    Returns None if no closed trades yet (nothing to compare).
    """
    # First trade entry timestamp (non-test)
    first_row = storage._conn.execute(
        "SELECT MIN(ts) FROM trade_journal "
        "WHERE thesis IS NOT NULL "
        "  AND thesis NOT LIKE '[TEST_ARTIFACT%' "
        "  AND thesis NOT LIKE '%DRY%'"
    ).fetchone()
    if first_row is None or first_row[0] is None:
        return None

    start_ts = first_row[0]
    if hasattr(start_ts, "to_pydatetime"):
        start_ts = start_ts.to_pydatetime()
    end_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # Realized PnL + trade count
    pnl_row = storage._conn.execute(
        "SELECT COALESCE(SUM(pnl), 0), COUNT(*) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL "
        "  AND (thesis IS NULL OR thesis NOT LIKE '[TEST_ARTIFACT%')"
    ).fetchone()
    realized_pnl = float(pnl_row[0])
    n_closed = int(pnl_row[1])

    agent_equity = starting_capital + realized_pnl
    agent_return = (realized_pnl / starting_capital * 100) if starting_capital > 0 else 0.0

    # BTC start price = closest bar <= start_ts
    btc_start_row = storage._conn.execute(
        "SELECT close FROM ohlcv "
        "WHERE exchange=? AND symbol='BTC/USDT' AND timeframe=? AND ts <= ? "
        "ORDER BY ts DESC LIMIT 1",
        [exchange, timeframe, start_ts],
    ).fetchone()
    btc_end_row = storage._conn.execute(
        "SELECT close FROM ohlcv "
        "WHERE exchange=? AND symbol='BTC/USDT' AND timeframe=? "
        "ORDER BY ts DESC LIMIT 1",
        [exchange, timeframe],
    ).fetchone()
    if not btc_start_row or not btc_end_row:
        return None

    btc_start = float(btc_start_row[0])
    btc_end = float(btc_end_row[0])
    btc_return = (btc_end - btc_start) / btc_start * 100 if btc_start > 0 else 0.0

    days_elapsed = (end_ts - start_ts).total_seconds() / 86400

    return BenchmarkResult(
        start_ts=start_ts,
        end_ts=end_ts,
        days_elapsed=days_elapsed,
        starting_capital=starting_capital,
        agent_equity_realized=agent_equity,
        agent_return_pct=agent_return,
        btc_start_price=btc_start,
        btc_end_price=btc_end,
        btc_return_pct=btc_return,
        alpha_pct=agent_return - btc_return,
        n_closed_trades=n_closed,
        realized_pnl_usdt=realized_pnl,
    )


def compute_equity_curve_with_benchmark(
    storage,
    starting_capital: float = 50.0,
    exchange: str = "bybit",
    timeframe: str = "1h",
) -> pd.DataFrame:
    """Time-series: per closed trade, compute cumulative agent equity AND
    what BTC hold equivalent would be at same timestamp.

    Returns DataFrame with cols: ts, pair, pnl, cum_pnl, agent_equity,
    btc_hold_equivalent, alpha_pct.
    """
    df = storage._conn.execute(
        "SELECT exit_ts, pair, pnl FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL AND exit_ts IS NOT NULL "
        "  AND (thesis IS NULL OR thesis NOT LIKE '[TEST_ARTIFACT%') "
        "ORDER BY exit_ts"
    ).fetchdf()
    if df.empty:
        return pd.DataFrame(columns=[
            "ts", "pair", "pnl", "cum_pnl", "agent_equity",
            "btc_hold_equiv", "alpha_pct",
        ])

    # BTC start price = closest bar <= first exit
    first_exit = df["exit_ts"].iloc[0]
    btc_start_row = storage._conn.execute(
        "SELECT close FROM ohlcv "
        "WHERE exchange=? AND symbol='BTC/USDT' AND timeframe=? AND ts <= ? "
        "ORDER BY ts DESC LIMIT 1",
        [exchange, timeframe, first_exit],
    ).fetchone()
    if not btc_start_row:
        return pd.DataFrame()
    btc_start = float(btc_start_row[0])

    df = df.copy()
    df["cum_pnl"] = df["pnl"].cumsum()
    df["agent_equity"] = starting_capital + df["cum_pnl"]
    # Get BTC close at each exit_ts
    btc_prices = []
    for ts in df["exit_ts"]:
        row = storage._conn.execute(
            "SELECT close FROM ohlcv "
            "WHERE exchange=? AND symbol='BTC/USDT' AND timeframe=? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            [exchange, timeframe, ts],
        ).fetchone()
        btc_prices.append(float(row[0]) if row else None)
    df["btc_close"] = btc_prices
    df["btc_hold_equiv"] = starting_capital * df["btc_close"] / btc_start
    df["agent_return_pct"] = (df["agent_equity"] / starting_capital - 1) * 100
    df["btc_return_pct"] = (df["btc_hold_equiv"] / starting_capital - 1) * 100
    df["alpha_pct"] = df["agent_return_pct"] - df["btc_return_pct"]
    return df[["exit_ts", "pair", "pnl", "cum_pnl", "agent_equity",
               "btc_hold_equiv", "agent_return_pct", "btc_return_pct", "alpha_pct"]]
