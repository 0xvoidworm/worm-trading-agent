"""Step 17.B3 — Standalone pair-trade backtest engine (cointegration mean-reversion).

Per pair: build rolling cointegration panel (B1), compute dynamic z-threshold (B2),
detect spread signals, simulate 2-leg pair trade with PaperBroker.

WHY standalone instead of production wire-through: Step 14B/15 cost weeks wiring
FUNDING_FLIP into production then proved no edge. Goal Step 17 = confirm edge
cheaply FIRST (this script), wire to production daemon only after STABLE_POSITIVE
verdict on ≥1 pair (Step 18).

Exit logic (mean-reversion semantics):
  - long-spread (entry z<<0): exit when z >= -exit_z (revert toward 0)
  - short-spread (entry z>>0): exit when z <= exit_z
  - stop-loss: |z| >= max_z_kill (spread broke, no longer mean-reverting)
  - time-stop: hold > half_life × time_stop_hl_mult (default 4×) capped by max_hold_hours

Usage:
  python3 scripts/backtest_cointegration.py \
      --start 2025-04-27 --end 2026-04-26 \
      --label step17_coint_1y \
      --entry-z-mode dynamic --slippage-bps 10
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

from data_pipe.cointegration import (  # noqa: E402
    detect_spread_signals,
    dynamic_entry_threshold,
    parse_spread_payload,
    rolling_cointegration_panel,
)
from data_pipe.paper_broker import PaperBroker  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402
from scripts.backtest_sweep import (  # noqa: E402
    classify,
    per_quarter_stats,
    split_into_quarters,
)


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"


def _build_inmemory_broker(
    starting_capital: float, slippage_bps: float
) -> tuple[PaperBroker, duckdb.DuckDBPyConnection]:
    """In-memory broker untuk satu pair-run. Tidak menyentuh production DB."""
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


def simulate_pair_trades(
    panel: pd.DataFrame,
    events: list[dict],
    pair: tuple[str, str],
    broker: PaperBroker,
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    risk_pct_per_pair: float = 5.0,
    exit_z: float = 0.5,
    max_z_kill: float = 4.0,
    time_stop_hl_mult: float = 4.0,
    max_hold_hours: int = 168,
) -> list[dict]:
    """Simulate 2-leg trades. Returns list of trade dicts.

    Sizing: leg A notional = (equity × risk_pct_per_pair / 100).
            leg B notional = leg A notional × |hedge_ratio|.
    """
    if panel.empty or not events:
        return []

    panel_ts = [pd.Timestamp(t) for t in panel["ts"].values]
    ts_to_idx = {t: i for i, t in enumerate(panel_ts)}
    a_close_map: dict = {pd.Timestamp(t): float(c) for t, c in
                          zip(df_a["ts"], df_a["close"])}
    b_close_map: dict = {pd.Timestamp(t): float(c) for t, c in
                          zip(df_b["ts"], df_b["close"])}

    trades: list[dict] = []
    open_until_ts: Optional[pd.Timestamp] = None  # serial pair-trades (1 at a time)

    for ev in events:
        parsed = parse_spread_payload(ev["payload"])
        side = parsed.get("side")
        beta = float(parsed.get("hedge_ratio", 0))
        hl = float(parsed.get("hl", 24.0))
        entry_ts = pd.Timestamp(ev["ts"])

        # Serialize: skip event if previous trade still open at entry_ts
        if open_until_ts is not None and entry_ts <= open_until_ts:
            continue

        if side not in ("long-spread", "short-spread"):
            continue
        if side == "long-spread":
            dir_a, dir_b = "LONG", "SHORT"
        else:
            dir_a, dir_b = "SHORT", "LONG"

        # Pricing must be available
        entry_a_price = a_close_map.get(entry_ts)
        entry_b_price = b_close_map.get(entry_ts)
        if not entry_a_price or not entry_b_price or beta == 0:
            continue

        # Sizing
        equity_now = broker.equity_now()
        leg_a_notional = equity_now * (risk_pct_per_pair / 100.0)
        size_a = leg_a_notional / entry_a_price
        size_b = (leg_a_notional * abs(beta)) / entry_b_price

        tid_a = broker.open_position(
            pair[0], dir_a, size_a, entry_a_price,
            ts=entry_ts.to_pydatetime(),
        )
        tid_b = broker.open_position(
            pair[1], dir_b, size_b, entry_b_price,
            ts=entry_ts.to_pydatetime(),
        )

        # Find exit
        start_idx = ts_to_idx.get(entry_ts)
        if start_idx is None:
            # Should not happen since events come from panel
            broker.close_position(tid_a, entry_a_price, "no_panel_idx",
                                   ts=entry_ts.to_pydatetime())
            broker.close_position(tid_b, entry_b_price, "no_panel_idx",
                                   ts=entry_ts.to_pydatetime())
            continue

        time_stop_bars = min(int(hl * time_stop_hl_mult), max_hold_hours)
        exit_idx: Optional[int] = None
        exit_reason = "time_stop"
        max_j = min(start_idx + 1 + time_stop_bars, len(panel))
        for j in range(start_idx + 1, max_j):
            z = float(panel["z_score"].iloc[j])
            if side == "long-spread" and z >= -exit_z:
                exit_idx = j
                exit_reason = "mean_revert"
                break
            if side == "short-spread" and z <= exit_z:
                exit_idx = j
                exit_reason = "mean_revert"
                break
            if abs(z) >= max_z_kill:
                exit_idx = j
                exit_reason = "stop_loss"
                break
        if exit_idx is None:
            exit_idx = max_j - 1 if max_j > start_idx + 1 else start_idx

        exit_ts = panel_ts[exit_idx]
        exit_a_price = a_close_map.get(exit_ts, entry_a_price)
        exit_b_price = b_close_map.get(exit_ts, entry_b_price)

        res_a = broker.close_position(
            tid_a, exit_a_price, exit_reason, ts=exit_ts.to_pydatetime()
        )
        res_b = broker.close_position(
            tid_b, exit_b_price, exit_reason, ts=exit_ts.to_pydatetime()
        )

        trades.append({
            "pair": f"{pair[0]}|{pair[1]}",
            "side": side,
            "entry_ts": entry_ts.to_pydatetime(),
            "exit_ts": exit_ts.to_pydatetime(),
            "entry_z": float(parsed.get("z", 0)),
            "exit_reason": exit_reason,
            "leg_a_pnl": res_a["pnl"],
            "leg_b_pnl": res_b["pnl"],
            "pnl": float((res_a["pnl"] or 0) + (res_b["pnl"] or 0)),
            "hedge_ratio": beta,
            "hold_bars": exit_idx - start_idx,
        })
        open_until_ts = exit_ts

    return trades


def run_backtest_for_pair(
    db_path: Path,
    pair: tuple[str, str],
    start: datetime,
    end: datetime,
    lookback_bars: int = 720,
    refit_freq_bars: int = 24,
    z_window: int = 50,
    entry_z_mode: str = "fixed",
    entry_z_fixed: float = 2.0,
    entry_z_percentile: float = 95.0,
    starting_capital: float = 10000.0,
    risk_pct_per_pair: float = 5.0,
    exit_z: float = 0.5,
    max_z_kill: float = 4.0,
    slippage_bps: float = 10.0,
    quarters_n: int = 4,
) -> dict:
    """Single-pair backtest. Return summary dict."""
    storage = Storage(db_path=db_path, read_only=True)
    df_a = storage.get_ohlcv("bybit", pair[0], "1h", limit=100_000)
    df_b = storage.get_ohlcv("bybit", pair[1], "1h", limit=100_000)
    storage.close()

    # Filter window (ts column might be tz-aware or naive — normalize)
    if not df_a.empty:
        df_a = df_a.copy()
        df_a["ts"] = pd.to_datetime(df_a["ts"])
        df_a = df_a[(df_a["ts"] >= start) & (df_a["ts"] <= end)]
    if not df_b.empty:
        df_b = df_b.copy()
        df_b["ts"] = pd.to_datetime(df_b["ts"])
        df_b = df_b[(df_b["ts"] >= start) & (df_b["ts"] <= end)]

    if df_a.empty or df_b.empty:
        return {
            "pair": list(pair), "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0, "panel_rows": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "entry_z_used": 0.0, "panel_coint_rate": 0.0,
            "verdict_reason": "no ohlcv for one or both legs",
            "per_quarter": [], "trades": [],
        }

    panel = rolling_cointegration_panel(
        df_a, df_b, pair,
        lookback_bars=lookback_bars,
        refit_freq_bars=refit_freq_bars,
        z_window=z_window,
    )
    if panel.empty:
        return {
            "pair": list(pair), "verdict": "NO_DATA",
            "n_events": 0, "n_trades": 0, "panel_rows": 0,
            "realized_return_pct": 0.0, "sign_consistency": "0/0",
            "entry_z_used": 0.0, "panel_coint_rate": 0.0,
            "verdict_reason": f"panel empty (need ≥{lookback_bars+z_window} bars)",
            "per_quarter": [], "trades": [],
        }

    if entry_z_mode == "dynamic":
        entry_z = dynamic_entry_threshold(panel, percentile=entry_z_percentile)
    else:
        entry_z = float(entry_z_fixed)

    events = detect_spread_signals(
        panel, pair, entry_z=entry_z, max_z_kill=max_z_kill,
    )

    broker, conn = _build_inmemory_broker(starting_capital, slippage_bps)
    try:
        trades = simulate_pair_trades(
            panel, events, pair, broker,
            df_a=df_a, df_b=df_b,
            risk_pct_per_pair=risk_pct_per_pair,
            exit_z=exit_z, max_z_kill=max_z_kill,
        )
    finally:
        conn.close()

    closed_simple = [{"exit_ts": t["exit_ts"], "pnl": t["pnl"]} for t in trades]
    quarters = split_into_quarters(start, end, quarters_n)
    per_q = per_quarter_stats(closed_simple, quarters, starting_capital)
    cls = classify(per_q, n_total_closed=len(trades))
    realized = sum(t["pnl"] for t in trades)
    return {
        "pair": list(pair),
        "entry_z_mode": entry_z_mode,
        "entry_z_used": float(entry_z),
        "n_events": len(events),
        "n_trades": len(trades),
        "panel_rows": len(panel),
        "panel_coint_rate": float(panel["is_cointegrated"].mean()),
        "realized_pnl": float(realized),
        "realized_return_pct": float(realized / starting_capital * 100.0),
        "verdict": cls["verdict"],
        "verdict_reason": cls.get("reason", ""),
        "sign_consistency": cls["sign_consistency"],
        "per_quarter": per_q,
        "trades": trades,
    }


def _write_comparison_md(
    summaries: list[dict], path: Path,
    start: datetime, end: datetime,
    args: argparse.Namespace,
) -> None:
    lines = [
        "# Step 17.B3 — Cointegration pair-trade backtest",
        "",
        f"**Window:** {start.date()} → {end.date()} ({(end - start).days} days)  ",
        f"**Quarters:** {args.quarters}  ",
        f"**Entry-z mode:** {args.entry_z_mode}  ",
        f"**Lookback bars:** {args.lookback_bars}  ",
        f"**Refit freq:** {args.refit_freq_bars} bars  ",
        f"**Slippage:** {args.slippage_bps} bps/side  ",
        f"**Risk per pair:** {args.risk_pct_per_pair}% equity (leg A notional)",
        "",
        "## Per-pair summary",
        "",
        "| Pair | Verdict | n_trades | Realized% | Sign | EntryZ | CoIntRate |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        pair = "|".join(s["pair"])
        entry_z = s.get("entry_z_used", 0.0)
        coint = s.get("panel_coint_rate", 0.0)
        lines.append(
            f"| {pair} | **{s['verdict']}** | {s['n_trades']} | "
            f"{s['realized_return_pct']:+.2f}% | {s['sign_consistency']} | "
            f"{entry_z:.2f} | {coint:.2f} |"
        )
    lines.append("")
    lines.append("## Per-quarter detail")
    for s in summaries:
        pair = "|".join(s["pair"])
        lines.append(f"\n### {pair} → {s['verdict']}")
        lines.append(f"_{s.get('verdict_reason', '')}_")
        lines.append("")
        lines.append("| Q | n | realized% | sign |")
        lines.append("|---|---|---|---|")
        for q in s.get("per_quarter", []):
            lines.append(
                f"| {q['quarter']} | {q['n_trades_closed']} | "
                f"{q['realized_return_pct']:+.2f}% | {q['sign']} |"
            )
    path.write_text("\n".join(lines))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Step 17.B3 cointegration pair-trade backtest",
    )
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--label", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--pairs", default="all",
                   help="comma-separated 'A/USDT|B/USDT' or 'all' from config")
    p.add_argument("--entry-z-mode", choices=["fixed", "dynamic"], default="fixed")
    p.add_argument("--entry-z-fixed", type=float, default=2.0)
    p.add_argument("--entry-z-percentile", type=float, default=95.0)
    p.add_argument("--lookback-bars", type=int, default=720,
                   help="Default 720 = 30d × 24h")
    p.add_argument("--refit-freq-bars", type=int, default=24,
                   help="Default 24 = daily refit")
    p.add_argument("--risk-pct-per-pair", type=float, default=5.0)
    p.add_argument("--exit-z", type=float, default=0.5)
    p.add_argument("--max-z-kill", type=float, default=4.0)
    p.add_argument("--slippage-bps", type=float, default=10.0)
    p.add_argument("--quarters", type=int, default=4)
    args = p.parse_args(argv)

    cfg = yaml.safe_load((ROOT / "config" / "pairs.yaml").read_text())
    if args.pairs == "all":
        candidates = [tuple(pp) for pp in cfg.get("cointegration_candidates", [])]
    else:
        candidates = []
        for pp in args.pairs.split(","):
            a, b = pp.split("|")
            candidates.append((a.strip(), b.strip()))

    if not candidates:
        print("ERROR: no candidate pairs", file=sys.stderr)
        return 2

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    out_dir = RESULTS_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[bt-coint] label={args.label}")
    print(f"[bt-coint] window: {start.date()} → {end.date()}")
    print(f"[bt-coint] candidates: {len(candidates)}")

    summaries: list[dict] = []
    for pair in candidates:
        print(f"[bt-coint] {pair[0]} | {pair[1]} ...")
        summary = run_backtest_for_pair(
            db_path=Path(args.db), pair=pair, start=start, end=end,
            lookback_bars=args.lookback_bars,
            refit_freq_bars=args.refit_freq_bars,
            entry_z_mode=args.entry_z_mode,
            entry_z_fixed=args.entry_z_fixed,
            entry_z_percentile=args.entry_z_percentile,
            risk_pct_per_pair=args.risk_pct_per_pair,
            exit_z=args.exit_z,
            max_z_kill=args.max_z_kill,
            slippage_bps=args.slippage_bps,
            quarters_n=args.quarters,
        )
        summaries.append(summary)
        print(f"[bt-coint]   verdict={summary['verdict']} "
              f"n_trades={summary['n_trades']} "
              f"realized={summary['realized_return_pct']:+.2f}% "
              f"sign={summary['sign_consistency']}")

    (out_dir / "summary.json").write_text(
        json.dumps({"summaries": summaries}, indent=2, default=str)
    )
    _write_comparison_md(summaries, out_dir / "comparison.md", start, end, args)
    print(f"[bt-coint] DONE — {out_dir / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
