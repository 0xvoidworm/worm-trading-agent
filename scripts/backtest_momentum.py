#!/usr/bin/env python3
"""Backtest momentum strategy: buy top N% 24h gainers, hold X days.

Same thesis as NEAR trade (CoinGecko trending = momentum proxy). Validates
historically: if I had bought the day's biggest 24h gainer in tier1+tier2
universe + held N days, what would PnL distribution look like?

For each day t in OHLCV history:
  - Rank symbols by 24h % return (close[t] / close[t-24h])
  - Pick top K
  - Simulate buy at close[t], hold X days, sell at close[t+X*24]
  - Track PnL net of fees

Aggregate per K (1, 2, 3) and per X (1, 3, 7, 14 days).

Output: data/backtest_results/momentum_<date>.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"

UNIVERSE = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
            "XRP/USDT", "AVAX/USDT", "ARB/USDT", "OP/USDT"]

DEFAULT_FEE_PCT_SIDE = 0.10  # Binance spot taker
DEFAULT_HOLD_DAYS = [1, 3, 7, 14]
DEFAULT_TOP_K = [1, 2, 3]
DEFAULT_REBALANCE_DAYS = 1   # rebalance window — skip days within hold


def load_close_panel(conn, exchange="bybit", tf="1h"):
    rows = conn.execute(
        f"""
        SELECT ts, symbol, close
        FROM ohlcv
        WHERE exchange=? AND timeframe=? AND symbol IN ({','.join('?'*len(UNIVERSE))})
        ORDER BY ts, symbol
        """,
        [exchange, tf] + UNIVERSE,
    ).fetchdf()
    if rows.empty:
        return pd.DataFrame()
    return rows.pivot(index="ts", columns="symbol", values="close").sort_index()


def backtest(panel: pd.DataFrame, top_k: int, hold_days: int, fee_pct_side: float) -> dict:
    """Walk forward. Pick top-K gainer each rebalance day, hold N days, exit."""
    if panel.empty:
        return {"top_k": top_k, "hold_days": hold_days, "n_trades": 0}
    # Resample to daily close (use 00:00 bar) for stable ranking
    daily = panel.resample("1D").last().dropna(how="all")
    if len(daily) < hold_days + 2:
        return {"top_k": top_k, "hold_days": hold_days, "n_trades": 0}

    # Daily 24h return = today_close / prev_close - 1
    ret24h = daily.pct_change()

    trades = []
    # Iterate over days, pick top K from ret24h, hold X days
    # Step by hold_days (no overlap) so we have non-overlapping non-pyramid
    step = max(1, hold_days)
    for i in range(1, len(daily) - hold_days, step):
        day_idx = daily.index[i]
        ranking = ret24h.iloc[i].dropna().sort_values(ascending=False)
        top = ranking.head(top_k)
        if top.empty:
            continue
        exit_idx = i + hold_days
        if exit_idx >= len(daily):
            break
        exit_day = daily.index[exit_idx]
        for sym, signal_ret in top.items():
            entry = daily.iloc[i][sym]
            exit_p = daily.iloc[exit_idx][sym]
            if pd.isna(entry) or pd.isna(exit_p) or entry <= 0:
                continue
            gross_pct = (exit_p - entry) / entry * 100
            net_pct = gross_pct - 2 * fee_pct_side
            trades.append({
                "entry_ts": day_idx,
                "exit_ts": exit_day,
                "symbol": sym,
                "signal_24h_ret_pct": float(signal_ret * 100),
                "entry_price": float(entry),
                "exit_price": float(exit_p),
                "pnl_pct_gross": float(gross_pct),
                "pnl_pct_net": float(net_pct),
            })
    if not trades:
        return {"top_k": top_k, "hold_days": hold_days, "n_trades": 0}
    df = pd.DataFrame(trades)
    return {
        "top_k": top_k,
        "hold_days": hold_days,
        "n_trades": len(df),
        "n_wins": int((df["pnl_pct_net"] > 0).sum()),
        "win_rate_pct": float((df["pnl_pct_net"] > 0).mean() * 100),
        "avg_pct_net": float(df["pnl_pct_net"].mean()),
        "median_pct_net": float(df["pnl_pct_net"].median()),
        "best_pct_net": float(df["pnl_pct_net"].max()),
        "worst_pct_net": float(df["pnl_pct_net"].min()),
        "total_compound_pct": float(((1 + df["pnl_pct_net"] / 100).prod() - 1) * 100),
        "by_symbol": df.groupby("symbol")["pnl_pct_net"].agg(["count", "mean"]).to_dict(),
        "first_entry": str(df["entry_ts"].iloc[0]),
        "last_entry": str(df["entry_ts"].iloc[-1]),
    }


def _verdict(r: dict) -> str:
    n = r.get("n_trades", 0)
    if n == 0:
        return "NO_DATA"
    if n < 10:
        return "INSUFFICIENT_SAMPLE"
    wr = r.get("win_rate_pct", 0)
    avg = r.get("avg_pct_net", 0)
    total = r.get("total_compound_pct", 0)
    if wr >= 55 and avg > 0.5 and total > 10:
        return "STABLE_POSITIVE"
    if wr >= 50 and avg > 0:
        return "MARGINAL_POSITIVE"
    if wr < 40 and avg < 0:
        return "STABLE_NEGATIVE"
    return "INCONSISTENT"


def render_md(results: list[dict]) -> str:
    lines = []
    lines.append(f"# Momentum Backtest — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append("**Strategy**: Each day, pick top-K 24h gainers from 8-symbol universe ")
    lines.append(f"(BTC/ETH/SOL/BNB/XRP/AVAX/ARB/OP). Buy at daily close, hold X days, sell. Fee 0.1%/side. ")
    lines.append("Rebalance step = hold_days (non-overlapping).")
    lines.append("")
    lines.append("## Summary Matrix")
    lines.append("| top_K | hold_days | N | Win% | Avg net% | Median net% | Compound% | Best | Worst | Verdict |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in results:
        if r.get("n_trades", 0) == 0:
            lines.append(f"| {r['top_k']} | {r['hold_days']} | 0 | — | — | — | — | — | — | NO_DATA |")
            continue
        v = _verdict(r)
        lines.append(
            f"| {r['top_k']} | {r['hold_days']} | {r['n_trades']} | "
            f"{r['win_rate_pct']:.1f} | {r['avg_pct_net']:+.3f} | "
            f"{r['median_pct_net']:+.3f} | {r['total_compound_pct']:+.2f} | "
            f"{r['best_pct_net']:+.2f} | {r['worst_pct_net']:+.2f} | {v} |"
        )
    lines.append("")
    lines.append("## Per-Combo Detail (winning configs only)")
    for r in results:
        if r.get("n_trades", 0) == 0:
            continue
        v = _verdict(r)
        if v not in ("STABLE_POSITIVE", "MARGINAL_POSITIVE"):
            continue
        lines.append(f"\n### top_K={r['top_k']} hold={r['hold_days']}d ({v})")
        lines.append(f"- {r['n_trades']} trades, win rate {r['win_rate_pct']:.1f}%")
        lines.append(f"- avg net {r['avg_pct_net']:+.3f}%, median {r['median_pct_net']:+.3f}%, compound {r['total_compound_pct']:+.2f}%")
        lines.append(f"- best/worst: {r['best_pct_net']:+.2f}% / {r['worst_pct_net']:+.2f}%")
        by_sym = r.get("by_symbol", {})
        if by_sym:
            count = by_sym.get("count", {})
            mean = by_sym.get("mean", {})
            lines.append("- by symbol:")
            for sym in sorted(mean, key=lambda x: -mean[x]):
                lines.append(f"  - {sym}: {count[sym]} trades, avg {mean[sym]:+.3f}%")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fee", type=float, default=DEFAULT_FEE_PCT_SIDE)
    args = p.parse_args()

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        panel = load_close_panel(conn)
    finally:
        conn.close()

    if panel.empty:
        print("[backtest] no OHLCV data — abort")
        return 1

    print(f"[backtest] panel: {len(panel)} bars × {panel.shape[1]} symbols ({panel.index.min()} → {panel.index.max()})")

    results = []
    for k in DEFAULT_TOP_K:
        for h in DEFAULT_HOLD_DAYS:
            r = backtest(panel, k, h, args.fee)
            v = _verdict(r) if r.get("n_trades") else "NO_DATA"
            print(f"[backtest] top_K={k} hold={h:>2}d → {r.get('n_trades', 0)} trades, verdict={v}")
            results.append(r)

    md = render_md(results)
    out = OUT_DIR / f"momentum_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"\n[backtest] report written: {out} ({len(md)} chars)")
    print(md.split("## Per-Combo Detail")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
