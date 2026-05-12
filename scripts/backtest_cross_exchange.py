"""Step 20 — Cross-exchange funding-rate differential backtest.

Per symbol: merge bybit + binance funding, compute differential, emit signals
saat |diff| ≥ threshold, simulate trades dengan ATR stops (reuse Step 18
trend_follow pattern). Two thesis modes:

  - mean-revert: bybit-crowded (diff>0) → SHORT (longs about to squeeze)
                 binance-crowded (diff<0) → LONG
  - momentum:    bybit-crowded → LONG (strong demand)
                 binance-crowded → SHORT

Backtest both per symbol per 12Q to determine direction edge (if any).
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

from data_pipe.cross_exchange import (  # noqa: E402
    compute_funding_diff_panel,
    detect_funding_diff_signals,
    dynamic_diff_threshold,
    parse_diff_payload,
)
from data_pipe.paper_broker import PaperBroker  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402
from data_pipe.trend_follow import compute_bollinger_atr_panel  # noqa: E402
from scripts.backtest_sweep import (  # noqa: E402
    classify,
    per_quarter_stats,
    split_into_quarters,
)


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"


def _build_inmemory_broker(starting, slippage_bps):
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
    b = PaperBroker(starting_capital=starting, slippage_bps_per_side=slippage_bps)
    b.attach_conn(conn)
    return b, conn


def _side_to_direction(side_tag: str, thesis: str) -> str:
    """Map side_tag (bybit-crowded / binance-crowded) + thesis → LONG/SHORT."""
    if thesis == "mean-revert":
        return "SHORT" if side_tag == "bybit-crowded" else "LONG"
    if thesis == "momentum":
        return "LONG" if side_tag == "bybit-crowded" else "SHORT"
    raise ValueError(f"unknown thesis {thesis}")


def simulate_cx_trades(
    events: list[dict],
    ohlcv_panel: pd.DataFrame,
    symbol: str,
    broker: PaperBroker,
    thesis: str,
    risk_pct: float = 5.0,
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 4.0,
    max_hold_hours: int = 168,
) -> list[dict]:
    if ohlcv_panel.empty or not events:
        return []

    ts_to_idx = {
        pd.Timestamp(t): i for i, t in enumerate(ohlcv_panel["ts"].values)
    }
    trades: list[dict] = []
    open_until: Optional[pd.Timestamp] = None

    for ev in events:
        entry_ts = pd.Timestamp(ev["ts"])
        if open_until is not None and entry_ts <= open_until:
            continue
        parsed = parse_diff_payload(ev["payload"])
        side_tag = parsed.get("side")
        if side_tag not in ("bybit-crowded", "binance-crowded"):
            continue
        direction = _side_to_direction(side_tag, thesis)

        # Find closest OHLCV bar at or after entry_ts (funding ts may not align 1h exactly)
        # Use forward-fill: find first idx where panel ts >= entry_ts
        idx = None
        # Use bisect-like search via ts values
        ts_vals = pd.to_datetime(ohlcv_panel["ts"].values)
        mask = ts_vals >= entry_ts
        if not mask.any():
            continue
        idx = int(mask.argmax())
        if idx >= len(ohlcv_panel) - 1:
            continue
        bar = ohlcv_panel.iloc[idx]
        if pd.isna(bar["atr"]) or pd.isna(bar["close"]):
            continue
        entry_price = float(bar["close"])
        atr_entry = float(bar["atr"])
        if entry_price <= 0 or atr_entry <= 0:
            continue

        notional = broker.equity_now() * (risk_pct / 100.0)
        size = notional / entry_price
        tid = broker.open_position(
            symbol, direction, size, entry_price,
            ts=entry_ts.to_pydatetime(),
        )

        if direction == "LONG":
            stop_price = entry_price - stop_atr_mult * atr_entry
            peak = entry_price
        else:
            stop_price = entry_price + stop_atr_mult * atr_entry
            peak = entry_price

        exit_idx = None
        exit_reason = "time_stop"
        max_j = min(idx + 1 + max_hold_hours, len(ohlcv_panel))
        for j in range(idx + 1, max_j):
            row = ohlcv_panel.iloc[j]
            close = float(row["close"])
            atr_now = float(row["atr"]) if not pd.isna(row["atr"]) else atr_entry
            if direction == "LONG":
                high = float(row["high"])
                peak = max(peak, high)
                if close <= stop_price:
                    exit_idx, exit_reason = j, "stop_loss"
                    break
                if close <= peak - trail_atr_mult * atr_now:
                    exit_idx, exit_reason = j, "trailing_stop"
                    break
            else:
                low = float(row["low"])
                peak = min(peak, low)
                if close >= stop_price:
                    exit_idx, exit_reason = j, "stop_loss"
                    break
                if close >= peak + trail_atr_mult * atr_now:
                    exit_idx, exit_reason = j, "trailing_stop"
                    break
        if exit_idx is None:
            exit_idx = max_j - 1 if max_j > idx + 1 else idx

        exit_ts = pd.Timestamp(ohlcv_panel["ts"].iloc[exit_idx])
        exit_price = float(ohlcv_panel["close"].iloc[exit_idx])
        res = broker.close_position(
            tid, exit_price, exit_reason, ts=exit_ts.to_pydatetime(),
        )
        trades.append({
            "symbol": symbol,
            "side_tag": side_tag,
            "thesis": thesis,
            "direction": direction,
            "entry_ts": entry_ts.to_pydatetime(),
            "exit_ts": exit_ts.to_pydatetime(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "atr_at_entry": atr_entry,
            "exit_reason": exit_reason,
            "pnl": float(res["pnl"] or 0),
            "hold_bars": exit_idx - idx,
            "diff": parsed.get("diff"),
        })
        open_until = exit_ts
    return trades


def run_cx_for_symbol(
    db_path, symbol, start, end, thesis,
    threshold_mode="dynamic",
    threshold_fixed=0.0002,
    threshold_percentile=90.0,
    starting_capital=10000.0,
    risk_pct=5.0,
    stop_atr_mult=2.0,
    trail_atr_mult=4.0,
    max_hold_hours=168,
    slippage_bps=10.0,
    quarters_n=4,
    debounce_hours=24,
):
    storage = Storage(db_path=db_path, read_only=True)
    by_fund = storage.get_funding("bybit", symbol, limit=100_000)
    bi_fund = storage.get_funding("binance", symbol, limit=100_000)
    df_ohlcv = storage.get_ohlcv("bybit", symbol, "1h", limit=100_000)
    storage.close()

    if by_fund.empty or bi_fund.empty or df_ohlcv.empty:
        return {
            "symbol": symbol, "thesis": thesis, "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "verdict_reason": "missing one of bybit/binance funding or ohlcv",
            "per_quarter": [], "trades": [],
        }

    # Filter ts windows
    for df in (by_fund, bi_fund, df_ohlcv):
        df["ts"] = pd.to_datetime(df["ts"])
    by_fund = by_fund[(by_fund["ts"] >= start) & (by_fund["ts"] <= end)]
    bi_fund = bi_fund[(bi_fund["ts"] >= start) & (bi_fund["ts"] <= end)]
    df_ohlcv = df_ohlcv[(df_ohlcv["ts"] >= start) & (df_ohlcv["ts"] <= end)]

    panel = compute_funding_diff_panel(by_fund, bi_fund, symbol)
    if panel.empty:
        return {
            "symbol": symbol, "thesis": thesis, "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "verdict_reason": "diff panel empty",
            "per_quarter": [], "trades": [],
        }

    if threshold_mode == "dynamic":
        thr = dynamic_diff_threshold(panel, percentile=threshold_percentile)
    else:
        thr = threshold_fixed

    events = detect_funding_diff_signals(
        panel, symbol, threshold=thr, debounce_hours=debounce_hours,
    )

    ohlcv_panel = compute_bollinger_atr_panel(df_ohlcv)

    broker, conn = _build_inmemory_broker(starting_capital, slippage_bps)
    try:
        trades = simulate_cx_trades(
            events, ohlcv_panel, symbol, broker, thesis=thesis,
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
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = wins / len(trades) * 100.0 if trades else 0.0

    return {
        "symbol": symbol, "thesis": thesis,
        "n_events": len(events),
        "n_trades": len(trades),
        "panel_rows": len(panel),
        "threshold_used": float(thr),
        "diff_p90_abs": float(panel["diff"].abs().quantile(0.90)),
        "realized_pnl": float(realized),
        "realized_return_pct": float(realized / starting_capital * 100.0),
        "verdict": cls["verdict"],
        "verdict_reason": cls.get("reason", ""),
        "sign_consistency": cls["sign_consistency"],
        "win_rate_pct": float(win_rate),
        "per_quarter": per_q,
        "trades": trades,
    }


def _write_md(summaries, path, start, end, args):
    lines = [
        "# Step 20 — Cross-exchange funding-diff backtest",
        "",
        f"**Window:** {start.date()} → {end.date()} ({(end-start).days} days)  ",
        f"**Quarters:** {args.quarters}  ",
        f"**Threshold:** {args.threshold_mode} (p{args.threshold_percentile} if dynamic)  ",
        f"**Slippage:** {args.slippage_bps} bps/side  ",
        "",
        "## Per-symbol summary",
        "",
        "| Symbol | Thesis | Verdict | n_ev | n_tr | Realized% | Sign | Win% | Thr |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['symbol']} | {s['thesis']} | **{s['verdict']}** | "
            f"{s['n_events']} | {s['n_trades']} | "
            f"{s['realized_return_pct']:+.2f}% | "
            f"{s['sign_consistency']} | {s.get('win_rate_pct', 0):.1f}% | "
            f"{s.get('threshold_used', 0)*100:.4f}% |"
        )
    lines.append("")
    n_pos = sum(1 for s in summaries if s["verdict"] == "STABLE_POSITIVE")
    n_neg = sum(1 for s in summaries if s["verdict"] == "STABLE_NEGATIVE")
    total = sum(s["realized_pnl"] for s in summaries)
    lines.append("## Aggregate")
    lines.append(f"- STABLE_POSITIVE: {n_pos}")
    lines.append(f"- STABLE_NEGATIVE: {n_neg}")
    lines.append(f"- Total realized PnL: {total:+.2f}")
    path.write_text("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser(description="Step 20 cross-exchange funding-diff backtest")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="all")
    p.add_argument("--thesis", choices=["mean-revert", "momentum", "both"], default="both")
    p.add_argument("--threshold-mode", choices=["dynamic", "fixed"], default="dynamic")
    p.add_argument("--threshold-fixed", type=float, default=0.0002)
    p.add_argument("--threshold-percentile", type=float, default=90.0)
    p.add_argument("--risk-pct", type=float, default=5.0)
    p.add_argument("--stop-atr-mult", type=float, default=2.0)
    p.add_argument("--trail-atr-mult", type=float, default=4.0)
    p.add_argument("--max-hold-hours", type=int, default=168)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--quarters", type=int, default=4)
    p.add_argument("--debounce-hours", type=int, default=24)
    args = p.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "pairs.yaml").read_text())
    symbols = (list(cfg.get("tier1", [])) + list(cfg.get("tier2", []))
               if args.symbols == "all"
               else [s.strip() for s in args.symbols.split(",")])

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    out_dir = RESULTS_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    theses = ["mean-revert", "momentum"] if args.thesis == "both" else [args.thesis]
    print(f"[bt-cx] label={args.label}, theses={theses}, symbols={len(symbols)}")

    summaries = []
    for thesis in theses:
        for sym in symbols:
            print(f"[bt-cx] {sym} thesis={thesis} ...")
            s = run_cx_for_symbol(
                db_path=Path(args.db), symbol=sym, start=start, end=end,
                thesis=thesis,
                threshold_mode=args.threshold_mode,
                threshold_fixed=args.threshold_fixed,
                threshold_percentile=args.threshold_percentile,
                risk_pct=args.risk_pct,
                stop_atr_mult=args.stop_atr_mult,
                trail_atr_mult=args.trail_atr_mult,
                max_hold_hours=args.max_hold_hours,
                slippage_bps=args.slippage_bps,
                quarters_n=args.quarters,
                debounce_hours=args.debounce_hours,
            )
            summaries.append(s)
            print(f"[bt-cx]   verdict={s['verdict']} n_ev={s['n_events']} "
                  f"n_tr={s['n_trades']} realized={s['realized_return_pct']:+.2f}% "
                  f"sign={s['sign_consistency']} thr={s.get('threshold_used', 0)*100:.4f}%")

    (out_dir / "summary.json").write_text(
        json.dumps({"summaries": summaries}, indent=2, default=str)
    )
    _write_md(summaries, out_dir / "comparison.md", start, end, args)
    print(f"[bt-cx] DONE — {out_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
