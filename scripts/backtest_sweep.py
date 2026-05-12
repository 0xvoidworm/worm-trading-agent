#!/usr/bin/env python3
"""Threshold sensitivity sweep + walk-forward consistency check — Step 7.

Wrapper di atas backtest_production.py. Untuk tiap threshold di --thresholds,
jalankan full backtest pada window yang sama, lalu split window jadi N
non-overlapping quarters dan kelaskan hasil per-threshold:

  STABLE_POSITIVE: ≥3/4 quarter realized return positive
  STABLE_NEGATIVE: ≥3/4 quarter realized return negative
  INCONSISTENT:    2/2 mixed OR ada quarter <8 trades closed
  INSUFFICIENT_SAMPLE: total trades closed < 30

Tidak ada "winner" pick. Report apa adanya supaya tidak terjebak p-hacking
(threshold hunt yang accidentally optimize untuk noise).

Usage:
    python scripts/backtest_sweep.py \\
        --start 2025-04-26 --end 2026-04-25 \\
        --thresholds "2.66,4,6,8" --quarters 4 \\
        --label step7_sweep_365d
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import backtest_production as bt  # noqa: E402


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"
MIN_QUARTER_TRADES = 8        # quarter dengan <ini = unreliable, → INCONSISTENT
INSUFFICIENT_TOTAL = 30        # mirror backtest_production.INSUFFICIENT_SAMPLE_THRESHOLD


# ─── window splitting ────────────────────────────────────────────────────

def split_into_quarters(start: datetime, end: datetime,
                         n: int) -> list[tuple[datetime, datetime]]:
    """Bagi [start, end] jadi n non-overlapping segments by day count."""
    if n < 1:
        raise ValueError("quarters must be >= 1")
    total_days = (end - start).days
    if total_days < n:
        raise ValueError(f"window {total_days}d too short for {n} quarters")
    seg_days = total_days // n
    segs: list[tuple[datetime, datetime]] = []
    cur = start
    for i in range(n):
        if i == n - 1:
            segs.append((cur, end))
        else:
            nxt = cur + timedelta(days=seg_days)
            segs.append((cur, nxt))
            cur = nxt
    return segs


# ─── per-quarter metric extraction from trades.csv ───────────────────────

def _read_closed_trades(trades_csv: Path) -> list[dict]:
    """Return list of CLOSED trade dicts: {exit_ts, pnl}."""
    rows: list[dict] = []
    if not trades_csv.exists():
        return rows
    with trades_csv.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            if (r.get("status") or "").upper() != "CLOSED":
                continue
            try:
                pnl = float(r["pnl"]) if r.get("pnl") not in (None, "") else None
                exit_ts = r.get("exit_ts") or ""
                if pnl is None or not exit_ts:
                    continue
                ts = datetime.fromisoformat(exit_ts.replace("Z", ""))
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                rows.append({"exit_ts": ts, "pnl": pnl})
            except (ValueError, KeyError):
                continue
    return rows


def _quarter_sign_label(n_trades: int, ret_pct: float,
                         min_trades: int) -> str:
    """Per-quarter taxonomy:
        NO_DATA  : zero trades closed in window
        SPARSE   : has data but <min_trades (unreliable sample)
        POSITIVE : ret_pct > 0
        NEGATIVE : ret_pct < 0
        FLAT     : ret_pct == 0 (rare — exact zero)
    """
    if n_trades == 0:
        return "NO_DATA"
    if n_trades < min_trades:
        return "SPARSE"
    if ret_pct > 0:
        return "POSITIVE"
    if ret_pct < 0:
        return "NEGATIVE"
    return "FLAT"


def per_quarter_stats(closed_trades: list[dict],
                       quarters: list[tuple[datetime, datetime]],
                       starting_capital: float) -> list[dict]:
    """Per-quarter: return_pct, n_trades, sign label (with NO_DATA / SPARSE).

    Denominator = starting_capital (NOT equity-at-quarter-start). Goal: apple-
    to-apple sign comparison antar quarter, BUKAN compounded-return measurement.
    Empty quarters get sign='NO_DATA' supaya tidak salah dihitung sebagai
    NEGATIVE/FLAT.
    """
    out: list[dict] = []
    for i, (q_start, q_end) in enumerate(quarters, 1):
        q_pnls = [t["pnl"] for t in closed_trades
                  if q_start <= t["exit_ts"] <= q_end]
        n = len(q_pnls)
        ret_pct = (sum(q_pnls) / starting_capital * 100.0
                   if starting_capital > 0 and n > 0 else 0.0)
        sign = _quarter_sign_label(n, ret_pct, MIN_QUARTER_TRADES)
        out.append({
            "quarter": i,
            "start": q_start.isoformat(),
            "end": q_end.isoformat(),
            "n_trades_closed": n,
            "realized_pnl": float(sum(q_pnls)) if n > 0 else 0.0,
            "realized_return_pct": ret_pct,
            "sign": sign,
        })
    return out


# ─── classifier ──────────────────────────────────────────────────────────

MIN_QUARTERS_WITH_DATA = 3   # <ini → INSUFFICIENT_SAMPLE_DISTRIBUTION

# Step 17.A4 sample-bias guard: kalau 1 quarter dominate |total realized PnL|
# >= ini, klasifikasi STABLE_* tidak reliable (1 quarter outlier driving result).
# Step 14B precedent: V2 "+2.66% 1y" pure Q11 outlier (>80% concentration).
# 60% threshold — di atas existing STABLE_POSITIVE test case (50% max), di bawah
# Step 14B outlier pattern (~80%). Catches Q-outlier driven false-positive.
OUTLIER_CONCENTRATION_THRESHOLD_PCT = 60.0


def _max_quarter_concentration(per_q: list[dict]) -> tuple[float, int]:
    """Return (max |q_pnl| / sum |q_pnls| × 100, max_quarter_id).

    Computed over quarters_with_data only (NO_DATA excluded). Returns (0, 0)
    kalau no data atau total abs = 0.
    """
    contribs = [
        (int(q["quarter"]), abs(float(q.get("realized_pnl", 0.0))))
        for q in per_q
        if q.get("sign") != "NO_DATA"
    ]
    if not contribs:
        return 0.0, 0
    total_abs = sum(p for _, p in contribs)
    if total_abs == 0:
        return 0.0, 0
    max_q, max_pnl = max(contribs, key=lambda x: x[1])
    return (max_pnl / total_abs) * 100.0, max_q


def classify(per_q: list[dict], n_total_closed: int) -> dict:
    """Klasifikasi 8 kemungkinan:

      INSUFFICIENT_SAMPLE             — total closed < INSUFFICIENT_TOTAL
      INSUFFICIENT_SAMPLE_DISTRIBUTION — quarters_with_data < MIN_QUARTERS_WITH_DATA
                                         (signal fire timpa-numpuk di sedikit quarter)
      OUTLIER_DRIVEN                  — 1 quarter |pnl| > OUTLIER_CONCENTRATION_THRESHOLD_PCT
                                         dari sum(|q_pnls|) → sample-bias, edge claim
                                         tidak reliable (Step 14B Q11 precedent)
      INCONSISTENT                    — ada quarter SPARSE (data tapi <8) ATAU
                                         mixed signs di quarters_with_data
      STABLE_POSITIVE / STABLE_NEGATIVE — ≥3 quarter (dari yang punya data)
                                         konsisten direction

    Sign consistency dihitung HANYA dari quarters_with_data — empty quarter
    tidak boleh skew klasifikasi sebagai 'negatif'.
    """
    nq = len(per_q)
    quarters_with_data = sum(1 for q in per_q if q["sign"] != "NO_DATA")
    quarters_no_data = nq - quarters_with_data
    n_pos = sum(1 for q in per_q if q["sign"] == "POSITIVE")
    n_neg = sum(1 for q in per_q if q["sign"] == "NEGATIVE")
    sparse_qs = [q["quarter"] for q in per_q if q["sign"] == "SPARSE"]
    cons = (f"{n_pos}/{quarters_with_data}"
            if quarters_with_data else "0/0")
    base = {
        "sign_consistency": cons,
        "quarters_with_data": quarters_with_data,
        "quarters_no_data": quarters_no_data,
    }

    if n_total_closed < INSUFFICIENT_TOTAL:
        return {**base, "verdict": "INSUFFICIENT_SAMPLE",
                "reason": f"only {n_total_closed} closed trades "
                          f"(<{INSUFFICIENT_TOTAL})"}

    if quarters_with_data < MIN_QUARTERS_WITH_DATA:
        return {**base, "verdict": "INSUFFICIENT_SAMPLE_DISTRIBUTION",
                "reason": f"only {quarters_with_data}/{nq} quarter(s) have "
                          f"trades (<{MIN_QUARTERS_WITH_DATA}) — sample "
                          "too concentrated"}

    # Step 17.A4: sample-bias guard sebelum check STABLE/INCONSISTENT
    max_conc_pct, max_q = _max_quarter_concentration(per_q)
    if max_conc_pct >= OUTLIER_CONCENTRATION_THRESHOLD_PCT:
        return {
            **base,
            "verdict": "OUTLIER_DRIVEN",
            "reason": (
                f"Q{max_q} contributes {max_conc_pct:.1f}% of |total realized "
                f"PnL| (≥{OUTLIER_CONCENTRATION_THRESHOLD_PCT}%) — single-quarter "
                "outlier dominates result, edge claim NOT reliable"
            ),
            "max_quarter_concentration_pct": round(max_conc_pct, 2),
            "max_quarter": max_q,
        }

    if sparse_qs:
        return {**base, "verdict": "INCONSISTENT",
                "reason": f"quarter(s) {sparse_qs} have <{MIN_QUARTER_TRADES} "
                          "trades (sparse, unreliable)"}

    # Step 18 fix: require MAJORITY of decided quarters, not just ≥3 absolute.
    # Sebelumnya: n_pos=3 vs n_neg=9 keliru STABLE_POSITIVE karena POS checked
    # first. Sekarang require n_pos > n_neg (atau vice versa) supaya verdict
    # mencerminkan dominant direction. Tie → INCONSISTENT.
    if n_pos >= 3 and n_pos > n_neg:
        return {**base, "verdict": "STABLE_POSITIVE",
                "reason": f"{n_pos}/{quarters_with_data} quarter(s) with data "
                          "are positive"}
    if n_neg >= 3 and n_neg > n_pos:
        return {**base, "verdict": "STABLE_NEGATIVE",
                "reason": f"{n_neg}/{quarters_with_data} quarter(s) with data "
                          "are negative"}
    return {**base, "verdict": "INCONSISTENT",
            "reason": f"mixed signs across {quarters_with_data} "
                      "quarter(s) with data"}


# ─── single-threshold run ────────────────────────────────────────────────

def run_one_threshold(threshold: float, start: datetime, end: datetime,
                       label_prefix: str, db_path: Path,
                       sweep_root: Path, quarters_n: int,
                       cycle_hours: int, verbose: bool,
                       slippage_bps: float = 0.0) -> dict:
    """Run one backtest at given threshold, compute per-quarter classification."""
    # Sub-label & redirect output ke sweep folder
    sub_label = f"{label_prefix}_t{threshold:g}"
    sub_dir = sweep_root / "per_threshold" / sub_label
    sub_dir.parent.mkdir(parents=True, exist_ok=True)

    # backtest_production.run_backtest writes ke RESULTS_ROOT/<label>;
    # kita re-point sementara supaya semua sub-run terkurung di sweep folder.
    orig_root = bt.RESULTS_ROOT
    bt.RESULTS_ROOT = sub_dir.parent
    try:
        t0 = time.time()
        summary = bt.run_backtest(
            start=start, end=end, label=sub_label,
            db_path=db_path, cycle_hours=cycle_hours,
            verbose=verbose, regime_t_threshold=threshold,
            slippage_bps=slippage_bps,
        )
        elapsed = time.time() - t0
    finally:
        bt.RESULTS_ROOT = orig_root

    trades_csv = sub_dir / "trades.csv"
    closed = _read_closed_trades(trades_csv)
    quarters = split_into_quarters(start, end, quarters_n)
    per_q = per_quarter_stats(closed, quarters,
                               summary["starting_capital"])
    cls = classify(per_q, summary["n_trades_closed"])

    return {
        "threshold": threshold,
        "label": sub_label,
        "elapsed_sec": elapsed,
        "n_trades_total": summary["n_trades_total"],
        "n_trades_closed": summary["n_trades_closed"],
        "n_trades_open_at_end": summary["n_trades_open_at_end"],
        "realized_return_pct": summary["realized_return_pct"],
        "mtm_return_pct": summary["mtm_return_pct"],
        "sharpe_annualized": summary["strategy"]["sharpe_annualized"],
        "max_drawdown_pct": summary["strategy"]["max_drawdown_pct"],
        "edge_verdict_backtest": summary["edge_verdict"],
        "per_quarter": per_q,
        "classification": cls,
        "cycles_health_breakdown": summary["cycles_health_breakdown"],
    }


# ─── output ──────────────────────────────────────────────────────────────

def write_comparison_md(sweep_summary: dict, out_path: Path) -> None:
    rs = sweep_summary["runs"]
    nq = sweep_summary["quarters"]
    lines = [
        f"# Threshold Sensitivity Sweep — `{sweep_summary['label']}`",
        "",
        f"**Window:** {sweep_summary['start']} → {sweep_summary['end']} "
        f"({sweep_summary['window_days']} days)  ",
        f"**Quarters:** {nq} non-overlapping segments  ",
        f"**Thresholds tested:** {len(rs)}  ",
        f"**Min trades/quarter for STABLE verdict:** {MIN_QUARTER_TRADES}  ",
        f"**Min total closed for non-INSUFFICIENT verdict:** {INSUFFICIENT_TOTAL}  ",
        "",
        "## Sensitivity Table",
        "",
    ]
    q_headers = " | ".join(f"Q{i+1} sign" for i in range(nq))
    qt_headers = " | ".join(f"Q{i+1} N (ret%)" for i in range(nq))
    lines.append(
        "| Threshold | Total | Closed | Open@End | Realized% | MTM% | Sharpe | "
        f"{q_headers} | {qt_headers} | QwD | Sign Cons | Verdict |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|"
        + "|".join(["---"] * (nq * 2))
        + "|---|---|---|"
    )
    for r in rs:
        q_signs = " | ".join(q["sign"] for q in r["per_quarter"])
        q_cells = " | ".join(
            (f"0 (NO_DATA)" if q["sign"] == "NO_DATA"
             else f"{q['n_trades_closed']} ({q['realized_return_pct']:+.2f})")
            for q in r["per_quarter"]
        )
        cls = r["classification"]
        lines.append(
            f"| t≥{r['threshold']:g} | {r['n_trades_total']} | "
            f"{r['n_trades_closed']} | {r['n_trades_open_at_end']} | "
            f"{r['realized_return_pct']:+.2f} | {r['mtm_return_pct']:+.2f} | "
            f"{r['sharpe_annualized']:.2f} | "
            f"{q_signs} | {q_cells} | "
            f"{cls['quarters_with_data']}/{nq} | "
            f"{cls['sign_consistency']} | **{cls['verdict']}** |"
        )

    lines += [
        "",
        "## Per-Threshold Detail",
        "",
    ]
    for r in rs:
        cls = r["classification"]
        lines += [
            f"### t≥{r['threshold']:g} → **{cls['verdict']}**",
            "",
            f"_{cls['reason']}_  ",
            f"_quarters_with_data: {cls['quarters_with_data']}/{nq} · "
            f"sign_consistency: {cls['sign_consistency']}_",
            "",
            "| Quarter | Window | Closed Trades | Realized PnL | Return% | Sign |",
            "|---|---|---|---|---|---|",
        ]
        for q in r["per_quarter"]:
            lines.append(
                f"| Q{q['quarter']} | "
                f"{q['start'][:10]} → {q['end'][:10]} | "
                f"{q['n_trades_closed']} | "
                f"${q['realized_pnl']:+.2f} | "
                f"{q['realized_return_pct']:+.2f}% | "
                f"`{q['sign']}` |"
            )
        hb = r["cycles_health_breakdown"]
        lines += [
            "",
            f"Health: trading_OK={hb['trading_OK']} "
            f"trading_GAP={hb['trading_GAP']} trading_STALE={hb['trading_STALE']} "
            f"| operational_OK={hb['operational_OK']}",
            f"Sub-run elapsed: {r['elapsed_sec']:.1f}s",
            "",
        ]

    lines += [
        "## Caveat",
        "",
        "Reported as-is. **NO cherry-picking, NO 'winner' announcement.** "
        "STABLE_POSITIVE results are CANDIDATES for further validation, "
        "NOT production-ready. Step 7 = sensitivity map + consistency check, "
        "BUKAN parameter selection.",
        "",
        "- `STABLE_POSITIVE` / `STABLE_NEGATIVE`: ≥3 quarter (dari yang punya "
        "data) konsisten direction.",
        "- `INCONSISTENT`: ada quarter SPARSE (1..<"
        f"{MIN_QUARTER_TRADES} trades) ATAU mixed signs di quarters_with_data.",
        f"- `INSUFFICIENT_SAMPLE`: total closed trades < {INSUFFICIENT_TOTAL} "
        "(below CLT applicability).",
        f"- `INSUFFICIENT_SAMPLE_DISTRIBUTION`: <{MIN_QUARTERS_WITH_DATA} "
        "quarters punya trades — sample numpuk di sedikit segmen waktu, "
        "consistency tidak bisa diuji.",
        "",
        "Per-quarter sign label:",
        "- `POSITIVE` / `NEGATIVE` / `FLAT` — ≥"
        f"{MIN_QUARTER_TRADES} trades, return positive/negative/exactly-zero",
        f"- `SPARSE` — 1..<{MIN_QUARTER_TRADES} trades (data ada tapi "
        "tidak reliable)",
        "- `NO_DATA` — 0 trades closed (dikecualikan dari sign tally)",
        "",
        "Quarter return uses **starting_capital** as denominator (apple-to-apple "
        "across quarters, NOT compounded). Goal: consistency check, not absolute "
        "return measurement.",
        "",
    ]
    out_path.write_text("\n".join(lines))


# ─── main ────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def _parse_thresholds(s: str) -> list[float]:
    vals = [float(x.strip()) for x in s.split(",") if x.strip()]
    if not vals:
        raise argparse.ArgumentTypeError("at least one threshold required")
    return vals


def run_sweep(start: datetime, end: datetime, label: str,
              thresholds: list[float], quarters_n: int,
              db_path: Path, cycle_hours: int, verbose: bool,
              slippage_bps: float = 0.0) -> dict:
    sweep_root = RESULTS_ROOT / f"sweep_{label}"
    if sweep_root.exists():
        shutil.rmtree(sweep_root)
    sweep_root.mkdir(parents=True)

    print(f"[sweep] label={label}")
    print(f"[sweep] window: {start.date()} → {end.date()} "
          f"({(end - start).days} days)")
    print(f"[sweep] thresholds: {thresholds}")
    print(f"[sweep] quarters: {quarters_n}")
    print(f"[sweep] out: {sweep_root}")

    runs: list[dict] = []
    for i, t in enumerate(thresholds, 1):
        print(f"\n[sweep] {i}/{len(thresholds)} threshold t≥{t:g} ...")
        run = run_one_threshold(
            threshold=t, start=start, end=end,
            label_prefix=label, db_path=db_path,
            sweep_root=sweep_root, quarters_n=quarters_n,
            cycle_hours=cycle_hours, verbose=verbose,
            slippage_bps=slippage_bps,
        )
        runs.append(run)
        cls = run["classification"]
        print(f"[sweep]   → {cls['verdict']} ({cls['sign_consistency']}) "
              f"closed={run['n_trades_closed']} "
              f"realized={run['realized_return_pct']:+.2f}% "
              f"elapsed={run['elapsed_sec']:.1f}s")

    sweep_summary = {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "window_days": (end - start).days,
        "quarters": quarters_n,
        "thresholds": thresholds,
        "min_quarter_trades": MIN_QUARTER_TRADES,
        "insufficient_total_threshold": INSUFFICIENT_TOTAL,
        "runs": runs,
    }
    (sweep_root / "summary.json").write_text(
        json.dumps(sweep_summary, indent=2, default=str)
    )
    write_comparison_md(sweep_summary, sweep_root / "comparison.md")
    print(f"\n[sweep] DONE — see {sweep_root / 'comparison.md'}")
    return sweep_summary


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Threshold sensitivity sweep (Step 7)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--label", required=True)
    p.add_argument("--thresholds", required=True, type=_parse_thresholds,
                   help="Comma-separated, e.g. \"2.66,4,6,8\"")
    p.add_argument("--quarters", type=int, default=4)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--cycle-hours", type=int, default=1)
    p.add_argument("--slippage-bps", type=float, default=0.0,
                   help="Slippage bps per-side (Step 17.A3). "
                        "Default 0.0 (= pre-17 behaviour). "
                        "Recommended 10.0 (≈ 20 bps round-trip Bybit perp).")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end <= start:
        print("ERROR: end <= start", file=sys.stderr)
        return 2

    run_sweep(
        start=start, end=end, label=args.label,
        thresholds=args.thresholds, quarters_n=args.quarters,
        db_path=Path(args.db), cycle_hours=args.cycle_hours,
        verbose=args.verbose,
        slippage_bps=args.slippage_bps,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
