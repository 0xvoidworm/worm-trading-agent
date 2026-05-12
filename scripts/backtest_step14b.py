#!/usr/bin/env python3
"""Step 14B — FUNDING_FLIP-only paper backtest sweep with hold variants.

Bridge Step 13B FUNDING_FLIP D3 edge (n=28 dom band, hit 0.71/0.68/0.61
@ h=1/4/24) into actual P&L via full production pipeline (sizing + exits +
fees + killswitch + paper broker).

Variants tested (3, no cherry-pick — all reported as-is):
  V1 — existing exits (default cfg: SL=5%, TP=5%, max_hold=72h, regime_flip_t=6.0)
  V2 — max_hold=24h (regime_flip enabled)
  V3 — max_hold=72h, regime_flip DISABLED (threshold=999.0 effectively off)

For each variant:
  - Run backtest on FULL window (365d default).
  - Split into N quarters (default 4), compute per-quarter realized return.
  - Apply Step 7 classifier (STABLE_POSITIVE / STABLE_NEGATIVE / INCONSISTENT
    / INSUFFICIENT_SAMPLE / INSUFFICIENT_SAMPLE_DISTRIBUTION).
  - Compute BnH BTC baseline + alpha (strategy% - bnh%).

Output: per-variant detail + comparison table at
    data/backtest_results/<label>/{summary.json, comparison.md, per_variant/...}

Acceptance discipline (per Step 14B spec):
  - Don't cherry-pick "best" variant — report all 3 verdicts honestly.
  - Don't claim STABLE_POSITIVE if n<30 — classifier enforces INSUFFICIENT_SAMPLE.
  - Don't change filter mid-run — band fixed across V1/V2/V3.

Usage:
    python scripts/backtest_step14b.py \\
        --start 2025-04-26 --end 2026-04-25 \\
        --label step14b_funding_flip_365d
"""
from __future__ import annotations

import argparse
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
from scripts.backtest_sweep import (  # noqa: E402
    INSUFFICIENT_TOTAL,
    MIN_QUARTER_TRADES,
    classify,
    per_quarter_stats,
    split_into_quarters,
    _read_closed_trades,
)


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"


# ─── variant definitions ────────────────────────────────────────────────

# Each variant defines a cfg_overrides dict deep-merged into the loaded
# pairs.yaml. detector_whitelist is COMMON to all 3 — only exits differ.
# This enforces "don't change filter mid-run" — band/whitelist identical.
COMMON_OVERRIDES = {
    "advisor": {
        "detector_whitelist": ["FUNDING_FLIP"],
        "funding_flip": {
            "band_min": 0.0001,   # 0.01% per Step 13B D3 dominant band
            "band_max": 0.0003,   # 0.03% (exclusive)
        },
    },
}

VARIANTS = [
    {
        "name": "V1",
        "description": "Existing exits (SL=5% TP=5% max_hold=72h regime_flip_t=6.0)",
        "exits_overrides": {},   # use yaml defaults
    },
    {
        "name": "V2",
        "description": "max_hold=24h (regime_flip enabled t=6.0)",
        "exits_overrides": {"max_hold_hours": 24},
    },
    {
        "name": "V3",
        "description": "max_hold=72h, regime_flip DISABLED (t=999 ≈ off)",
        "exits_overrides": {"regime_flip_t_threshold": 999.0},
    },
]


def _build_overrides(variant: dict) -> dict:
    overrides = {**COMMON_OVERRIDES}
    if variant["exits_overrides"]:
        overrides = {**overrides, "exits": variant["exits_overrides"]}
    return overrides


# ─── single-variant runner ──────────────────────────────────────────────

