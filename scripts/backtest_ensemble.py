"""Step 19 — Ensemble voting backtest.

Reads existing `signal_events` rows dari DB, aggregates per-symbol votes,
emits ENSEMBLE_ENTRY events, simulates single-leg trades dengan ATR stops
(reuse logic dari Step 18 trend_follow).

WHY at this level: 4 thesis class individually rejected (Step 15/17/18); ensemble
voting tests apakah orthogonal signals collectively dapat mengisolasi edge
yang individual signal tidak punya.
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

from data_pipe.ensemble import (  # noqa: E402
    aggregate_votes,
    emit_ensemble_signals,
    parse_ensemble_payload,
)
from data_pipe.paper_broker import PaperBroker  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402
from data_pipe.trend_follow import (  # noqa: E402
    compute_bollinger_atr_panel,
    detect_breakout_signals,
)
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


def simulate_ensemble_trades(
    events: list[dict],
    panel: pd.DataFrame,
    symbol: str,
    broker: PaperBroker,
    risk_pct: float = 5.0,
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 4.0,
    max_hold_hours: int = 168,
    invert: bool = False,
) -> list[dict]:
    """Simulate trades dari ENSEMBLE_ENTRY events. Side dari payload, ATR + entry
    price dari panel pada ts. Exit: stop/trail/time (no SMA cross — ensemble is
    higher-level signal, doesn't need lower-level SMA invalidation).
    """
    if panel.empty or not events:
        return []

    ts_to_idx: dict[pd.Timestamp, int] = {
        pd.Timestamp(t): i for i, t in enumerate(panel["ts"].values)
    }

    trades: list[dict] = []
    open_until: Optional[pd.Timestamp] = None

    for ev in events:
        entry_ts = pd.Timestamp(ev["ts"])
        if open_until is not None and entry_ts <= open_until:
            continue
        parsed = parse_ensemble_payload(ev["payload"])
        side = parsed.get("side")  # "long" / "short"
        if side not in ("long", "short"):
            continue
        if invert:
            side = "short" if side == "long" else "long"

        idx = ts_to_idx.get(entry_ts)
        if idx is None or idx >= len(panel) - 1:
            continue
        bar = panel.iloc[idx]
        if pd.isna(bar["atr"]) or pd.isna(bar["close"]):
            continue
        entry_price = float(bar["close"])
        atr_entry = float(bar["atr"])
        if entry_price <= 0 or atr_entry <= 0:
            continue

        direction = "LONG" if side == "long" else "SHORT"
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

        exit_idx: Optional[int] = None
        exit_reason = "time_stop"
        max_j = min(idx + 1 + max_hold_hours, len(panel))
        for j in range(idx + 1, max_j):
            row = panel.iloc[j]
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

        exit_ts = pd.Timestamp(panel["ts"].iloc[exit_idx])
        exit_price = float(panel["close"].iloc[exit_idx])
        res = broker.close_position(
            tid, exit_price, exit_reason, ts=exit_ts.to_pydatetime(),
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
            "hold_bars": exit_idx - idx,
            "vote_sum": parsed.get("vote_sum"),
            "n_signals": parsed.get("n_signals"),
            "signal_types": parsed.get("types", ""),
        })
        open_until = exit_ts

    return trades


def run_ensemble_for_symbol(
    db_path: Path,
    symbol: str,
    start: datetime,
    end: datetime,
    lookback_hours: int = 24,
    min_agreement: int = 2,
    min_distinct_types: int = 2,
    debounce_hours: int = 4,
    starting_capital: float = 10000.0,
    risk_pct: float = 5.0,
    stop_atr_mult: float = 2.0,
    trail_atr_mult: float = 4.0,
    max_hold_hours: int = 168,
    slippage_bps: float = 10.0,
    quarters_n: int = 4,
    invert: bool = False,
) -> dict:
    storage = Storage(db_path=db_path, read_only=True)
    df_ohlcv = storage.get_ohlcv("bybit", symbol, "1h", limit=100_000)
    sig_df = storage.get_signals(limit=200_000)  # all per-symbol relevant
    storage.close()

    if df_ohlcv.empty:
        return {
            "symbol": symbol, "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "verdict_reason": "no ohlcv", "per_quarter": [], "trades": [],
        }
    df_ohlcv = df_ohlcv.copy()
    df_ohlcv["ts"] = pd.to_datetime(df_ohlcv["ts"])
    df_ohlcv = df_ohlcv[
        (df_ohlcv["ts"] >= start) & (df_ohlcv["ts"] <= end)
    ].reset_index(drop=True)
    panel = compute_bollinger_atr_panel(df_ohlcv)

    # Filter signal_events ke window
    sig_df = sig_df.copy()
    if not sig_df.empty:
        sig_df["ts"] = pd.to_datetime(sig_df["ts"])
        sig_df = sig_df[(sig_df["ts"] >= start) & (sig_df["ts"] <= end)]

    # Step 19: TREND_BREAKOUT detector baru (Step 18), belum di DB → inject inline
    trend_events = detect_breakout_signals(panel, symbol=symbol)
    if trend_events:
        trend_df = pd.DataFrame([{
            "ts": pd.Timestamp(e["ts"]), "event_type": e["event_type"],
            "symbol": e["symbol"], "payload": e["payload"],
            "severity": e["severity"],
        } for e in trend_events])
        sig_df = pd.concat([sig_df, trend_df], ignore_index=True) if not sig_df.empty else trend_df

    votes = aggregate_votes(sig_df, symbols=[symbol], lookback_hours=lookback_hours)
    events = emit_ensemble_signals(
        votes, min_agreement=min_agreement,
        min_distinct_types=min_distinct_types,
        debounce_hours=debounce_hours,
    )

    broker, conn = _build_inmemory_broker(starting_capital, slippage_bps)
    try:
        trades = simulate_ensemble_trades(
            events, panel, symbol, broker,
            risk_pct=risk_pct,
            stop_atr_mult=stop_atr_mult,
            trail_atr_mult=trail_atr_mult,
            max_hold_hours=max_hold_hours,
            invert=invert,
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

    by_exit: dict[str, int] = {}
    for t in trades:
        by_exit[t["exit_reason"]] = by_exit.get(t["exit_reason"], 0) + 1

    return {
        "symbol": symbol,
        "n_events": len(events),
        "n_trades": len(trades),
        "realized_pnl": float(realized),
        "realized_return_pct": float(realized / starting_capital * 100.0),
        "verdict": cls["verdict"],
        "verdict_reason": cls.get("reason", ""),
        "sign_consistency": cls["sign_consistency"],
        "win_rate_pct": float(win_rate),
        "by_exit_reason": by_exit,
        "per_quarter": per_q,
        "trades": trades,
    }


def _write_md(summaries, path, start, end, args):
    lines = [
        "# Step 19 — Ensemble voting backtest",
        "",
        f"**Window:** {start.date()} → {end.date()} ({(end-start).days} days)  ",
        f"**Quarters:** {args.quarters}  ",
        f"**Lookback votes:** {args.lookback_hours}h  ",
        f"**Min agreement:** {args.min_agreement}  ",
        f"**Min distinct types:** {args.min_distinct_types}  ",
        f"**Debounce:** {args.debounce_hours}h  ",
        f"**Stops:** {args.stop_atr_mult}× ATR (initial), {args.trail_atr_mult}× ATR (trail)  ",
        f"**Slippage:** {args.slippage_bps} bps/side",
        "",
        "## Per-symbol summary",
        "",
        "| Symbol | Verdict | n_events | n_trades | Realized% | Sign | Win% |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['symbol']} | **{s['verdict']}** | {s['n_events']} | "
            f"{s['n_trades']} | {s['realized_return_pct']:+.2f}% | "
            f"{s['sign_consistency']} | {s.get('win_rate_pct', 0):.1f}% |"
        )
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

    n_pos = sum(1 for s in summaries if s["verdict"] == "STABLE_POSITIVE")
    n_neg = sum(1 for s in summaries if s["verdict"] == "STABLE_NEGATIVE")
    total_realized = sum(s["realized_pnl"] for s in summaries)
    lines.append("")
    lines.append("## Aggregate")
    lines.append(f"- STABLE_POSITIVE: {n_pos}")
    lines.append(f"- STABLE_NEGATIVE: {n_neg}")
    lines.append(f"- Total realized PnL: {total_realized:+.2f}")
    path.write_text("\n".join(lines))


def main(argv=None):
    p = argparse.ArgumentParser(description="Step 19 ensemble voting backtest")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--symbols", default="all")
    p.add_argument("--lookback-hours", type=int, default=24)
    p.add_argument("--min-agreement", type=int, default=2)
    p.add_argument("--min-distinct-types", type=int, default=2)
    p.add_argument("--debounce-hours", type=int, default=4)
    p.add_argument("--risk-pct", type=float, default=5.0)
    p.add_argument("--stop-atr-mult", type=float, default=2.0)
    p.add_argument("--trail-atr-mult", type=float, default=4.0)
    p.add_argument("--max-hold-hours", type=int, default=168)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--quarters", type=int, default=4)
    p.add_argument("--invert", action="store_true",
                   help="Invert vote semantics (sanity check vs signal inversion edge)")
    args = p.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "pairs.yaml").read_text())
    symbols = (list(cfg.get("tier1", [])) + list(cfg.get("tier2", []))
               if args.symbols == "all"
               else [s.strip() for s in args.symbols.split(",") if s.strip()])

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    out_dir = RESULTS_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[bt-ens] label={args.label}")
    print(f"[bt-ens] window: {start.date()} → {end.date()}")
    print(f"[bt-ens] symbols: {len(symbols)}, min_agreement={args.min_agreement}")

    summaries = []
    for sym in symbols:
        print(f"[bt-ens] {sym} ...")
        s = run_ensemble_for_symbol(
            db_path=Path(args.db), symbol=sym, start=start, end=end,
            lookback_hours=args.lookback_hours,
            min_agreement=args.min_agreement,
            min_distinct_types=args.min_distinct_types,
            debounce_hours=args.debounce_hours,
            risk_pct=args.risk_pct,
            stop_atr_mult=args.stop_atr_mult,
            trail_atr_mult=args.trail_atr_mult,
            max_hold_hours=args.max_hold_hours,
            slippage_bps=args.slippage_bps,
            quarters_n=args.quarters,
            invert=args.invert,
        )
        summaries.append(s)
        print(f"[bt-ens]   verdict={s['verdict']} n_ev={s['n_events']} "
              f"n_tr={s['n_trades']} realized={s['realized_return_pct']:+.2f}% "
              f"sign={s['sign_consistency']} win={s.get('win_rate_pct', 0):.1f}%")

    (out_dir / "summary.json").write_text(
        json.dumps({"summaries": summaries}, indent=2, default=str)
    )
    _write_md(summaries, out_dir / "comparison.md", start, end, args)
    print(f"[bt-ens] DONE — {out_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
