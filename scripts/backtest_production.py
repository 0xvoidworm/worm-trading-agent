#!/usr/bin/env python3
"""Production-path backtest harness — Step 5.

Replay 1h cycles via EXACT production code path:
    set_as_of → risk_check_and_engage → run_health_check → build_context
    → run_advisor → apply_verdict → run_executor

Look-ahead bias prevention via StorageClamped (view-based time clamping).
Trades booked on PaperBroker (in-memory DuckDB shared with StorageClamped).
Killswitch persisted to per-run tmp_dir (filesystem semantics preserved).

Output (per --label):
    data/backtest_results/<label>/{summary.json, trades.csv,
                                   equity_curve.csv, report.md}

Edge verdict heuristic (vs BTC buy-and-hold baseline):
    positive : return > BnH AND sharpe > 0.5
    neutral  : within ±10pp of BnH return
    negative : return < BnH-10pp OR sharpe < 0

Usage:
    python scripts/backtest_production.py \\
        --start 2026-02-26 --end 2026-04-25 --label step5_initial
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.auto_advisor import run_advisor_cycle  # noqa: E402
from data_pipe.paper_broker import PaperBroker  # noqa: E402
from data_pipe.portfolio import DEFAULT_STARTING_CAPITAL  # noqa: E402
from data_pipe.risk_guard import check_and_engage as risk_check_and_engage  # noqa: E402
from data_pipe.storage import Storage  # noqa: E402
from data_pipe.storage_clamped import StorageClamped  # noqa: E402


CONFIG_PATH = ROOT / "config" / "pairs.yaml"
DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"


# ─── helpers ─────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    """Parse YYYY-MM-DD → naive UTC midnight."""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def _generate_cycles(start: datetime, end: datetime, hours: int = 1) -> list[datetime]:
    """Inclusive start..end stepping `hours` jam. Aligned to bar boundary."""
    cycles: list[datetime] = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        cycles.append(t)
        t = t + timedelta(hours=hours)
    return cycles


def _mark_to_market(broker: PaperBroker, storage: StorageClamped,
                    exchange: str, tf: str) -> float:
    """starting_capital + realized + sum unrealized OPEN trades on as_of close."""
    realized_row = broker._require_conn().execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL"
    ).fetchone()
    realized = float(realized_row[0] or 0.0)

    open_rows = broker._require_conn().execute(
        "SELECT pair, direction, entry_price, size FROM trade_journal "
        "WHERE status='OPEN' AND direction IN ('LONG','SHORT') "
        "  AND entry_price IS NOT NULL AND size IS NOT NULL"
    ).fetchall()
    unreal = 0.0
    for pair, direction, entry, size in open_rows:
        row = storage._conn.execute(
            "SELECT close FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? "
            "ORDER BY ts DESC LIMIT 1",
            [exchange, str(pair), tf],
        ).fetchone()
        if row is None or row[0] is None:
            continue
        last = float(row[0])
        side = 1.0 if direction == "LONG" else -1.0
        unreal += (last - float(entry)) * float(size) * side
    return broker.starting_capital + realized + unreal


def _bnh_baseline(storage: Storage, symbol: str, exchange: str, tf: str,
                  start: datetime, end: datetime,
                  starting_capital: float) -> dict:
    """Buy-and-hold: full equity into `symbol` at first bar >= start, mark to
    last bar <= end. Return {start_price, end_price, return_pct, equity_curve}.
    """
    rows = storage._conn.execute(
        "SELECT ts, close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? "
        "  AND ts >= ? AND ts <= ? ORDER BY ts",
        [exchange, symbol, tf, start, end],
    ).fetchall()
    if not rows or rows[0][1] is None:
        return {"start_price": None, "end_price": None, "return_pct": 0.0,
                "curve": []}
    start_p = float(rows[0][1])
    end_p = float(rows[-1][1])
    units = starting_capital / start_p
    ret_pct = (end_p / start_p - 1.0) * 100.0
    curve = [(ts, units * float(c)) for ts, c in rows if c is not None]
    return {"start_price": start_p, "end_price": end_p,
            "return_pct": ret_pct, "curve": curve}


def _compute_metrics(equity_curve: list[tuple[datetime, float]],
                     starting_capital: float) -> dict:
    """Total return, max drawdown, Sharpe (hourly returns annualized)."""
    if not equity_curve:
        return {"final_equity": starting_capital, "return_pct": 0.0,
                "max_drawdown_pct": 0.0, "sharpe_annualized": 0.0,
                "n_bars": 0}
    eqs = [e for _, e in equity_curve]
    final = eqs[-1]
    ret_pct = (final / starting_capital - 1.0) * 100.0
    # Max DD
    peak = eqs[0]
    max_dd = 0.0
    for e in eqs:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    # Sharpe — hourly returns, annualize sqrt(24*365)
    rets = []
    for i in range(1, len(eqs)):
        if eqs[i - 1] > 0:
            rets.append(eqs[i] / eqs[i - 1] - 1.0)
    if len(rets) < 2:
        sharpe = 0.0
    else:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mu / sd) * math.sqrt(24 * 365) if sd > 0 else 0.0
    return {"final_equity": final, "return_pct": ret_pct,
            "max_drawdown_pct": max_dd, "sharpe_annualized": sharpe,
            "n_bars": len(eqs)}


def _verdict_edge(strat: dict, bnh: dict, n_closed: int) -> str:
    """insufficient_sample | positive | neutral | negative — heuristic, no tuning.

    Step 6: n_closed < INSUFFICIENT_SAMPLE_THRESHOLD → INSUFFICIENT_SAMPLE,
    statistik tidak valid. Threshold = 30 (rule of thumb minimum sample
    untuk Central Limit Theorem applicability terhadap rata-rata PnL).
    """
    if n_closed < INSUFFICIENT_SAMPLE_THRESHOLD:
        return "insufficient_sample"
    s = strat["return_pct"]
    b = bnh["return_pct"]
    if s > b and strat["sharpe_annualized"] > 0.5:
        return "positive"
    if s < (b - 10.0) or strat["sharpe_annualized"] < 0:
        return "negative"
    return "neutral"


def _realized_pct(broker: PaperBroker, starting_capital: float) -> float:
    """Realized-only return % — sum PnL trades CLOSED dibagi starting."""
    row = broker._require_conn().execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL"
    ).fetchone()
    realized = float(row[0] or 0.0)
    return (realized / starting_capital) * 100.0 if starting_capital > 0 else 0.0


# ─── main loop ───────────────────────────────────────────────────────────

INSUFFICIENT_SAMPLE_THRESHOLD = 30  # n_closed < ini → edge verdict statistik invalid


# ─── trading-only health shim ────────────────────────────────────────────

class _trading_only_health_shim:
    """Context manager: temporarily replace `executor.run_health_check`
    sehingga gate #3 di-evaluate hanya berdasarkan trading dimension.

    Production executor tidak diubah — shim aktif hanya selama backtest
    cycle eksekusi, lalu di-restore. Tidak ada race karena backtest
    single-threaded.
    """
    def __init__(self, cycle_ts: datetime):
        self.cycle_ts = cycle_ts
        self._orig = None

    def __enter__(self):
        from data_pipe import executor as _exec_mod
        from data_pipe.health import run_health_check_split
        self._orig = _exec_mod.run_health_check

        def _patched(storage, cfg, *, now_utc=None):
            split = run_health_check_split(
                storage, cfg, now_utc=now_utc or self.cycle_ts,
            )
            t = split["trading"]
            return {
                # Mirror existing run_health_check shape — only `status` key
                # is read by executor (`if health["status"] != "OK": ...`).
                "status": t["status"],
                "generated_at": split["generated_at"],
                "summary": split["summary"],
                "ohlcv_gaps": t["ohlcv_gaps"],
                "ohlcv_stale": t["ohlcv_stale"],
                "funding_stale": split["operational"]["funding_stale"],
                "feeds": split["operational"]["feeds"],
            }

        _exec_mod.run_health_check = _patched
        return self

    def __exit__(self, *exc):
        from data_pipe import executor as _exec_mod
        _exec_mod.run_health_check = self._orig
        return False


@dataclass
class CycleStat:
    ts: datetime
    trading_health: str
    operational_health: str
    risk: str
    risk_pct: float
    advisor_verdict: str
    exec_action: str
    exec_reason: str
    gate_reason: str
    equity: float
    n_entries: int = 0     # Step 8: count of OPEN actions in this cycle
    n_exits: int = 0       # Step 8: count of CLOSE actions in this cycle


def _deep_merge(base: dict, overrides: dict) -> dict:
    """Recursive dict merge — overrides win at leaf level."""
    out = dict(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def run_backtest(start: datetime, end: datetime, label: str,
                 db_path: Path = DEFAULT_DB,
                 cycle_hours: int = 1,
                 verbose: bool = False,
                 regime_t_threshold: float | None = None,
                 cfg_overrides: dict | None = None,
                 slippage_bps: float = 0.0) -> dict:
    """Execute backtest. Return summary dict, also persist all artifacts.

    `regime_t_threshold`: optional override untuk REGIME_CHANGE t-stat
    threshold yang dipakai auto_advisor. None → pakai default modul (8.0).
    Dipakai oleh sweep harness (Step 7) untuk explore sensitivity.

    `cfg_overrides`: optional dict deep-merged into yaml-loaded cfg AFTER load.
    Dipakai Step 14B sweep untuk inject `advisor.detector_whitelist` /
    `exits.max_hold_hours` / `exits.regime_flip_t_threshold` per-variant tanpa
    mutate config file di disk.
    """
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    if cfg_overrides:
        cfg = _deep_merge(cfg, cfg_overrides)
    starting_capital = float(
        (cfg.get("portfolio") or {}).get("starting_capital",
                                         DEFAULT_STARTING_CAPITAL)
    )
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]

    out_dir = RESULTS_ROOT / label
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Per-run tmp_dir untuk killswitch + executor.log + verdict file —
    # filesystem semantics preserved (production code unchanged).
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"bt_{label}_"))

    print(f"[backtest] label={label}")
    print(f"[backtest] window: {start.date()} → {end.date()}")
    print(f"[backtest] starting_capital={starting_capital:.2f}")
    print(f"[backtest] tmp_dir={tmp_dir}")
    print(f"[backtest] out_dir={out_dir}")

    cycles = _generate_cycles(start, end, cycle_hours)
    print(f"[backtest] cycles: {len(cycles)} ({cycle_hours}h each)")

    # Build clamped storage + broker
    src = Storage(db_path=db_path, read_only=True)
    broker = PaperBroker(
        starting_capital=starting_capital,
        slippage_bps_per_side=slippage_bps,
    )
    clamped = StorageClamped(source=src, broker=broker)
    src.close()
    if slippage_bps != 0.0:
        print(f"[backtest] slippage={slippage_bps} bps/side")

    # BnH baseline (computed before loop — uses raw historical data)
    src_for_bnh = Storage(db_path=db_path, read_only=True)
    bnh = _bnh_baseline(src_for_bnh, "BTC/USDT", exchange, tf,
                        start, end, starting_capital)
    src_for_bnh.close()

    cycle_stats: list[CycleStat] = []
    equity_curve: list[tuple[datetime, float]] = []

    from data_pipe.health import run_health_check_split

    for i, cycle_ts in enumerate(cycles):
        clamped.set_as_of(cycle_ts)

        # Risk guard
        try:
            rs = risk_check_and_engage(clamped, cfg, tmp_dir, now_utc=cycle_ts)
            risk_status = rs["status"]
            risk_pct = rs["daily_pnl_pct"]
        except Exception as e:
            risk_status, risk_pct = "ERR", 0.0
            if verbose:
                print(f"  [warn] risk: {e!r}")

        # Split health — backtest hanya gate trading dimension. operational
        # tetap di-record di equity_curve untuk transparency.
        try:
            health_split = run_health_check_split(clamped, cfg, now_utc=cycle_ts)
            t_status = health_split["trading"]["status"]
            o_status = health_split["operational"]["status"]
        except Exception as e:
            t_status = o_status = "ERR"
            health_split = None
            if verbose:
                print(f"  [warn] health: {e!r}")

        # Advisor + executor — ENTRY gated only on trading health.
        # Step 8: pakai run_advisor_cycle (multi-trade-per-cycle).
        v_type = "SKIP"
        action = "SKIP"
        reason = ""
        gate_reason = ""
        n_entries_this_cycle = 0
        n_exits_this_cycle = 0
        if t_status == "OK":
            try:
                _adv_kwargs = {"now_utc": cycle_ts}
                if regime_t_threshold is not None:
                    _adv_kwargs["threshold"] = regime_t_threshold
                # Trading-only health shim wraps the entire cycle (executor
                # gate #3 reads run_health_check during each iteration).
                with _trading_only_health_shim(cycle_ts):
                    cycle_result = run_advisor_cycle(
                        clamped, cfg, tmp_dir, **_adv_kwargs,
                    )
                n_entries_this_cycle = cycle_result["n_entries"]
                n_exits_this_cycle = cycle_result["n_exits"]
                # Last verdict & exec action — used for legacy CycleStat fields
                # (display + report). Multi-trade summary tracked via n_entries/n_exits.
                last = (
                    (cycle_result["entries"] + cycle_result["exits"])
                    [-1]
                    if (cycle_result["entries"] or cycle_result["exits"])
                    else None
                )
                if last is not None:
                    action = last.get("action", "?")
                    reason = last.get("reason", "")
                    v_type = (
                        "EXIT" if cycle_result["exits"]
                        else "ENTRY" if cycle_result["entries"]
                        else "WAIT"
                    )
                else:
                    action = "WAIT"
                    v_type = "WAIT"
                if n_entries_this_cycle + n_exits_this_cycle > 1:
                    # Tag multi-trade cycles in the action label for visibility.
                    action = (
                        f"MULTI(E{n_entries_this_cycle}X{n_exits_this_cycle})"
                    )
            except Exception as e:
                action = "ERR"
                reason = repr(e)
                if verbose:
                    print(f"  [warn] adv/exec: {e!r}")
        else:
            action = f"GATED_TRADING({t_status})"
            gate_reason = f"trading={t_status}"

        eq = _mark_to_market(broker, clamped, exchange, tf)
        cycle_stats.append(CycleStat(
            ts=cycle_ts, trading_health=t_status, operational_health=o_status,
            risk=risk_status, risk_pct=risk_pct, advisor_verdict=v_type,
            exec_action=action, exec_reason=reason,
            gate_reason=gate_reason, equity=eq,
            n_entries=n_entries_this_cycle, n_exits=n_exits_this_cycle,
        ))
        equity_curve.append((cycle_ts, eq))

        if verbose or (i % 100 == 0):
            print(
                f"  cycle {i + 1}/{len(cycles)} {cycle_ts:%Y-%m-%d %H:%M} "
                f"trading={t_status} oper={o_status} risk={risk_status} "
                f"daily={risk_pct:+.2f}% verdict={v_type} "
                f"exec={action} entries={n_entries_this_cycle} "
                f"exits={n_exits_this_cycle} eq={eq:.2f}"
            )

    # Metrics
    strat = _compute_metrics(equity_curve, starting_capital)
    n_total = int(broker._require_conn().execute(
        "SELECT COUNT(*) FROM trade_journal"
    ).fetchone()[0])
    n_closed = int(broker._require_conn().execute(
        "SELECT COUNT(*) FROM trade_journal WHERE status='CLOSED'"
    ).fetchone()[0])
    n_open_at_end = n_total - n_closed
    edge = _verdict_edge(strat, bnh, n_closed)
    realized_pct = _realized_pct(broker, starting_capital)
    mtm_pct = strat["return_pct"]  # mark-to-market (final eq vs starting)

    # Health + gate breakdowns
    cycles_health_breakdown = {
        "trading_OK": sum(1 for c in cycle_stats if c.trading_health == "OK"),
        "trading_GAP": sum(1 for c in cycle_stats if c.trading_health == "GAP"),
        "trading_STALE": sum(1 for c in cycle_stats if c.trading_health == "STALE"),
        "operational_OK": sum(1 for c in cycle_stats if c.operational_health == "OK"),
        "operational_GAP": sum(1 for c in cycle_stats if c.operational_health == "GAP"),
    }
    gate_reason_counts: dict[str, int] = {}
    for c in cycle_stats:
        if c.gate_reason:
            gate_reason_counts[c.gate_reason] = (
                gate_reason_counts.get(c.gate_reason, 0) + 1
            )

    # Persist artifacts
    _write_trades_csv(broker, out_dir / "trades.csv")
    _write_equity_csv(equity_curve, cycle_stats, out_dir / "equity_curve.csv", bnh)
    # Step 8 — multi-trade-per-cycle stats
    total_cycle_entries = sum(c.n_entries for c in cycle_stats)
    total_cycle_exits = sum(c.n_exits for c in cycle_stats)
    multi_trade_cycles = sum(
        1 for c in cycle_stats if (c.n_entries + c.n_exits) > 1
    )
    cycles_with_entry = sum(1 for c in cycle_stats if c.n_entries > 0)
    cycles_with_exit = sum(1 for c in cycle_stats if c.n_exits > 0)

    summary = {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "cycle_hours": cycle_hours,
        "n_cycles": len(cycles),
        "starting_capital": starting_capital,
        "exchange": exchange,
        "timeframe": tf,
        "strategy": strat,
        "realized_return_pct": realized_pct,
        "mtm_return_pct": mtm_pct,
        "buy_hold_btc": {
            "start_price": bnh["start_price"],
            "end_price": bnh["end_price"],
            "return_pct": bnh["return_pct"],
        },
        "edge_verdict": edge,
        "insufficient_sample_threshold": INSUFFICIENT_SAMPLE_THRESHOLD,
        "n_trades_total": n_total,
        "n_trades_closed": n_closed,
        "n_trades_open_at_end": n_open_at_end,
        "cycles_health_breakdown": cycles_health_breakdown,
        "gate_reason_counts": gate_reason_counts,
        "tmp_dir": str(tmp_dir),
        "regime_t_threshold": regime_t_threshold,
        # Step 8 multi-trade orchestrator stats
        "multi_trade": {
            "total_cycle_entries": total_cycle_entries,
            "total_cycle_exits": total_cycle_exits,
            "cycles_with_entry": cycles_with_entry,
            "cycles_with_exit": cycles_with_exit,
            "multi_trade_cycles": multi_trade_cycles,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2,
                                                     default=str))
    _write_report_md(out_dir / "report.md", summary, cycle_stats, broker)

    clamped.close()
    print(f"\n[backtest] DONE — edge_verdict: {edge.upper()}")
    if edge == "insufficient_sample":
        print(f"[backtest] WARNING: only {n_closed} closed trades < "
              f"{INSUFFICIENT_SAMPLE_THRESHOLD} → statistically meaningless")
    print(f"[backtest] strategy MTM return={mtm_pct:+.2f}% "
          f"realized={realized_pct:+.2f}% "
          f"sharpe={strat['sharpe_annualized']:.2f} "
          f"max_dd={strat['max_drawdown_pct']:.2f}%")
    print(f"[backtest] trades: total={n_total} closed={n_closed} "
          f"open_at_end={n_open_at_end}")
    print(f"[backtest] cycles: trading OK={cycles_health_breakdown['trading_OK']}/"
          f"{len(cycle_stats)} (oper OK={cycles_health_breakdown['operational_OK']})")
    print(f"[backtest] BnH BTC return={bnh['return_pct']:+.2f}%")
    print(f"[backtest] artifacts in: {out_dir}")
    return summary


# ─── output writers ──────────────────────────────────────────────────────

def _write_trades_csv(broker: PaperBroker, path: Path) -> None:
    rows = broker._require_conn().execute(
        "SELECT id, ts, pair, direction, entry_price, size, status, "
        "exit_ts, exit_price, pnl, thesis, notes, signal_id "
        "FROM trade_journal ORDER BY ts"
    ).fetchall()
    cols = ["id", "ts", "pair", "direction", "entry_price", "size",
            "status", "exit_ts", "exit_price", "pnl", "thesis",
            "notes", "signal_id"]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(r)


def _write_equity_csv(curve: list[tuple[datetime, float]],
                      cycle_stats: list["CycleStat"],
                      path: Path, bnh: dict) -> None:
    """Equity curve CSV + per-cycle health/gate columns untuk transparency."""
    bnh_map = {ts: v for ts, v in bnh.get("curve", [])}
    stats_map = {c.ts: c for c in cycle_stats}
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity", "bnh_btc_equity",
                    "trading_health", "operational_health", "gate_reason",
                    "exec_action"])
        for ts, eq in curve:
            bnh_v = bnh_map.get(ts, "")
            cs = stats_map.get(ts)
            w.writerow([
                ts.isoformat(), f"{eq:.4f}",
                f"{bnh_v:.4f}" if isinstance(bnh_v, float) else "",
                cs.trading_health if cs else "",
                cs.operational_health if cs else "",
                cs.gate_reason if cs else "",
                cs.exec_action if cs else "",
            ])


def _write_report_md(path: Path, summary: dict,
                     cycles: list[CycleStat], broker: PaperBroker) -> None:
    s = summary["strategy"]
    b = summary["buy_hold_btc"]
    edge = summary["edge_verdict"].upper()
    n_closed = summary["n_trades_closed"]
    n_open_at_end = summary["n_trades_open_at_end"]
    insufficient = edge == "INSUFFICIENT_SAMPLE"

    # Action breakdown
    actions: dict[str, int] = {}
    for c in cycles:
        actions[c.exec_action] = actions.get(c.exec_action, 0) + 1

    # Closed trades stats
    closed = broker._require_conn().execute(
        "SELECT pnl FROM trade_journal WHERE status='CLOSED' AND pnl IS NOT NULL"
    ).fetchall()
    pnls = [float(r[0]) for r in closed]
    n_wins = sum(1 for p in pnls if p > 0)
    n_losses = sum(1 for p in pnls if p < 0)
    win_rate = (n_wins / n_closed * 100.0) if n_closed else 0.0
    avg_pnl = (sum(pnls) / n_closed) if n_closed else 0.0
    best = max(pnls) if pnls else 0.0
    worst = min(pnls) if pnls else 0.0

    hb = summary["cycles_health_breakdown"]
    n_cycles = summary["n_cycles"]

    lines = [
        f"# Backtest Report — `{summary['label']}`",
        "",
        f"**Window:** {summary['start']} → {summary['end']}  ",
        f"**Cycles:** {summary['n_cycles']} × {summary['cycle_hours']}h  ",
        f"**Exchange/TF:** {summary['exchange']} / {summary['timeframe']}  ",
        f"**Starting capital:** ${summary['starting_capital']:.2f}  ",
        "",
        f"## Edge Verdict: **{edge}**",
        "",
    ]
    if insufficient:
        lines += [
            f"> ⚠ **INSUFFICIENT_SAMPLE**: only `{n_closed}` closed trades "
            f"(< {summary['insufficient_sample_threshold']} threshold). "
            "Edge verdict statistically meaningless — return/sharpe figures "
            "below are descriptive, not inferential. Need more closed trades "
            "(longer window or denser signal regime) sebelum bisa conclude.",
            "",
        ]
    lines += [
        "## Returns — Realized vs Mark-to-Market",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Realized return % (closed trades only) | {summary['realized_return_pct']:+.2f}% |",
        f"| MTM return % (incl. open trades at end) | {summary['mtm_return_pct']:+.2f}% |",
        f"| Final equity (MTM) | ${s['final_equity']:.2f} |",
        f"| Max drawdown | {s['max_drawdown_pct']:.2f}% |",
        f"| Sharpe (annualized) | {s['sharpe_annualized']:.2f} |",
        f"| BnH BTC return % | {b['return_pct']:+.2f}% |",
        "",
        "## Trade Stats",
        "",
        f"- Total trades opened: **{summary['n_trades_total']}**",
        f"- Closed: **{n_closed}** | Open at end: **{n_open_at_end}** | "
        f"Wins: {n_wins} | Losses: {n_losses}",
        f"- Win rate (closed only): **{win_rate:.1f}%**",
        f"- Avg PnL/closed trade: ${avg_pnl:+.2f}",
        f"- Best: ${best:+.2f} | Worst: ${worst:+.2f}",
        "",
        "## Health Breakdown (per cycle)",
        "",
        "| Dimension | OK | GAP | STALE |",
        "|---|---|---|---|",
        f"| trading | {hb['trading_OK']} ({hb['trading_OK']/n_cycles*100:.1f}%) "
        f"| {hb['trading_GAP']} ({hb['trading_GAP']/n_cycles*100:.1f}%) "
        f"| {hb['trading_STALE']} ({hb['trading_STALE']/n_cycles*100:.1f}%) |",
        f"| operational | {hb['operational_OK']} ({hb['operational_OK']/n_cycles*100:.1f}%) "
        f"| {hb['operational_GAP']} ({hb['operational_GAP']/n_cycles*100:.1f}%) | — |",
        "",
        "_Backtest gates ENTRY hanya pada `trading.status != OK` (Step 6). "
        "Operational status (funding/news feeds) di-record untuk transparency "
        "tapi tidak gate trading decision._",
        "",
        "## Cycle Action Breakdown",
        "",
    ]
    for k, v in sorted(actions.items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{k}`: {v} ({v/n_cycles*100:.1f}%)")

    if summary["gate_reason_counts"]:
        lines += ["", "## Gate Reason Counts", ""]
        for k, v in sorted(summary["gate_reason_counts"].items(),
                            key=lambda kv: -kv[1]):
            lines.append(f"- `{k}`: {v}")

    lines += [
        "",
        "## Notes",
        "",
        f"- Sample-size threshold: ≥{summary['insufficient_sample_threshold']} "
        "closed trades required for verdict to be statistically meaningful.",
        "- Verdict heuristic (only applied when sample ≥ threshold): "
        "`positive` if return > BnH AND sharpe > 0.5; "
        "`negative` if return < BnH-10pp OR sharpe < 0; `neutral` otherwise.",
        "- Look-ahead bias prevented via `StorageClamped` "
        "(view-based time clamping over `_as_of` table).",
        "- Trades booked on `PaperBroker` with 0.11% per-side fee at close.",
        "- Killswitch state persisted to per-run `tmp_dir` (production "
        "filesystem semantics preserved).",
        "",
    ]
    path.write_text("\n".join(lines))


# ─── CLI ─────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Production-path backtest harness (Step 5)",
    )
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--label", required=True, help="Output dir suffix")
    p.add_argument("--db", default=str(DEFAULT_DB),
                   help="Source DuckDB path (read-only)")
    p.add_argument("--cycle-hours", type=int, default=1,
                   help="Cadence in hours (default 1, matches OHLCV bar)")
    p.add_argument("--regime-t-threshold", type=float, default=None,
                   help="Override REGIME_CHANGE t-stat threshold "
                        "(default: auto_advisor module default = 8.0)")
    p.add_argument("--slippage-bps", type=float, default=0.0,
                   help="Slippage bps per-side untuk PaperBroker (Step 17.A3). "
                        "Default 0.0 (no slippage = pre-17 behaviour). "
                        "Post-17 recommendation: 10.0 (≈ 20 bps round-trip realistic).")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end < start:
        print("ERROR: end < start", file=sys.stderr)
        return 2

    summary = run_backtest(
        start=start, end=end, label=args.label,
        db_path=Path(args.db), cycle_hours=args.cycle_hours,
        verbose=args.verbose,
        regime_t_threshold=args.regime_t_threshold,
        slippage_bps=args.slippage_bps,
    )
    return 0 if summary["edge_verdict"] != "ERR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
