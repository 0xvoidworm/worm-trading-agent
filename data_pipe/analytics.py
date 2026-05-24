"""Performance analytics from trade_journal.

Computes metrics for risk/PnL/win-rate analysis across multiple windows.
Read-only — safe to call alongside daemon writer.

Metrics:
  - Total PnL (realized), avg PnL/trade, best/worst trade
  - Win rate (% of CLOSED trades with pnl > 0)
  - Avg hold time (entry → exit)
  - By-symbol breakdown
  - By-kind (EVENT/NARRATIVE/MANUAL based on thesis tag) breakdown
  - Equity curve points

Time windows: all-time, last_24h, last_7d, last_30d.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd


WINDOWS = {
    "all": None,
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@dataclass
class WindowStats:
    window: str
    n_closed: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_breakeven: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    median_pnl: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0
    win_rate_pct: float = 0.0
    avg_hold_hours: float = 0.0
    by_symbol: dict = field(default_factory=dict)
    by_kind: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "window": self.window,
            "n_closed": self.n_closed,
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "n_breakeven": self.n_breakeven,
            "total_pnl": round(self.total_pnl, 4),
            "avg_pnl": round(self.avg_pnl, 4),
            "median_pnl": round(self.median_pnl, 4),
            "best_pnl": round(self.best_pnl, 4),
            "worst_pnl": round(self.worst_pnl, 4),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "avg_hold_hours": round(self.avg_hold_hours, 2),
            "by_symbol": {k: round(v, 4) for k, v in self.by_symbol.items()},
            "by_kind": {k: round(v, 4) for k, v in self.by_kind.items()},
        }


def _classify_kind(thesis: str) -> str:
    """Extract trade kind from thesis tag (set by live_executor)."""
    if not thesis:
        return "UNKNOWN"
    t = thesis.upper()
    if "LIVE_LLM DRY" in t:
        return "DRY"
    if "LIVE_LLM" in t or "LIVE_CLOSE" in t:
        return "LIVE"
    if "[TEST_ARTIFACT" in t:
        return "TEST"
    return "OTHER"


def fetch_closed_trades(storage, since: Optional[datetime] = None) -> pd.DataFrame:
    sql = (
        "SELECT id, ts, pair, direction, entry_price, size, exit_ts, "
        "exit_price, pnl, thesis, notes "
        "FROM trade_journal WHERE status='CLOSED' "
        "  AND (thesis IS NULL OR thesis NOT LIKE '[TEST_ARTIFACT%')"
    )
    params: list = []
    if since is not None:
        sql += " AND exit_ts >= ?"
        params.append(since)
    sql += " ORDER BY exit_ts"
    return storage._conn.execute(sql, params).fetchdf()


def fetch_open_trades(storage) -> pd.DataFrame:
    return storage._conn.execute(
        "SELECT id, ts, pair, direction, entry_price, size, thesis "
        "FROM trade_journal WHERE status='OPEN' "
        "  AND (thesis IS NULL OR thesis NOT LIKE '[TEST_ARTIFACT%')"
    ).fetchdf()


def compute_window_stats(closed_df: pd.DataFrame, window_label: str) -> WindowStats:
    s = WindowStats(window=window_label)
    if closed_df.empty:
        return s
    df = closed_df.copy()
    df = df[df["pnl"].notna()]
    if df.empty:
        return s

    s.n_closed = int(len(df))
    s.n_wins = int((df["pnl"] > 0).sum())
    s.n_losses = int((df["pnl"] < 0).sum())
    s.n_breakeven = int((df["pnl"] == 0).sum())
    s.total_pnl = float(df["pnl"].sum())
    s.avg_pnl = float(df["pnl"].mean())
    s.median_pnl = float(df["pnl"].median())
    s.best_pnl = float(df["pnl"].max())
    s.worst_pnl = float(df["pnl"].min())
    s.win_rate_pct = (s.n_wins / s.n_closed * 100) if s.n_closed > 0 else 0.0

    # Hold time
    df["hold_h"] = (
        (pd.to_datetime(df["exit_ts"]) - pd.to_datetime(df["ts"]))
        .dt.total_seconds() / 3600
    )
    s.avg_hold_hours = float(df["hold_h"].mean()) if not df["hold_h"].isna().all() else 0.0

    # By symbol
    by_sym = df.groupby("pair")["pnl"].sum().to_dict()
    s.by_symbol = {str(k): float(v) for k, v in by_sym.items()}

    # By kind (parsed from thesis)
    df["kind"] = df["thesis"].fillna("").apply(_classify_kind)
    by_kind = df.groupby("kind")["pnl"].sum().to_dict()
    s.by_kind = {str(k): float(v) for k, v in by_kind.items()}

    return s


def compute_all_windows(storage) -> dict[str, WindowStats]:
    now = datetime.now(timezone.utc)
    out: dict[str, WindowStats] = {}
    for label, delta in WINDOWS.items():
        if delta is None:
            df = fetch_closed_trades(storage)
        else:
            df = fetch_closed_trades(storage, since=now - delta)
        out[label] = compute_window_stats(df, label)
    return out


def compute_equity_curve(storage, starting_capital: float = 50.0) -> pd.DataFrame:
    """Time-series of cumulative realized PnL + equity."""
    df = fetch_closed_trades(storage)
    if df.empty:
        return pd.DataFrame(columns=["exit_ts", "pnl", "cum_pnl", "equity"])
    df = df[df["pnl"].notna()].sort_values("exit_ts").reset_index(drop=True)
    df["cum_pnl"] = df["pnl"].cumsum()
    df["equity"] = starting_capital + df["cum_pnl"]
    return df[["exit_ts", "pair", "pnl", "cum_pnl", "equity"]]


def portfolio_snapshot(storage, cfg: dict) -> dict:
    """Current portfolio state — equity, exposure, drawdown, killswitch dist."""
    from .portfolio import get_account_state
    from .risk_guard import get_equity_drawdown, get_daily_pnl_state

    state = get_account_state(storage, cfg)
    dd = get_equity_drawdown(storage, cfg)
    pnl = get_daily_pnl_state(storage, cfg)

    return {
        "equity_realized": round(state["equity"], 4),
        "gross_notional_open": round(state["gross_notional"], 4),
        "gross_pct": round(state["gross_pct"], 2),
        "n_open": state["n_open"],
        "max_concurrent": state["max_concurrent"],
        "headroom_usdt": round(state["headroom"], 4),
        "starting_capital": round(dd["starting_capital"], 4),
        "equity_mtm": round(dd["equity_mtm"], 4),
        "unrealized_pnl": round(dd["unrealized"], 4),
        "drawdown_pct_vs_start": round(dd["drawdown_pct"], 2),
        "drawdown_remaining_to_floor": round(dd["floor_pct"] - dd["drawdown_pct"], 2),
        "daily_pnl": round(pnl["daily_pnl"], 4),
        "daily_pnl_pct": round(pnl["daily_pnl_pct"], 2),
        "daily_status": pnl["status"],
    }
