#!/usr/bin/env python3
"""Signal distribution audit — Step 6.

Tujuan: ground-truth distribusi t-stat REGIME_CHANGE signals di window
historical → calibration baseline untuk threshold (BUKAN tuning target).

Output:
  - Histogram t-stat (configurable buckets)
  - Per-pair count
  - Per-direction (trending_up vs trending_down) ratio
  - Median, p25, p75, p95
  - Recommended thresholds (top-25%, top-10%, top-5% percentile cuts)
  - Frequency per day (sparsity check)

Saves to data/backtest_results/signal_audit_<label>/
  - distribution.json
  - histogram.md

Usage:
    python scripts/signal_distribution_audit.py \\
        --start 2026-02-26 --end 2026-04-25 --label step6_audit
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"
DEFAULT_BUCKETS = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]


# ─── parsers ─────────────────────────────────────────────────────────────

_T_RE = re.compile(r"\bt=(-?[\d.]+)")


def _parse_t(payload: str | None) -> Optional[float]:
    if not payload:
        return None
    m = _T_RE.search(payload)
    if not m:
        return None
    try:
        return abs(float(m.group(1)))
    except ValueError:
        return None


def _parse_direction(payload: str | None) -> Optional[str]:
    if not payload:
        return None
    if "to=trending_up" in payload:
        return "LONG"
    if "to=trending_down" in payload:
        return "SHORT"
    return None


# ─── audit ───────────────────────────────────────────────────────────────

def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interp percentile. `pct` in [0, 100]."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def audit_signals(db_path: Path, start: datetime, end: datetime,
                  signal_type: str, buckets: list[float]) -> dict:
    src = Storage(db_path=db_path, read_only=True)
    rows = src._conn.execute(
        "SELECT ts, symbol, payload FROM signal_events "
        "WHERE event_type=? AND ts >= ? AND ts <= ? "
        "ORDER BY ts",
        [signal_type, start, end],
    ).fetchall()
    src.close()

    total = len(rows)
    t_values: list[float] = []
    by_pair: dict[str, int] = {}
    by_dir: dict[str, int] = {"LONG": 0, "SHORT": 0, "UNKNOWN": 0}
    by_day: dict[str, int] = {}
    unparseable = 0

    for ts, symbol, payload in rows:
        t = _parse_t(payload)
        if t is None:
            unparseable += 1
        else:
            t_values.append(t)
        sym = str(symbol or "<none>")
        by_pair[sym] = by_pair.get(sym, 0) + 1
        d = _parse_direction(payload) or "UNKNOWN"
        by_dir[d] = by_dir.get(d, 0) + 1
        day_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        by_day[day_key] = by_day.get(day_key, 0) + 1

    # Histogram buckets
    hist: list[dict] = []
    for i in range(len(buckets) - 1):
        lo, hi = buckets[i], buckets[i + 1]
        c = sum(1 for v in t_values if lo <= v < hi)
        hist.append({"bucket": f"[{lo:g},{hi:g})", "lo": lo, "hi": hi,
                     "count": c, "pct": (c / total * 100.0) if total else 0.0})
    overflow = sum(1 for v in t_values if v >= buckets[-1])
    hist.append({"bucket": f"[{buckets[-1]:g},+inf)", "lo": buckets[-1], "hi": None,
                 "count": overflow,
                 "pct": (overflow / total * 100.0) if total else 0.0})

    # Stats
    sorted_t = sorted(t_values)
    stats = {
        "n_with_t": len(t_values),
        "n_unparseable": unparseable,
        "median": _percentile(sorted_t, 50),
        "p25": _percentile(sorted_t, 25),
        "p75": _percentile(sorted_t, 75),
        "p95": _percentile(sorted_t, 95),
        "min": sorted_t[0] if sorted_t else 0.0,
        "max": sorted_t[-1] if sorted_t else 0.0,
        "mean": (sum(sorted_t) / len(sorted_t)) if sorted_t else 0.0,
    }

    # Recommended threshold cuts (top-N% percentiles)
    recommended = {
        "top_25pct_cut": _percentile(sorted_t, 75),
        "top_10pct_cut": _percentile(sorted_t, 90),
        "top_5pct_cut": _percentile(sorted_t, 95),
    }

    # Sparsity
    n_days = (end - start).days + 1
    n_days_with_signal = len(by_day)
    avg_per_day = total / n_days if n_days > 0 else 0.0

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat(),
                    "n_days": n_days},
        "signal_type": signal_type,
        "buckets": buckets,
        "total_signals": total,
        "histogram": hist,
        "stats": stats,
        "recommended_thresholds": recommended,
        "by_pair": dict(sorted(by_pair.items(), key=lambda kv: -kv[1])),
        "by_direction": by_dir,
        "by_day": by_day,
        "sparsity": {
            "n_days_with_signal": n_days_with_signal,
            "n_days_total": n_days,
            "pct_active_days": (n_days_with_signal / n_days * 100.0)
                                if n_days > 0 else 0.0,
            "avg_signals_per_day": avg_per_day,
        },
    }


# ─── output ──────────────────────────────────────────────────────────────

def write_outputs(audit: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "distribution.json").write_text(
        json.dumps(audit, indent=2, default=str)
    )

    lines = [
        f"# Signal Distribution Audit — `{audit['signal_type']}`",
        "",
        f"**Window:** {audit['window']['start']} → {audit['window']['end']} "
        f"({audit['window']['n_days']} days)  ",
        f"**Total signals:** {audit['total_signals']}  ",
        f"**Parseable t-stat:** {audit['stats']['n_with_t']} "
        f"(unparseable: {audit['stats']['n_unparseable']})  ",
        "",
        "## t-stat Histogram",
        "",
        "| Bucket | Count | Pct |",
        "|---|---|---|",
    ]
    for h in audit["histogram"]:
        lines.append(f"| `{h['bucket']}` | {h['count']} | {h['pct']:.1f}% |")

    s = audit["stats"]
    lines += [
        "",
        "## Distribution Stats",
        "",
        f"- Min: {s['min']:.2f} | Max: {s['max']:.2f} | Mean: {s['mean']:.2f}",
        f"- p25: {s['p25']:.2f} | **Median: {s['median']:.2f}** | "
        f"p75: {s['p75']:.2f} | p95: {s['p95']:.2f}",
        "",
        "## Recommended Thresholds (percentile cuts)",
        "",
        "_Threshold yang akan capture top-N% paling-kuat signals. "
        "Semata referensi untuk Step 7 calibration — JANGAN auto-tune._",
        "",
        "| Cut | t-stat threshold |",
        "|---|---|",
        f"| top-25% (p75) | {audit['recommended_thresholds']['top_25pct_cut']:.2f} |",
        f"| top-10% (p90) | {audit['recommended_thresholds']['top_10pct_cut']:.2f} |",
        f"| top-5% (p95) | {audit['recommended_thresholds']['top_5pct_cut']:.2f} |",
        "",
        "## Per-Pair Breakdown",
        "",
        "| Pair | Count |",
        "|---|---|",
    ]
    for sym, c in audit["by_pair"].items():
        lines.append(f"| `{sym}` | {c} |")

    d = audit["by_direction"]
    lines += [
        "",
        "## Per-Direction",
        "",
        f"- LONG (trending_up):  {d.get('LONG', 0)}",
        f"- SHORT (trending_down): {d.get('SHORT', 0)}",
        f"- UNKNOWN:               {d.get('UNKNOWN', 0)}",
        "",
        "## Sparsity",
        "",
        f"- Active days (≥1 signal): "
        f"{audit['sparsity']['n_days_with_signal']} / "
        f"{audit['sparsity']['n_days_total']} "
        f"({audit['sparsity']['pct_active_days']:.1f}%)",
        f"- Avg signals/day: {audit['sparsity']['avg_signals_per_day']:.2f}",
        "",
    ]
    (out_dir / "histogram.md").write_text("\n".join(lines))


def _print_summary(audit: dict) -> None:
    s = audit["stats"]
    print(f"\n[audit] signal_type={audit['signal_type']} "
          f"window={audit['window']['start']} → {audit['window']['end']} "
          f"({audit['window']['n_days']}d)")
    print(f"[audit] total signals: {audit['total_signals']}  "
          f"parseable t-stat: {s['n_with_t']}")
    print(f"[audit] sparsity: {audit['sparsity']['avg_signals_per_day']:.2f}/day · "
          f"{audit['sparsity']['n_days_with_signal']}/"
          f"{audit['sparsity']['n_days_total']} active days")
    print(f"[audit] t-stat: min={s['min']:.2f} p25={s['p25']:.2f} "
          f"median={s['median']:.2f} p75={s['p75']:.2f} p95={s['p95']:.2f} "
          f"max={s['max']:.2f}")
    print("[audit] histogram:")
    for h in audit["histogram"]:
        bar = "█" * int(h["pct"] / 2)
        print(f"  {h['bucket']:>14} | {h['count']:5d}  {h['pct']:5.1f}%  {bar}")
    rt = audit["recommended_thresholds"]
    print(f"[audit] recommended cuts: top25%={rt['top_25pct_cut']:.2f} "
          f"top10%={rt['top_10pct_cut']:.2f} top5%={rt['top_5pct_cut']:.2f}")
    print(f"[audit] per-pair: {audit['by_pair']}")
    print(f"[audit] per-direction: {audit['by_direction']}")


# ─── CLI ─────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def _parse_buckets(s: str) -> list[float]:
    vals = [float(x.strip()) for x in s.split(",") if x.strip()]
    if len(vals) < 2:
        raise argparse.ArgumentTypeError("buckets need ≥2 boundaries")
    if vals != sorted(vals):
        raise argparse.ArgumentTypeError("buckets must be ascending")
    return vals


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Signal distribution audit (Step 6)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--label", required=True)
    p.add_argument("--signal-type", default="REGIME_CHANGE")
    p.add_argument("--buckets", default=",".join(str(b) for b in DEFAULT_BUCKETS),
                   type=_parse_buckets)
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args(argv)

    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    if end < start:
        print("ERROR: end < start", file=sys.stderr)
        return 2

    audit = audit_signals(
        db_path=Path(args.db),
        start=start, end=end,
        signal_type=args.signal_type,
        buckets=args.buckets,
    )

    out_dir = RESULTS_ROOT / f"signal_audit_{args.label}"
    write_outputs(audit, out_dir)
    _print_summary(audit)
    print(f"\n[audit] artifacts in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
