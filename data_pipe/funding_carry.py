"""Funding-rate carry strategy core — delta-neutral (long spot + short perp).

EDGE: manen funding yang dibayar long → short saat funding POSITIF, sambil
delta ≈ 0 (price PnL dua leg saling cancel). Income = Σ funding − fees − basis.

⚠️ Ini BUKAN `FUNDING_FLIP` (directional, udah direject). Ini market-neutral
*harvest* — beda fundamental.

Modul = simulasi backtest core + seleksi kandidat. Runner di
`scripts/backtest_funding_carry.py`.

ASUMSI MODEL (first-pass, sengaja konservatif):
- Delta-neutral → price PnL spot+perp cancel. Return ≈ funding − fees − basis_drift.
- Short perp NERIMA funding saat rate>0, MBAYAR saat rate<0 (masuk akru per interval).
- Fee round-trip: spot taker 0.1%×2 + perp taker 0.04%×2 = 0.28% notional (entry+exit).
- basis_cost: estimasi slippage 2-leg + konvergensi basis, per-trade.
- Tidak model likuidasi (short leg well-margined ~1x; delta-neutral). Funding flip
  ditangani exit rule (keluar saat funding < exit threshold).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd


DEFAULT_ENTRY_BPS = 1.0          # masuk kalau funding ≥ 1.0 bp/interval (0.01%)
DEFAULT_EXIT_BPS = 0.5           # keluar kalau funding < 0.5 bp
DEFAULT_FEE_ROUNDTRIP_PCT = 0.28  # spot 0.1%×2 + perp 0.04%×2
DEFAULT_BASIS_COST_PCT = 0.05    # slippage + basis convergence per trade


@dataclass
class CarryTrade:
    symbol: str
    entry_ts: datetime
    exit_ts: datetime
    n_intervals: int
    hold_hours: float
    funding_collected_pct: float   # gross funding manen (% notional)
    fees_pct: float
    basis_cost_pct: float
    net_pct: float                 # funding − fees − basis


@dataclass
class CarrySummary:
    symbol: str
    n_trades: int
    n_intervals_total: int
    n_intervals_in_market: int
    pct_time_in_market: float
    total_net_pct: float
    avg_net_per_trade_pct: float
    win_rate_pct: float
    avg_hold_hours: float
    ann_return_pct: float          # net tahunan rata-rata (additive / years span)
    sharpe_trades: float           # annualized, dari net per-trade (HEADLINE, incl fee)
    sharpe_deployed: float         # dari funding per-interval saat in-market (optimistic)
    max_drawdown_pct: float
    funding_collected_pct_total: float
    fees_pct_total: float
    years_span: float
    trade_returns: list[float] = field(default_factory=list)
    interval_returns: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("trade_returns", None)
        d.pop("interval_returns", None)
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


def simulate_carry(
    funding_df: pd.DataFrame,
    symbol: str = "?",
    entry_bps: float = DEFAULT_ENTRY_BPS,
    exit_bps: float = DEFAULT_EXIT_BPS,
    fee_roundtrip_pct: float = DEFAULT_FEE_ROUNDTRIP_PCT,
    basis_cost_pct: float = DEFAULT_BASIS_COST_PCT,
    smooth_intervals: int = 1,
    interval_hours: float = 8.0,
) -> CarrySummary:
    """Simulasi carry pada satu simbol.

    funding_df: kolom minimal `ts` (datetime) + `funding` (rate per interval,
    mis. 0.0001 = 1bp). Disort by ts.

    State machine: FLAT → (funding ≥ entry) → IN_CARRY → akru funding tiap
    interval → (funding < exit) → keluar (bayar fee+basis) → FLAT.
    """
    df = funding_df.sort_values("ts").reset_index(drop=True)
    rates = df["funding"].astype(float).to_numpy()
    ts = list(df["ts"])
    n = len(rates)
    entry_r = entry_bps / 10_000.0
    exit_r = exit_bps / 10_000.0
    if smooth_intervals and smooth_intervals > 1:
        signal = pd.Series(rates).rolling(smooth_intervals, min_periods=1).mean().to_numpy()
    else:
        signal = rates

    trades: list[CarryTrade] = []
    interval_returns: list[float] = []   # % funding per in-market interval
    in_market = False
    entry_idx: int | None = None
    funding_acc = 0.0

    def _close(end_idx: int) -> None:
        nonlocal in_market, entry_idx, funding_acc
        n_int = end_idx - entry_idx + 1
        net = funding_acc - fee_roundtrip_pct - basis_cost_pct
        trades.append(CarryTrade(
            symbol=symbol, entry_ts=ts[entry_idx], exit_ts=ts[end_idx],
            n_intervals=n_int, hold_hours=n_int * interval_hours,
            funding_collected_pct=funding_acc, fees_pct=fee_roundtrip_pct,
            basis_cost_pct=basis_cost_pct, net_pct=net,
        ))
        in_market = False
        entry_idx = None
        funding_acc = 0.0

    for i in range(n):
        r = rates[i]            # funding aktual interval ini
        sig = signal[i]         # sinyal (smoothed) buat keputusan entry/exit
        if in_market:
            funding_acc += r * 100.0          # short nerima funding (% notional)
            interval_returns.append(r * 100.0)
            if sig < exit_r:
                _close(i)
        else:
            if sig >= entry_r:
                in_market = True
                entry_idx = i
                funding_acc = r * 100.0
                interval_returns.append(r * 100.0)
    if in_market and entry_idx is not None:
        _close(n - 1)

    # ── stats ────────────────────────────────────────────────────────────
    trade_rets = [t.net_pct for t in trades]
    n_in_market = sum(t.n_intervals for t in trades)
    years = max((ts[-1] - ts[0]).days / 365.25, 0.01) if n > 1 else 0.01
    total_net = float(sum(trade_rets))
    funding_total = float(sum(t.funding_collected_pct for t in trades))
    fees_total = float(sum(t.fees_pct + t.basis_cost_pct for t in trades))

    if trade_rets:
        arr = np.array(trade_rets)
        win_rate = float((arr > 0).mean() * 100)
        avg_net = float(arr.mean())
        avg_hold = float(np.mean([t.hold_hours for t in trades]))
        trades_per_year = len(arr) / years
        sharpe_trades = (
            float(arr.mean() / arr.std() * math.sqrt(max(trades_per_year, 1)))
            if arr.std() > 1e-9 else 0.0
        )
        # max drawdown atas equity curve kumulatif (additive)
        eq = np.cumsum(arr)
        running_max = np.maximum.accumulate(eq)
        dd = running_max - eq
        max_dd = float(dd.max()) if len(dd) else 0.0
    else:
        win_rate = avg_net = avg_hold = sharpe_trades = max_dd = 0.0

    if interval_returns:
        ir = np.array(interval_returns)
        intervals_per_year = (24.0 / interval_hours) * 365
        sharpe_deployed = (
            float(ir.mean() / ir.std() * math.sqrt(intervals_per_year))
            if ir.std() > 1e-9 else 0.0
        )
    else:
        sharpe_deployed = 0.0

    return CarrySummary(
        symbol=symbol,
        n_trades=len(trades),
        n_intervals_total=n,
        n_intervals_in_market=n_in_market,
        pct_time_in_market=float(n_in_market / n * 100) if n else 0.0,
        total_net_pct=total_net,
        avg_net_per_trade_pct=avg_net,
        win_rate_pct=win_rate,
        avg_hold_hours=avg_hold,
        ann_return_pct=total_net / years,
        sharpe_trades=sharpe_trades,
        sharpe_deployed=sharpe_deployed,
        max_drawdown_pct=max_dd,
        funding_collected_pct_total=funding_total,
        fees_pct_total=fees_total,
        years_span=years,
        trade_returns=trade_rets,
        interval_returns=interval_returns,
    )


def infer_interval_hours(ts_series: pd.Series) -> float:
    """Tebak jarak antar funding event (jam) dari median delta ts."""
    s = pd.Series(pd.to_datetime(ts_series)).sort_values()
    if len(s) < 3:
        return 8.0
    deltas = s.diff().dropna().dt.total_seconds() / 3600.0
    med = float(deltas.median())
    return med if med > 0 else 8.0


def rank_carry_candidates(funding_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Ranking simbol by harvestable funding: avg rate, %positif, current."""
    rows = []
    for sym, df in funding_by_symbol.items():
        if df.empty:
            continue
        r = df.sort_values("ts")["funding"].astype(float)
        rows.append({
            "symbol": sym,
            "n": len(r),
            "avg_bps": float(r.mean() * 10_000),
            "median_bps": float(r.median() * 10_000),
            "pct_positive": float((r > 0).mean() * 100),
            "current_bps": float(r.iloc[-1] * 10_000),
            "ann_pct_if_const": float(r.mean() * (3 * 365) * 100),  # 8h → 3/day
        })
    return pd.DataFrame(rows).sort_values("avg_bps", ascending=False).reset_index(drop=True)
