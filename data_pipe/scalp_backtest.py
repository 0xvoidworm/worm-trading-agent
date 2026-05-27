"""Scalp backtest engine (P3) — THE GATE.

Smell test (`forward_return_peek`) cuma cek ARAH. Ini ngitung economics beneran:
entry @ open bar t+1 → walk bar-by-bar → cek SL/TP touch pakai high/low (intrabar) →
exit di TP / SL / timeout. Cost fee+spread dipungut round-trip. One-position-at-a-time
(realistis 1 akun kecil; sinyal overlap di-SKIP selama in-position).

═══ ANTI OPTIMISTIC-BIAS (sumber hantu +67%) ═══
1. **SL-first**: kalau 1 bar nyentuh SL DAN TP (high≥TP & low≤SL), asumsi SL kena duluan
   (gak tau path intrabar → worst-case). Bikin hasil PESIMIS, bukan optimis.
2. **No-lookahead**: entry = open t+1 (signal udah encode), exit cuma pakai bar ≥ entry.
3. **Validity guard**: bracket harus bener (LONG: sl<entry<tp; SHORT: tp<entry<sl) — skip
   yang gap-invalid (cegah instant-exit artefak).
4. **Walk-forward folds**: strategi FIX dijalanin di N segmen waktu berurutan → edge mesti
   stabil, bukan cuma ada di 1 rezim. (Param-fitting per fold = layer berikut.)
5. Fill di harga TP/SL persis; slippage diproksi via spread_bps. Konservatif buat size mini.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class CostModel:
    """Biaya per-side dalam bps. Default = Binance USDT-M taker (verified trade#1 ~4bp/side)."""
    taker_bps: float = 4.0
    maker_bps: float = 1.8
    spread_bps: float = 1.0       # crossing half-spread per taker side
    entry_maker: bool = False     # fade = bisa limit/maker (mean_reversion)
    exit_maker: bool = False      # TP-as-limit bisa maker; SL market = taker

    def entry_cost_bps(self) -> float:
        return self.maker_bps if self.entry_maker else self.taker_bps + self.spread_bps

    def exit_cost_bps(self) -> float:
        return self.maker_bps if self.exit_maker else self.taker_bps + self.spread_bps

    def round_trip_bps(self) -> float:
        return self.entry_cost_bps() + self.exit_cost_bps()


@dataclass
class Trade:
    entry_ts: object
    exit_ts: object
    direction: str
    entry_price: float
    exit_price: float
    outcome: str            # TP | SL | TIMEOUT
    hold_bars: int
    gross_bps: float
    net_bps: float


@dataclass
class BTStats:
    n: int = 0
    skipped_invalid: int = 0
    skipped_overlap: int = 0
    win_rate: float = 0.0
    avg_net_bps: float = 0.0
    median_net_bps: float = 0.0
    total_net_bps: float = 0.0
    gross_avg_bps: float = 0.0
    profit_factor: float = 0.0
    expectancy_bps: float = 0.0       # = avg_net_bps (per-trade EV net)
    max_dd_bps: float = 0.0
    avg_hold_bars: float = 0.0
    equity_mult: float = 1.0          # compounding (1+net) product
    outcomes: dict = field(default_factory=dict)

    def line(self) -> str:
        return (f"n={self.n:5d} win={self.win_rate:.3f} EVnet={self.avg_net_bps:+6.2f}bps "
                f"gross={self.gross_avg_bps:+6.2f} PF={self.profit_factor:4.2f} "
                f"tot={self.total_net_bps:+8.0f}bps eq×{self.equity_mult:.3f} "
                f"DD={self.max_dd_bps:+.0f}bps hold={self.avg_hold_bars:.1f} {self.outcomes}")


def _stats(trades: list[Trade], skipped_invalid: int = 0, skipped_overlap: int = 0) -> BTStats:
    if not trades:
        return BTStats(n=0, skipped_invalid=skipped_invalid, skipped_overlap=skipped_overlap)
    net = np.array([t.net_bps for t in trades], dtype=float)
    gross = np.array([t.gross_bps for t in trades], dtype=float)
    holds = np.array([t.hold_bars for t in trades], dtype=float)
    wins = net[net > 0]
    losses = net[net < 0]
    pf = (wins.sum() / abs(losses.sum())) if losses.size and losses.sum() != 0 else float("inf")
    eq = np.cumprod(1.0 + net / 1e4)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    outcomes: dict = {}
    for t in trades:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    return BTStats(
        n=len(trades), skipped_invalid=skipped_invalid, skipped_overlap=skipped_overlap,
        win_rate=float((net > 0).mean()),
        avg_net_bps=round(float(net.mean()), 3),
        median_net_bps=round(float(np.median(net)), 3),
        total_net_bps=round(float(net.sum()), 1),
        gross_avg_bps=round(float(gross.mean()), 3),
        profit_factor=round(float(pf), 3) if np.isfinite(pf) else float("inf"),
        expectancy_bps=round(float(net.mean()), 3),
        max_dd_bps=round(float(dd.min() * 1e4), 1),
        avg_hold_bars=round(float(holds.mean()), 2),
        equity_mult=round(float(eq[-1]), 4),
        outcomes=outcomes,
    )


def simulate(df: pd.DataFrame, signals: pd.DataFrame,
             cost: CostModel = CostModel(), max_hold_bars: int = 30,
             allow_overlap: bool = False) -> tuple[list[Trade], BTStats]:
    """Jalanin sinyal di df. Return (trades, stats). df & signals = full (engine filter internal)."""
    df = df.reset_index(drop=True)
    o, h, l, c = (df["open"].to_numpy(), df["high"].to_numpy(),
                  df["low"].to_numpy(), df["close"].to_numpy())
    ts = df["ts"].to_numpy()                              # buat storage (np.datetime64 ok)
    ts_ns = df["ts"].to_numpy().astype("datetime64[ns]").astype("int64")  # DuckDB kasih [us] → normalize ns
    n_bars = len(df)
    pos_of_ts = {int(t): i for i, t in enumerate(ts_ns)}  # Timestamp vs datetime64 mismatch → pakai ns
    rt_cost = cost.round_trip_bps()

    sigs = signals.sort_values("ts")
    trades: list[Trade] = []
    busy_until = -1
    skipped_invalid = skipped_overlap = 0

    for s in sigs.itertuples(index=False):
        sig_i = pos_of_ts.get(int(pd.Timestamp(s.ts).value))
        if sig_i is None:
            continue
        entry_i = sig_i + 1
        if entry_i >= n_bars:
            continue
        if not allow_overlap and entry_i <= busy_until:
            skipped_overlap += 1
            continue

        entry = o[entry_i]                 # eksekusi = open bar t+1 (self-consistent)
        sl, tp = float(s.sl), float(s.tp)
        is_long = s.direction == "LONG"
        # validity guard: bracket harus ngapit entry di sisi bener
        if is_long and not (sl < entry < tp):
            skipped_invalid += 1; continue
        if (not is_long) and not (tp < entry < sl):
            skipped_invalid += 1; continue

        end = min(entry_i + max_hold_bars, n_bars - 1)
        exit_i = None; exit_price = None; outcome = None
        for j in range(entry_i, end + 1):
            if is_long:
                hit_sl, hit_tp = l[j] <= sl, h[j] >= tp
            else:
                hit_sl, hit_tp = h[j] >= sl, l[j] <= tp
            if hit_sl:                     # SL-first (konservatif, termasuk kasus dua-duanya)
                exit_i, exit_price, outcome = j, sl, "SL"; break
            if hit_tp:
                exit_i, exit_price, outcome = j, tp, "TP"; break
        if exit_i is None:
            exit_i, exit_price, outcome = end, c[end], "TIMEOUT"

        gross = (exit_price - entry) / entry
        if not is_long:
            gross = -gross
        gross_bps = gross * 1e4
        net_bps = gross_bps - rt_cost
        trades.append(Trade(
            entry_ts=ts[entry_i], exit_ts=ts[exit_i], direction=s.direction,
            entry_price=entry, exit_price=exit_price, outcome=outcome,
            hold_bars=exit_i - entry_i, gross_bps=gross_bps, net_bps=net_bps,
        ))
        busy_until = exit_i

    return trades, _stats(trades, skipped_invalid, skipped_overlap)


def walk_forward(df: pd.DataFrame, signals: pd.DataFrame, n_folds: int = 6,
                 cost: CostModel = CostModel(), max_hold_bars: int = 30,
                 allow_overlap: bool = False) -> list[BTStats]:
    """Split timeline jadi n segmen berurutan; jalanin strategi FIX di tiap fold.
    Edge mesti STABIL antar fold (bukan cuma 1 rezim). Sinyal di-bucket by entry time;
    exit boleh spill ke bar fold berikut (data kontigu, realistis).
    """
    df = df.reset_index(drop=True)
    edges = np.linspace(0, len(df), n_folds + 1, dtype=int)
    out: list[BTStats] = []
    for k in range(n_folds):
        lo_ts, hi_ts = df["ts"].iloc[edges[k]], df["ts"].iloc[min(edges[k + 1], len(df) - 1)]
        fold_sig = signals[(signals["ts"] >= lo_ts) & (signals["ts"] < hi_ts)]
        _, st = simulate(df, fold_sig, cost, max_hold_bars, allow_overlap)
        out.append(st)
    return out
