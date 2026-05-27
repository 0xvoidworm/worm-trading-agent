"""Market-neutral pairs long/short (futures) — true dollar-neutral.

Long leg A + short leg B (β-hedged) → cuan dari konvergensi spread (z-score
mean-reversion). Futures bikin SHORT beneran mungkin (di spot cuma approximate
lewat ratio long-only), fee perp lebih murah, + funding dua leg.

PnL DIHITUNG DARI HARGA LEG BENERAN dengan β DIKUNCI SAAT ENTRY:
  LONG_SPREAD  (long A, short β·B): pnl = ret_A − β_entry·ret_B
  SHORT_SPREAD (short A, long β·B): pnl = β_entry·ret_B − ret_A
dinormalisasi /(1+β) = return on gross notional. (BUKAN Δspread — spread pake
β refit + rolling-mean → lompatan refit = PnL palsu. Bug itu udah dibuang.)

Input panel butuh kolom: ts, z_score, hedge_ratio, close_a, close_b
(+ optional is_cointegrated). Validasi via walk-forward OOS.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd


DEFAULT_ENTRY_Z = 2.0
DEFAULT_EXIT_Z = 0.5
DEFAULT_KILL_Z = 4.0
DEFAULT_FEE_ROUNDTRIP_PCT = 0.16   # 2 leg × perp taker 0.04% × (entry+exit)
DEFAULT_MAX_HOLD_BARS = 21 * 24


@dataclass
class PairTrade:
    pair: str
    side: str
    entry_ts: datetime
    exit_ts: datetime
    entry_z: float
    exit_z: float
    beta: float
    hold_hours: float
    gross_pnl_pct: float
    fees_pct: float
    net_pct: float
    exit_reason: str


@dataclass
class PairSummary:
    pair: str
    n_trades: int
    total_net_pct: float
    avg_net_per_trade_pct: float
    win_rate_pct: float
    avg_hold_hours: float
    ann_return_pct: float
    sharpe_trades: float
    max_drawdown_pct: float
    years_span: float
    pct_cointegrated_bars: float
    trade_returns: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("trade_returns", None)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def simulate_pairs(
    panel: pd.DataFrame,
    pair: str = "?",
    entry_z: float = DEFAULT_ENTRY_Z,
    exit_z: float = DEFAULT_EXIT_Z,
    kill_z: float = DEFAULT_KILL_Z,
    fee_roundtrip_pct: float = DEFAULT_FEE_ROUNDTRIP_PCT,
    max_hold_bars: int = DEFAULT_MAX_HOLD_BARS,
    require_cointegrated: bool = False,
    interval_hours: float = 1.0,
) -> PairSummary:
    """Backtest long/short pair. PnL dari harga leg + β dikunci saat entry."""
    need = {"ts", "z_score", "hedge_ratio", "close_a", "close_b"}
    if panel is None or panel.empty or not need.issubset(panel.columns):
        return PairSummary(pair, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0.0)

    p = panel.sort_values("ts").reset_index(drop=True)
    p = p.dropna(subset=["close_a", "close_b", "z_score", "hedge_ratio"])
    if len(p) < 50:
        return PairSummary(pair, 0, 0, 0, 0, 0, 0, 0, 0, 0.01, 0.0)
    z = p["z_score"].astype(float).to_numpy()
    ca = p["close_a"].astype(float).to_numpy()
    cb = p["close_b"].astype(float).to_numpy()
    beta = p["hedge_ratio"].astype(float).to_numpy()
    ts = list(p["ts"])
    coint = (p["is_cointegrated"].astype(bool).to_numpy()
             if "is_cointegrated" in p.columns else np.ones(len(p), bool))
    n = len(z)

    trades: list[PairTrade] = []
    state = "FLAT"
    ei: int | None = None       # entry index

    def _close(j: int, reason: str) -> None:
        nonlocal state, ei
        b0 = abs(float(beta[ei]))
        ret_a = ca[j] / ca[ei] - 1.0
        ret_b = cb[j] / cb[ei] - 1.0
        if state == "LONG_SPREAD":          # long A, short β·B
            raw = ret_a - b0 * ret_b
        else:                                # short A, long β·B
            raw = b0 * ret_b - ret_a
        gross = raw / (1.0 + b0) * 100.0     # return on gross notional
        net = gross - fee_roundtrip_pct
        trades.append(PairTrade(
            pair=pair, side=state, entry_ts=ts[ei], exit_ts=ts[j],
            entry_z=float(z[ei]), exit_z=float(z[j]), beta=b0,
            hold_hours=(j - ei) * interval_hours,
            gross_pnl_pct=gross, fees_pct=fee_roundtrip_pct, net_pct=net,
            exit_reason=reason,
        ))
        state = "FLAT"
        ei = None

    for i in range(n):
        zi = z[i]
        if state == "FLAT":
            if require_cointegrated and not coint[i]:
                continue
            if zi <= -entry_z:
                state, ei = "LONG_SPREAD", i
            elif zi >= entry_z:
                state, ei = "SHORT_SPREAD", i
        else:
            held = i - ei
            if abs(zi) <= exit_z:
                _close(i, "revert")
            elif abs(zi) >= kill_z:
                _close(i, "kill")
            elif held >= max_hold_bars:
                _close(i, "max_hold")
            elif state == "LONG_SPREAD" and zi >= entry_z:
                _close(i, "flip")
            elif state == "SHORT_SPREAD" and zi <= -entry_z:
                _close(i, "flip")
    if state != "FLAT" and ei is not None:
        _close(n - 1, "end")

    rets = [t.net_pct for t in trades]
    years = max((ts[-1] - ts[0]).days / 365.25, 0.01) if n > 1 else 0.01
    if rets:
        arr = np.array(rets)
        win = float((arr > 0).mean() * 100)
        avg = float(arr.mean())
        avg_hold = float(np.mean([t.hold_hours for t in trades]))
        tpy = len(arr) / years
        sharpe = (float(arr.mean() / arr.std() * math.sqrt(max(tpy, 1)))
                  if arr.std() > 1e-9 else 0.0)
        eq = np.cumsum(arr)
        max_dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
        total = float(arr.sum())
    else:
        win = avg = avg_hold = sharpe = max_dd = total = 0.0

    return PairSummary(
        pair=pair, n_trades=len(trades), total_net_pct=total,
        avg_net_per_trade_pct=avg, win_rate_pct=win, avg_hold_hours=avg_hold,
        ann_return_pct=total / years, sharpe_trades=sharpe,
        max_drawdown_pct=max_dd, years_span=years,
        pct_cointegrated_bars=float(coint.mean() * 100),
        trade_returns=rets,
    )
