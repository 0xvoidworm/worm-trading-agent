#!/usr/bin/env python3
"""Backtest CLI — REPLAY MODE.

Scan OHLCV historis bar-by-bar, re-detect signals (regime change, funding extreme,
z-spike) seakan daemon detector jalan di masa lalu, ukur forward P&L di multi-horizon.

Kenapa replay, bukan pakai table signal_events?
    signal_events hanya berisi event yang terjadi SEJAK daemon jalan (baru 1 hari).
    Untuk statistik meaningful butuh full sweep 30 hari data historis.

Output: table per (SIGNAL_TYPE, SUBTYPE, HORIZON) — N, WIN%, AVG%, MED%, MAX_DD%, SHARPE.

Usage:
    python scripts/backtest.py                                 # default full replay
    python scripts/backtest.py --skip-zspike                   # skip slowest replay
    python scripts/backtest.py --detail REGIME_CHANGE          # trade-by-trade @24h
    python scripts/backtest.py --detail Z_SPIKE --detail-horizon 4
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.regime import classify
from dashboard.cointegration import engle_granger


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market.duckdb"

# --- Backtest config ---
WARMUP_BARS = 200           # minimum history untuk classify() + engle_granger()
HORIZONS_H = [4, 24, 72]    # forward hold periods (bars = jam, TF 1h)
COST_SINGLE = 0.0011        # 0.11% round trip, bybit taker 0.055% × 2 sides
COST_PAIR = 0.0022          # 0.22% round trip, dua asset
FUNDING_EXTREME_THR = 0.001 # 0.1% per 8h → ~109% APR
Z_THRESHOLD = 2.0
ADF_P_MAX = 0.1             # skip pair kalau adf_p >= ini (non-cointegrated)


@dataclass
class Trade:
    signal_type: str
    subtype: str
    symbol: str
    entry_ts: pd.Timestamp
    entry_price: float       # for single asset; log-spread untuk pair
    direction: int           # +1 long, -1 short
    is_pair: bool = False
    returns: dict = field(default_factory=dict)   # horizon_h → post-cost return


# ─── Loaders ────────────────────────────────────────────────────────────────

def load_ohlcv(conn) -> dict[str, pd.DataFrame]:
    df = conn.execute(
        "SELECT symbol, ts, open, high, low, close, volume FROM ohlcv "
        "WHERE exchange='bybit' AND timeframe='1h' ORDER BY symbol, ts"
    ).fetchdf()
    return {sym: grp.reset_index(drop=True) for sym, grp in df.groupby("symbol")}


def load_funding(conn) -> dict[str, pd.DataFrame]:
    df = conn.execute(
        "SELECT symbol, ts, funding FROM funding_rate WHERE exchange='bybit' ORDER BY symbol, ts"
    ).fetchdf()
    return {sym: grp.reset_index(drop=True) for sym, grp in df.groupby("symbol")}


# ─── Replay: REGIME_CHANGE ──────────────────────────────────────────────────

def replay_regime(ohlcv_dict: dict[str, pd.DataFrame]) -> list[Trade]:
    """Per symbol, walk forward: classify 200-bar window, emit saat regime berubah
    ke trending_up/trending_down (skip transition ke ranging/chaos — no edge)."""
    trades = []
    for sym, df in ohlcv_dict.items():
        if len(df) < WARMUP_BARS + max(HORIZONS_H):
            continue
        prev_regime = None
        for i in range(WARMUP_BARS - 1, len(df)):
            window = df.iloc[i - WARMUP_BARS + 1 : i + 1]
            snap = classify(window)
            if prev_regime is None:
                prev_regime = snap.regime
                continue
            if snap.regime == prev_regime:
                continue
            # regime berubah → emit trade kalau regime baru directional
            if snap.regime in ("trending_up", "trending_down"):
                direction = 1 if snap.regime == "trending_up" else -1
                subtype = "REGIME_UP" if direction == 1 else "REGIME_DOWN"
                entry_price = float(df.iloc[i]["close"])
                entry_ts = pd.Timestamp(df.iloc[i]["ts"])
                t = Trade("REGIME_CHANGE", subtype, sym, entry_ts, entry_price, direction)
                for h in HORIZONS_H:
                    if i + h >= len(df):
                        continue
                    exit_price = float(df.iloc[i + h]["close"])
                    gross = direction * (exit_price - entry_price) / entry_price
                    t.returns[h] = gross - COST_SINGLE
                trades.append(t)
            prev_regime = snap.regime
    return trades


# ─── Replay: FUNDING_EXTREME ────────────────────────────────────────────────

def replay_funding(
    ohlcv_dict: dict[str, pd.DataFrame],
    funding_dict: dict[str, pd.DataFrame],
) -> list[Trade]:
    """Per symbol, tiap funding record dengan |rate| >= threshold: entry short-side
    yang bayar, ukur forward return."""
    trades = []
    for sym, f_df in funding_dict.items():
        if sym not in ohlcv_dict:
            continue
        ohlcv = ohlcv_dict[sym]
        ohlcv_ts = ohlcv["ts"].values.astype("datetime64[ns]")
        for _, row in f_df.iterrows():
            rate = float(row["funding"])
            if abs(rate) < FUNDING_EXTREME_THR:
                continue
            target_ts = pd.Timestamp(row["ts"]).to_datetime64()
            idx = int(np.searchsorted(ohlcv_ts, target_ts))
            if idx >= len(ohlcv):
                continue
            entry_price = float(ohlcv.iloc[idx]["close"])
            entry_ts = pd.Timestamp(ohlcv.iloc[idx]["ts"])
            direction = -1 if rate > 0 else 1  # fade sisi yang bayar
            subtype = "FUND_POS_FADE" if rate > 0 else "FUND_NEG_FADE"
            t = Trade("FUNDING_EXTREME", subtype, sym, entry_ts, entry_price, direction)
            for h in HORIZONS_H:
                if idx + h >= len(ohlcv):
                    continue
                exit_price = float(ohlcv.iloc[idx + h]["close"])
                gross = direction * (exit_price - entry_price) / entry_price
                t.returns[h] = gross - COST_SINGLE
            trades.append(t)
    return trades


# ─── Replay: Z_SPIKE (pair, cointegration) ──────────────────────────────────

def replay_zspike(
    ohlcv_dict: dict[str, pd.DataFrame],
    pairs: list[list[str]],
    console: Console,
) -> list[Trade]:
    """Per pair, rolling 200-bar engle_granger. Skip kalau adf_p >= 0.1 di window.
    Emit saat |z| crosses ±2 threshold dari sisi neutral (no re-emit while persisting)."""
    trades = []
    for pair in pairs:
        a, b = pair
        if a not in ohlcv_dict or b not in ohlcv_dict:
            continue
        df_a = ohlcv_dict[a][["ts", "close"]].rename(columns={"close": "close_a"})
        df_b = ohlcv_dict[b][["ts", "close"]].rename(columns={"close": "close_b"})
        merged = pd.merge(df_a, df_b, on="ts", how="inner").dropna().reset_index(drop=True)
        if len(merged) < WARMUP_BARS + max(HORIZONS_H):
            continue

        console.print(f"  pair {a}/{b}: {len(merged)-WARMUP_BARS} bars to replay...")
        prev_sign = 0   # 0 neutral, +1 last was z>=+2, -1 last was z<=-2
        log_a_all = np.log(merged["close_a"].values)
        log_b_all = np.log(merged["close_b"].values)

        for i in range(WARMUP_BARS - 1, len(merged)):
            y = pd.Series(log_a_all[i - WARMUP_BARS + 1 : i + 1])
            x = pd.Series(log_b_all[i - WARMUP_BARS + 1 : i + 1])
            try:
                res = engle_granger(y, x, pair_names=(a, b))
            except Exception:
                continue
            if res.adf_pvalue >= ADF_P_MAX:
                prev_sign = 0
                continue
            z = res.current_zscore
            curr_sign = 1 if z >= Z_THRESHOLD else (-1 if z <= -Z_THRESHOLD else 0)
            if curr_sign != 0 and curr_sign != prev_sign:
                # emit pair trade
                beta = res.hedge_ratio
                log_y_e = log_a_all[i]
                log_x_e = log_b_all[i]
                entry_spread = float(log_y_e - beta * log_x_e)
                direction = -1 if curr_sign == 1 else 1   # z>0 → short spread
                subtype = "Z_SHORT_SPR" if curr_sign == 1 else "Z_LONG_SPR"
                entry_ts = pd.Timestamp(merged.iloc[i]["ts"])
                t = Trade("Z_SPIKE", subtype, f"{a}|{b}", entry_ts, entry_spread,
                          direction, is_pair=True)
                for h in HORIZONS_H:
                    if i + h >= len(merged):
                        continue
                    log_y_x = log_a_all[i + h]
                    log_x_x = log_b_all[i + h]
                    exit_spread = float(log_y_x - beta * log_x_x)
                    # return approx ≈ direction * Δ(log_spread). Dollar-neutral construction.
                    r = direction * (exit_spread - entry_spread)
                    t.returns[h] = r - COST_PAIR
                trades.append(t)
            prev_sign = curr_sign
    return trades


# ─── Aggregation ────────────────────────────────────────────────────────────

def aggregate(trades: list[Trade]) -> pd.DataFrame:
    buckets: dict[tuple, list[tuple[pd.Timestamp, float]]] = {}
    for t in trades:
        for h, r in t.returns.items():
            buckets.setdefault((t.signal_type, t.subtype, h), []).append((t.entry_ts, r))

    rows = []
    for (st, sub, h), items in buckets.items():
        items = sorted(items, key=lambda x: x[0])
        rets = np.array([r for _, r in items])
        n = len(rets)
        if n == 0:
            continue
        wins = int((rets > 0).sum())
        win_pct = 100 * wins / n
        avg = float(rets.mean()) * 100
        med = float(np.median(rets)) * 100
        std = float(rets.std(ddof=1)) if n > 1 else 0.0
        # Per-trade IR (bukan annualized sharpe) — cukup untuk ranking relatif
        ir = (rets.mean() / std) if std > 0 else 0.0
        # Cumulative equity drawdown (sequential, 1 unit per trade)
        equity = np.cumsum(rets)
        peak = np.maximum.accumulate(equity)
        dd = float((equity - peak).min()) * 100
        total_pct = float(rets.sum()) * 100
        rows.append({
            "SIGNAL": st,
            "SUBTYPE": sub,
            "HORIZON": f"{h}h",
            "N": n,
            "WIN%": f"{win_pct:.1f}",
            "AVG%": f"{avg:+.2f}",
            "MED%": f"{med:+.2f}",
            "MAX_DD%": f"{dd:.2f}",
            "IR": f"{ir:+.2f}",
            "CUM%": f"{total_pct:+.1f}",
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["_h"] = df["HORIZON"].str.replace("h", "").astype(int)
    df = df.sort_values(["SIGNAL", "SUBTYPE", "_h"]).drop(columns=["_h"]).reset_index(drop=True)
    return df


def render_summary(df: pd.DataFrame, console: Console):
    if df.empty:
        console.print("[red]No trades generated — check data coverage atau threshold.[/red]")
        return
    title = "BACKTEST RESULT · 30D REPLAY · COSTS INCLUDED (0.11% single / 0.22% pair)"
    table = Table(box=box.HEAVY, title=f"[bold orange1]{title}[/bold orange1]",
                  title_justify="left", header_style="black on orange1")
    for col in df.columns:
        just = "left" if col in ("SIGNAL", "SUBTYPE", "HORIZON") else "right"
        table.add_column(col, justify=just)

    for _, row in df.iterrows():
        cells = []
        for col in df.columns:
            v = str(row[col])
            if col == "WIN%":
                p = float(v)
                v = f"[bold green]{v}[/bold green]" if p >= 55 else (
                    f"[red]{v}[/red]" if p < 45 else v)
            elif col == "IR":
                ir = float(v)
                v = f"[bold green]{v}[/bold green]" if ir >= 0.3 else (
                    f"[red]{v}[/red]" if ir < 0 else v)
            elif col in ("AVG%", "MED%", "CUM%"):
                x = float(v.replace("+", ""))
                v = f"[green]{v}[/green]" if x > 0 else (f"[red]{v}[/red]" if x < 0 else v)
            elif col == "MAX_DD%":
                v = f"[red]{v}[/red]"
            cells.append(v)
        table.add_row(*cells)
    console.print(table)


def render_detail(trades: list[Trade], signal_type: str, horizon: int, console: Console):
    matching = [t for t in trades if t.signal_type == signal_type and horizon in t.returns]
    if not matching:
        console.print(f"[yellow]no trades for {signal_type} @ {horizon}h[/yellow]")
        return
    matching.sort(key=lambda x: x.entry_ts)
    table = Table(box=box.SIMPLE_HEAVY,
                  title=f"[bold]DETAIL · {signal_type} @ {horizon}h · {len(matching)} trades[/bold]",
                  title_justify="left", header_style="black on orange1")
    for c in ("TIME", "SUBTYPE", "SYMBOL", "DIR", "ENTRY", "RET%"):
        table.add_column(c)
    cum = 0.0
    for t in matching:
        ret = t.returns[horizon] * 100
        cum += ret
        color = "green" if ret > 0 else "red"
        table.add_row(
            t.entry_ts.strftime("%m-%d %H:%M"),
            t.subtype,
            t.symbol,
            "LONG" if t.direction > 0 else "SHORT",
            f"{t.entry_price:.4f}",
            f"[{color}]{ret:+.2f}[/{color}]",
        )
    console.print(table)
    console.print(f"  Cumulative: [bold]{cum:+.2f}%[/bold]  ·  "
                  f"Avg per trade: [bold]{cum/len(matching):+.3f}%[/bold]")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skip-regime", action="store_true")
    p.add_argument("--skip-funding", action="store_true")
    p.add_argument("--skip-zspike", action="store_true")
    p.add_argument("--detail", default=None,
                   choices=["REGIME_CHANGE", "FUNDING_EXTREME", "Z_SPIKE"])
    p.add_argument("--detail-horizon", type=int, default=24, choices=HORIZONS_H)
    args = p.parse_args()

    console = Console()
    console.rule("[bold orange1]BACKTEST — REPLAY MODE[/bold orange1]")

    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    t0 = time.time()
    console.print("[cyan]Loading OHLCV + funding from DuckDB...[/cyan]")
    ohlcv = load_ohlcv(conn)
    funding = load_funding(conn)
    conn.close()
    console.print(f"  loaded in {time.time()-t0:.2f}s · "
                  f"{len(ohlcv)} symbols · {sum(len(v) for v in ohlcv.values())} OHLCV bars")

    all_trades: list[Trade] = []

    if not args.skip_regime:
        tt = time.time()
        console.print("[cyan]Replay REGIME_CHANGE...[/cyan]")
        tr = replay_regime(ohlcv)
        console.print(f"  → {len(tr)} trades · {time.time()-tt:.1f}s")
        all_trades.extend(tr)

    if not args.skip_funding:
        tt = time.time()
        console.print("[cyan]Replay FUNDING_EXTREME (threshold 0.1%/8h)...[/cyan]")
        tr = replay_funding(ohlcv, funding)
        console.print(f"  → {len(tr)} trades · {time.time()-tt:.1f}s")
        all_trades.extend(tr)

    if not args.skip_zspike:
        tt = time.time()
        console.print("[cyan]Replay Z_SPIKE (slow — engle_granger per bar per pair)...[/cyan]")
        pairs = cfg.get("cointegration_candidates", [])
        tr = replay_zspike(ohlcv, pairs, console)
        console.print(f"  → {len(tr)} trades · {time.time()-tt:.1f}s")
        all_trades.extend(tr)

    console.rule()
    df = aggregate(all_trades)
    render_summary(df, console)

    if args.detail:
        console.print()
        render_detail(all_trades, args.detail, args.detail_horizon, console)


if __name__ == "__main__":
    main()
