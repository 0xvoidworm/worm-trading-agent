"""Step 18.B — Standalone trend-follow backtest engine.

Per-symbol Bollinger+ATR breakout strategy (Step 18.A detector). Trades single
leg (LONG on long-breakout, SHORT on short-breakdown) dengan ATR-based stop
+ trailing + SMA-cross exit.

WHY standalone (sama dengan Step 17.B3 cointegration): confirm edge cheaply
sebelum wire ke production daemon. Step 14B/17.B precedent — production wire
sebelum edge-confirmation = wasted weeks.

Exit ladder (first to trigger):
1. Stop loss: close ≤ entry − stop_atr_mult × ATR_entry (long); inverse for short.
2. Trailing stop: drawdown dari peak/trough ≥ trail_atr_mult × ATR_now.
3. SMA cross: close re-crosses SMA in opposite direction (trend broken).
4. Time stop: hold > max_hold_hours.

Usage:
  python3 scripts/backtest_trend_follow.py \\
      --start 2023-04-27 --end 2026-04-26 \\
      --label step18_tf_3y --slippage-bps 10 --quarters 12
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.paper_broker import PaperBroker  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402
from data_pipe.trend_follow import (  # noqa: E402
    compute_bollinger_atr_panel,
    detect_breakout_signals,
    parse_breakout_payload,
)
from scripts.backtest_sweep import (  # noqa: E402
    classify,
    per_quarter_stats,
    split_into_quarters,
)


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"


def _build_inmemory_broker(
    starting_capital: float, slippage_bps: float,
) -> tuple[PaperBroker, duckdb.DuckDBPyConnection]:
    """In-memory broker per symbol — backtest tidak menyentuh production DB."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SEQUENCE seq_tj START 1")
    conn.execute(
        "CREATE TABLE trade_journal ("
        "  id INTEGER NOT NULL DEFAULT nextval('seq_tj'),"
        "  ts TIMESTAMP NOT NULL, pair VARCHAR NOT NULL,"
        "  direction VARCHAR NOT NULL, entry_price DOUBLE, size DOUBLE,"
        "  thesis VARCHAR, status VARCHAR NOT NULL DEFAULT 'OPEN',"
        "  exit_ts TIMESTAMP, exit_price DOUBLE, pnl DOUBLE, notes VARCHAR,"
        "  signal_id INTEGER, PRIMARY KEY (id))"
    )
    broker = PaperBroker(
        starting_capital=starting_capital,
        slippage_bps_per_side=slippage_bps,
    )
    broker.attach_conn(conn)
    return broker, conn