def run_one_variant(variant: dict, start: datetime, end: datetime,
                    label_prefix: str, db_path: Path,
                    sweep_root: Path, quarters_n: int,
                    cycle_hours: int, verbose: bool) -> dict:
    sub_label = f"{label_prefix}_{variant['name']}"
    sub_dir = sweep_root / "per_variant" / sub_label
    sub_dir.parent.mkdir(parents=True, exist_ok=True)

    orig_root = bt.RESULTS_ROOT
    bt.RESULTS_ROOT = sub_dir.parent
    try:
        t0 = time.time()
        summary = bt.run_backtest(
            start=start, end=end, label=sub_label,
            db_path=db_path, cycle_hours=cycle_hours,
            verbose=verbose,
            cfg_overrides=_build_overrides(variant),
        )
        elapsed = time.time() - t0
    finally:
        bt.RESULTS_ROOT = orig_root

    closed = _read_closed_trades(sub_dir / "trades.csv")
    quarters = split_into_quarters(start, end, quarters_n)
    per_q = per_quarter_stats(closed, quarters, summary["starting_capital"])
    cls = classify(per_q, summary["n_trades_closed"])

    bnh_ret = summary["buy_hold_btc"]["return_pct"]
    realized = summary["realized_return_pct"]
    mtm = summary["mtm_return_pct"]

    return {
        "variant": variant["name"],
        "description": variant["description"],
        "label": sub_label,
        "elapsed_sec": elapsed,
        "n_trades_total": summary["n_trades_total"],
        "n_trades_closed": summary["n_trades_closed"],
        "n_trades_open_at_end": summary["n_trades_open_at_end"],
        "realized_return_pct": realized,
        "mtm_return_pct": mtm,
        "sharpe_annualized": summary["strategy"]["sharpe_annualized"],
        "max_drawdown_pct": summary["strategy"]["max_drawdown_pct"],
        "bnh_btc_return_pct": bnh_ret,
        "alpha_realized_vs_bnh": realized - bnh_ret,
        "alpha_mtm_vs_bnh": mtm - bnh_ret,
        "edge_verdict_backtest": summary["edge_verdict"],
        "per_quarter": per_q,
        "classification": cls,
        "cycles_health_breakdown": summary["cycles_health_breakdown"],
        "exits_overrides": variant["exits_overrides"],
    }


# ─── output ─────────────────────────────────────────────────────────────