def simulate_trend_trades(
    panel: pd.DataFrame,
    events: list[dict],
    symbol: str,
    broker: PaperBroker,
    risk_pct: float = 5.0,
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 3.0,
    max_hold_hours: int = 168,
) -> list[dict]:
    """For each breakout event, simulate trade. Returns list of trade dicts.

    Sizing: notional = equity_now × risk_pct/100; size = notional / entry_price.
    """
    if panel.empty or not events:
        return []

    # Quick ts → idx lookup
    ts_to_idx: dict[pd.Timestamp, int] = {
        pd.Timestamp(t): i for i, t in enumerate(panel["ts"].values)
    }

    trades: list[dict] = []
    open_until_ts: Optional[pd.Timestamp] = None

    for ev in events:
        entry_ts = pd.Timestamp(ev["ts"])
        if open_until_ts is not None and entry_ts <= open_until_ts:
            continue

        parsed = parse_breakout_payload(ev["payload"])
        side = parsed.get("side")
        atr_entry = float(parsed.get("atr", 0))
        entry_price = float(parsed.get("close", 0))
        if not entry_price or not atr_entry or side not in (
            "long-breakout", "short-breakdown"
        ):
            continue

        direction = "LONG" if side == "long-breakout" else "SHORT"

        equity_now = broker.equity_now()
        notional = equity_now * (risk_pct / 100.0)
        size = notional / entry_price

        tid = broker.open_position(
            symbol, direction, size, entry_price,
            ts=entry_ts.to_pydatetime(),
        )

        # Find exit by walking forward
        entry_idx = ts_to_idx.get(entry_ts)
        if entry_idx is None:
            broker.close_position(tid, entry_price, "no_panel_idx",
                                   ts=entry_ts.to_pydatetime())
            continue

        if direction == "LONG":
            stop_price = entry_price - stop_atr_mult * atr_entry
            peak = entry_price
        else:
            stop_price = entry_price + stop_atr_mult * atr_entry
            peak = entry_price

        exit_idx: Optional[int] = None
        exit_reason = "time_stop"
        max_j = min(entry_idx + 1 + max_hold_hours, len(panel))
        for j in range(entry_idx + 1, max_j):
            row = panel.iloc[j]
            close = float(row["close"])
            sma = float(row["sma"]) if not pd.isna(row["sma"]) else None
            atr_now_val = row["atr"]
            atr_now = float(atr_now_val) if not pd.isna(atr_now_val) else atr_entry

            if direction == "LONG":
                high = float(row["high"]) if "high" in row else close
                peak = max(peak, high)
                if close <= stop_price:
                    exit_idx, exit_reason = j, "stop_loss"
                    break
                if close <= peak - trail_atr_mult * atr_now:
                    exit_idx, exit_reason = j, "trailing_stop"
                    break
                if sma is not None and close < sma:
                    exit_idx, exit_reason = j, "sma_cross"
                    break
            else:  # SHORT
                low = float(row["low"]) if "low" in row else close
                peak = min(peak, low)
                if close >= stop_price:
                    exit_idx, exit_reason = j, "stop_loss"
                    break
                if close >= peak + trail_atr_mult * atr_now:
                    exit_idx, exit_reason = j, "trailing_stop"
                    break
                if sma is not None and close > sma:
                    exit_idx, exit_reason = j, "sma_cross"
                    break
        if exit_idx is None:
            exit_idx = max_j - 1 if max_j > entry_idx + 1 else entry_idx

        exit_ts = pd.Timestamp(panel["ts"].iloc[exit_idx])
        exit_price = float(panel["close"].iloc[exit_idx])

        res = broker.close_position(
            tid, exit_price, exit_reason, ts=exit_ts.to_pydatetime()
        )
        trades.append({
            "symbol": symbol,
            "side": side,
            "direction": direction,
            "entry_ts": entry_ts.to_pydatetime(),
            "exit_ts": exit_ts.to_pydatetime(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "atr_at_entry": atr_entry,
            "exit_reason": exit_reason,
            "pnl": float(res["pnl"] or 0),
            "hold_bars": exit_idx - entry_idx,
        })
        open_until_ts = exit_ts

    return trades


def run_backtest_for_symbol(
    db_path: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    bb_window: int = 20,
    bb_std: float = 2.0,
    atr_window: int = 14,
    min_atr_pct: float = 0.003,
    debounce_bars: int = 4,
    starting_capital: float = 10000.0,
    risk_pct: float = 5.0,
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 3.0,
    max_hold_hours: int = 168,
    slippage_bps: float = 10.0,
    quarters_n: int = 4,
) -> dict:
    """Single-symbol backtest. Return summary dict."""
    storage = Storage(db_path=db_path, read_only=True)
    df = storage.get_ohlcv("bybit", symbol, "1h", limit=100_000)
    storage.close()
    if df.empty:
        return {
            "symbol": symbol, "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0, "panel_rows": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "verdict_reason": "no ohlcv",
            "per_quarter": [], "trades": [],
        }

    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"])
    df = df[(df["ts"] >= start) & (df["ts"] <= end)].reset_index(drop=True)
    if df.empty:
        return {
            "symbol": symbol, "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0, "panel_rows": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "verdict_reason": "no ohlcv in window",
            "per_quarter": [], "trades": [],
        }

    panel = compute_bollinger_atr_panel(
        df, bb_window=bb_window, bb_std=bb_std, atr_window=atr_window,
    )
    events = detect_breakout_signals(
        panel, symbol=symbol,
        min_atr_pct=min_atr_pct, debounce_bars=debounce_bars,
    )

    broker, conn = _build_inmemory_broker(starting_capital, slippage_bps)
    try:
        trades = simulate_trend_trades(
            panel, events, symbol, broker,
            risk_pct=risk_pct,
            stop_atr_mult=stop_atr_mult,
            trail_atr_mult=trail_atr_mult,
            max_hold_hours=max_hold_hours,
        )
    finally:
        conn.close()

    closed_simple = [
        {"exit_ts": t["exit_ts"], "pnl": t["pnl"]} for t in trades
    ]
    quarters = split_into_quarters(start, end, quarters_n)
    per_q = per_quarter_stats(closed_simple, quarters, starting_capital)
    cls = classify(per_q, n_total_closed=len(trades))
    realized = sum(t["pnl"] for t in trades)

    # Win-rate breakdown
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] < 0)
    flats = len(trades) - wins - losses
    win_rate = wins / len(trades) * 100.0 if trades else 0.0

    # Exit reason breakdown
    by_exit: dict[str, int] = {}
    for t in trades:
        by_exit[t["exit_reason"]] = by_exit.get(t["exit_reason"], 0) + 1

    return {
        "symbol": symbol,
        "n_events": len(events),
        "n_trades": len(trades),
        "panel_rows": len(panel),
        "realized_pnl": float(realized),
        "realized_return_pct": float(realized / starting_capital * 100.0),
        "verdict": cls["verdict"],
        "verdict_reason": cls.get("reason", ""),
        "sign_consistency": cls["sign_consistency"],
        "win_rate_pct": float(win_rate),
        "wins": wins, "losses": losses, "flats": flats,
        "by_exit_reason": by_exit,
        "per_quarter": per_q,
        "trades": trades,
    }


def _write_comparison_md(
    summaries: list[dict], path: Path,
    start: datetime, end: datetime,
    args: argparse.Namespace,
) -> None:
    lines = [
        "# Step 18.B — Trend-follow standalone backtest",
        "",
        f"**Window:** {start.date()} → {end.date()} ({(end - start).days} days)  ",
        f"**Quarters:** {args.quarters}  ",
        f"**BB:** SMA({args.bb_window}) ± {args.bb_std}×stdev  ",
        f"**ATR:** {args.atr_window} bars  ",
        f"**Stop ATR mult:** {args.stop_atr_mult}× (initial), {args.trail_atr_mult}× (trailing)  ",
        f"**Max hold:** {args.max_hold_hours}h  ",
        f"**Slippage:** {args.slippage_bps} bps/side  ",
        f"**Risk/trade:** {args.risk_pct}% equity notional",
        "",
        "## Per-symbol summary",
        "",
        "| Symbol | Verdict | n_trades | Realized% | Sign | Win% | Exits |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        exits = " ".join(
            f"{k}={v}" for k, v in sorted(
                s.get("by_exit_reason", {}).items(), key=lambda x: -x[1]
            )
        )
        lines.append(
            f"| {s['symbol']} | **{s['verdict']}** | {s['n_trades']} | "
            f"{s['realized_return_pct']:+.2f}% | {s['sign_consistency']} | "
            f"{s.get('win_rate_pct', 0):.1f}% | {exits} |"
        )

    # Per-quarter detail
    lines.append("")
    lines.append("## Per-quarter detail")
    for s in summaries:
        lines.append(f"\n### {s['symbol']} → {s['verdict']}")
        lines.append(f"_{s.get('verdict_reason', '')}_")
        lines.append("")
        lines.append("| Q | n | realized% | sign |")
        lines.append("|---|---|---|---|")
        for q in s.get("per_quarter", []):
            lines.append(
                f"| {q['quarter']} | {q['n_trades_closed']} | "
                f"{q['realized_return_pct']:+.2f}% | {q['sign']} |"
            )

    # Aggregate
    total_pnl = sum(s["realized_pnl"] for s in summaries)
    total_trades = sum(s["n_trades"] for s in summaries)
    n_stable_pos = sum(1 for s in summaries if s["verdict"] == "STABLE_POSITIVE")
    n_stable_neg = sum(1 for s in summaries if s["verdict"] == "STABLE_NEGATIVE")
    n_outlier = sum(1 for s in summaries if s["verdict"] == "OUTLIER_DRIVEN")
    lines.append("")
    lines.append("## Aggregate")
    lines.append(f"- Symbols sweep: {len(summaries)}")
    lines.append(f"- STABLE_POSITIVE: {n_stable_pos}")
    lines.append(f"- STABLE_NEGATIVE: {n_stable_neg}")
    lines.append(f"- OUTLIER_DRIVEN: {n_outlier}")
    lines.append(f"- Total trades: {total_trades}")
    lines.append(f"- Total realized PnL: {total_pnl:+.2f}")

    path.write_text("\n".join(lines))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Step 18.B trend-follow standalone backtest",
    )
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--label", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="all",
                   help="comma-separated or 'all' from config tier1+tier2")
    p.add_argument("--bb-window", type=int, default=20)
    p.add_argument("--bb-std", type=float, default=2.0)
    p.add_argument("--atr-window", type=int, default=14)
    p.add_argument("--min-atr-pct", type=float, default=0.003)
    p.add_argument("--debounce-bars", type=int, default=4)
    p.add_argument("--risk-pct", type=float, default=5.0)
    p.add_argument("--stop-atr-mult", type=float, default=2.0)
    p.add_argument("--trail-atr-mult", type=float, default=3.0)
    p.add_argument("--max-hold-hours", type=int, default=168)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--quarters", type=int, default=4)
    args = p.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "pairs.yaml").read_text())
    if args.symbols == "all":
        symbols = list(cfg.get("tier1", [])) + list(cfg.get("tier2", []))
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: no symbols", file=sys.stderr)
        return 2

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    out_dir = RESULTS_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[bt-tf] label={args.label}")
    print(f"[bt-tf] window: {start.date()} → {end.date()}")
    print(f"[bt-tf] symbols: {len(symbols)}")

    summaries: list[dict] = []
    for sym in symbols:
        print(f"[bt-tf] {sym} ...")
        summary = run_backtest_for_symbol(
            db_path=Path(args.db), symbol=sym, start=start, end=end,
            bb_window=args.bb_window,
            bb_std=args.bb_std,
            atr_window=args.atr_window,
            min_atr_pct=args.min_atr_pct,
            debounce_bars=args.debounce_bars,
            risk_pct=args.risk_pct,
            stop_atr_mult=args.stop_atr_mult,
            trail_atr_mult=args.trail_atr_mult,
            max_hold_hours=args.max_hold_hours,
            slippage_bps=args.slippage_bps,
            quarters_n=args.quarters,
        )
        summaries.append(summary)
        print(
            f"[bt-tf]   verdict={summary['verdict']} "
            f"n_trades={summary['n_trades']} "
            f"realized={summary['realized_return_pct']:+.2f}% "
            f"sign={summary['sign_consistency']} "
            f"win={summary.get('win_rate_pct', 0):.1f}%"
        )

    (out_dir / "summary.json").write_text(
        json.dumps({"summaries": summaries}, indent=2, default=str)
    )
    _write_comparison_md(summaries, out_dir / "comparison.md", start, end, args)
    print(f"[bt-tf] DONE — {out_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