def write_comparison_md(sweep: dict, out_path: Path) -> None:
    rs = sweep["runs"]
    nq = sweep["quarters"]
    lines = [
        f"# Step 14B — FUNDING_FLIP-Only Paper Backtest — `{sweep['label']}`",
        "",
        f"**Window:** {sweep['start']} → {sweep['end']} "
        f"({sweep['window_days']} days)  ",
        f"**Quarters:** {nq} non-overlapping segments  ",
        f"**Variants tested:** {len(rs)} (V1, V2, V3 — no cherry-pick)  ",
        f"**Detector whitelist:** `FUNDING_FLIP` only  ",
        f"**Dominant band:** |curr| ∈ [0.01%, 0.03%) — Step 13B D3 evidence  ",
        f"**Min trades/quarter for STABLE verdict:** {MIN_QUARTER_TRADES}  ",
        f"**Min total closed for non-INSUFFICIENT verdict:** {INSUFFICIENT_TOTAL}  ",
        "",
        "## Variant Comparison",
        "",
        "| Variant | Closed | Open@End | Realized% | MTM% | BnH BTC% | "
        "α realized | α MTM | Sharpe | MaxDD% | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rs:
        cls = r["classification"]
        lines.append(
            f"| **{r['variant']}** | {r['n_trades_closed']} | "
            f"{r['n_trades_open_at_end']} | "
            f"{r['realized_return_pct']:+.2f} | "
            f"{r['mtm_return_pct']:+.2f} | "
            f"{r['bnh_btc_return_pct']:+.2f} | "
            f"{r['alpha_realized_vs_bnh']:+.2f} | "
            f"{r['alpha_mtm_vs_bnh']:+.2f} | "
            f"{r['sharpe_annualized']:.2f} | "
            f"{r['max_drawdown_pct']:.2f} | "
            f"**{cls['verdict']}** |"
        )

    lines += ["", "## Per-Variant Detail", ""]
    for r in rs:
        cls = r["classification"]
        lines += [
            f"### {r['variant']} — {r['description']}",
            "",
            f"_Verdict: **{cls['verdict']}** — {cls['reason']}_  ",
            f"_quarters_with_data: {cls['quarters_with_data']}/{nq} · "
            f"sign_consistency: {cls['sign_consistency']}_  ",
            f"_exits_overrides: `{r['exits_overrides']}`_",
            "",
            "| Quarter | Window | Closed | Realized PnL | Return% | Sign |",
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
            f"trading_GAP={hb['trading_GAP']} trading_STALE={hb['trading_STALE']}",
            f"Sub-run elapsed: {r['elapsed_sec']:.1f}s",
            "",
        ]

    lines += [
        "## Verdict Heuristic",
        "",
        "Per-quarter sign label uses Step 7 taxonomy. Aggregate verdict "
        f"requires ≥3 quarters with data and ≥{MIN_QUARTER_TRADES} closed "
        "trades per quarter for STABLE classification.",
        "",
        f"- `STABLE_POSITIVE` — ≥3/4 quarter (data ≥{MIN_QUARTER_TRADES}) positive",
        f"- `STABLE_NEGATIVE` — ≥3/4 quarter (data ≥{MIN_QUARTER_TRADES}) negative",
        "- `INCONSISTENT` — mixed signs OR sparse quarter present",
        f"- `INSUFFICIENT_SAMPLE` — total closed < {INSUFFICIENT_TOTAL}",
        "- `INSUFFICIENT_SAMPLE_DISTRIBUTION` — <3 quarters punya trades",
        "",
        "Alpha vs BnH BTC: `alpha = strategy_return - bnh_return`. Positive "
        "alpha = strategy beat passive BTC hold; negative = strategy lost vs "
        "passive. BnH baseline same across variants (computed on raw OHLCV).",
        "",
        "## Caveat",
        "",
        "**No cherry-pick. No 'winner' announcement.** Step 14B = bridge from "
        "D3 metric edge to actual P&L. STABLE_POSITIVE on ≥1 variant is "
        "evidence FUNDING_FLIP-only path is viable; INSUFFICIENT_SAMPLE on all "
        "variants means signal supply too sparse even after dominant-band "
        "filter (n=28 historical → likely <20 closed trades per quarter "
        "across 4 quarters of 8 symbols).",
        "",
        "If all 3 variants INSUFFICIENT_SAMPLE: Step 14B inconclusive. "
        "Options for Step 14C/15: (a) widen band to capture more signals, "
        "(b) extend window beyond 365d, (c) abandon FUNDING_FLIP-only.",
        "",
    ]
    out_path.write_text("\n".join(lines))


# ─── main ───────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def run_sweep(start: datetime, end: datetime, label: str,
              quarters_n: int, db_path: Path, cycle_hours: int,
              verbose: bool) -> dict:
    sweep_root = RESULTS_ROOT / label
    if sweep_root.exists():
        shutil.rmtree(sweep_root)
    sweep_root.mkdir(parents=True)

    print(f"[step14b] label={label}")
    print(f"[step14b] window: {start.date()} → {end.date()} "
          f"({(end - start).days} days)")
    print(f"[step14b] variants: {[v['name'] for v in VARIANTS]}")
    print(f"[step14b] quarters: {quarters_n}")
    print(f"[step14b] out: {sweep_root}")

    runs: list[dict] = []
    for i, variant in enumerate(VARIANTS, 1):
        print(f"\n[step14b] {i}/{len(VARIANTS)} variant {variant['name']} — "
              f"{variant['description']}")
        run = run_one_variant(
            variant=variant, start=start, end=end,
            label_prefix=label, db_path=db_path,
            sweep_root=sweep_root, quarters_n=quarters_n,
            cycle_hours=cycle_hours, verbose=verbose,
        )
        runs.append(run)
        cls = run["classification"]
        print(f"[step14b]   → {cls['verdict']} ({cls['sign_consistency']}) "
              f"closed={run['n_trades_closed']} "
              f"realized={run['realized_return_pct']:+.2f}% "
              f"alpha_real={run['alpha_realized_vs_bnh']:+.2f}% "
              f"elapsed={run['elapsed_sec']:.1f}s")

    sweep_summary = {
        "label": label,
        "step": "14B",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "window_days": (end - start).days,
        "quarters": quarters_n,
        "common_overrides": COMMON_OVERRIDES,
        "variants": [{"name": v["name"], "description": v["description"],
                      "exits_overrides": v["exits_overrides"]}
                     for v in VARIANTS],
        "min_quarter_trades": MIN_QUARTER_TRADES,
        "insufficient_total_threshold": INSUFFICIENT_TOTAL,
        "runs": runs,
    }
    (sweep_root / "summary.json").write_text(
        json.dumps(sweep_summary, indent=2, default=str)
    )
    write_comparison_md(sweep_summary, sweep_root / "comparison.md")
    print(f"\n[step14b] DONE — see {sweep_root / 'comparison.md'}")
    return sweep_summary


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Step 14B FUNDING_FLIP-only sweep")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--label", required=True)
    p.add_argument("--quarters", type=int, default=4)
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--cycle-hours", type=int, default=1)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end <= start:
        print("ERROR: end <= start", file=sys.stderr)
        return 2

    run_sweep(
        start=start, end=end, label=args.label,
        quarters_n=args.quarters,
        db_path=Path(args.db), cycle_hours=args.cycle_hours,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
