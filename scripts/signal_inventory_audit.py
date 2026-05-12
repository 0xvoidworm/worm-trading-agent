#!/usr/bin/env python3
"""Signal Inventory Pre-Flight Audit — Step 9.

Pure audit: tidak modifikasi auto_advisor / executor / threshold / config.
Sebelum decision soal multi-detector fusion (Step 10+), ground-truth dulu:
  - Berapa banyak signals tiap event_type yang sudah masuk DB?
  - Apakah distribusi temporal/strength mereka complement REGIME_CHANGE,
    atau klastering di window yang sama (Q4)?
  - Apakah ada complementarity (hari unik tambahan) kalau di-fuse?
  - Apakah detector sehat (count tidak anomali rendah)?

Output: data/backtest_results/signal_inventory_<label>/
  - inventory.json   (full structured data, semua event types & cross-event)
  - report.md        (human-readable Section A-E)

Usage:
    python scripts/signal_inventory_audit.py --label step9_preflight
    python scripts/signal_inventory_audit.py --start 2025-11-14 \\
        --end 2026-04-26 --label step9_full
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402


DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"

# Event types yang relevan utk trading decision (excluded operational ones:
# RISK_KILLSWITCH, RISK_RESET, FEED_DEAD, CYCLE_ABNORMAL).
TRADING_EVENT_TYPES = ["REGIME_CHANGE", "FUNDING_EXTREME", "FUNDING_FLIP", "Z_SPIKE"]

# Histogram buckets — meaningful utk REGIME_CHANGE & Z_SPIKE (standardized t/z).
# FUNDING_* rate dalam % (typical 0.001..0.5) → akan jatuh di [0,2) bucket; itu
# expected. Section A flag scale-mismatch untuk reader awareness.
DEFAULT_BUCKETS = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0, math.inf]


# ─── payload parsers ────────────────────────────────────────────────────

_RE_REGIME_T = re.compile(r"\bt=(-?[\d.]+)")
_RE_ZSPIKE = re.compile(r"\bz=(-?[\d.]+)")
_RE_FUNDING_RATE = re.compile(r"\brate=(-?[\d.]+)%")
_RE_FUNDING_FLIP = re.compile(r"\bcurr=(-?[\d.]+)%")


def _parse_strength(event_type: str, payload: str | None) -> Optional[float]:
    """Extract magnitude/strength field per event_type. Returns abs value.

    REGIME_CHANGE → |t-stat| (unitless ~0..30)
    Z_SPIKE       → |z|      (unitless ~2..6)
    FUNDING_*     → |rate%|  (percent, ~0.02..0.5)
    """
    if not payload:
        return None
    if event_type == "REGIME_CHANGE":
        m = _RE_REGIME_T.search(payload)
    elif event_type == "Z_SPIKE":
        m = _RE_ZSPIKE.search(payload)
    elif event_type == "FUNDING_EXTREME":
        m = _RE_FUNDING_RATE.search(payload)
    elif event_type == "FUNDING_FLIP":
        m = _RE_FUNDING_FLIP.search(payload)
    else:
        return None
    if not m:
        return None
    try:
        return abs(float(m.group(1)))
    except ValueError:
        return None


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


# ─── per-type inventory ─────────────────────────────────────────────────

def _split_quarters(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """4 equal contiguous quarters dari [start, end). Konvensi sama dengan
    backtest_sweep.split_into_quarters: floor(seconds/4) per quarter, sisa
    masuk Q4."""
    span = (end - start).total_seconds()
    q_sec = span / 4.0
    bounds = [start + timedelta(seconds=i * q_sec) for i in range(4)] + [end]
    return [(bounds[i], bounds[i + 1]) for i in range(4)]


def inventory_one(
    storage: Storage, event_type: str, start: datetime, end: datetime,
    buckets: list[float],
) -> dict:
    rows = storage._conn.execute(
        "SELECT ts, symbol, payload FROM signal_events "
        "WHERE event_type=? AND ts >= ? AND ts < ? "
        "ORDER BY ts",
        [event_type, start, end],
    ).fetchall()

    total = len(rows)
    strengths: list[float] = []
    by_pair: dict[str, int] = {}
    by_day: dict[str, int] = {}
    timestamps: list[datetime] = []
    n_unparseable = 0

    for ts, symbol, payload in rows:
        s = _parse_strength(event_type, payload)
        if s is None:
            n_unparseable += 1
        else:
            strengths.append(s)
        sym = str(symbol or "<none>")
        by_pair[sym] = by_pair.get(sym, 0) + 1
        day_key = ts.strftime("%Y-%m-%d")
        by_day[day_key] = by_day.get(day_key, 0) + 1
        timestamps.append(ts)

    # Quarter buckets
    quarters = _split_quarters(start, end)
    by_quarter: list[dict] = []
    for i, (qs, qe) in enumerate(quarters, 1):
        c = sum(1 for t in timestamps if qs <= t < qe)
        by_quarter.append({
            "q": f"Q{i}",
            "start": qs.isoformat(),
            "end": qe.isoformat(),
            "count": c,
        })

    # Histogram (semi-finite buckets; last upper = +inf)
    sorted_s = sorted(strengths)
    hist: list[dict] = []
    for i in range(len(buckets) - 1):
        lo, hi = buckets[i], buckets[i + 1]
        if math.isinf(hi):
            c = sum(1 for v in sorted_s if v >= lo)
            label = f"[{lo:g},+inf)"
        else:
            c = sum(1 for v in sorted_s if lo <= v < hi)
            label = f"[{lo:g},{hi:g})"
        hist.append({
            "bucket": label, "lo": lo,
            "hi": None if math.isinf(hi) else hi,
            "count": c,
            "pct": (c / total * 100.0) if total else 0.0,
        })

    # Distribution stats
    if sorted_s:
        stats = {
            "n_with_strength": len(sorted_s),
            "n_unparseable": n_unparseable,
            "min": sorted_s[0],
            "p25": _percentile(sorted_s, 25),
            "median": _percentile(sorted_s, 50),
            "p75": _percentile(sorted_s, 75),
            "p95": _percentile(sorted_s, 95),
            "max": sorted_s[-1],
            "mean": sum(sorted_s) / len(sorted_s),
        }
    else:
        stats = {
            "n_with_strength": 0, "n_unparseable": n_unparseable,
            "min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0,
            "p95": 0.0, "max": 0.0, "mean": 0.0,
        }

    # Inter-arrival gap (hours) — rata-rata jam antar signal
    gaps_h: list[float] = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600.0
        gaps_h.append(delta)
    sorted_g = sorted(gaps_h)
    gap_stats = {
        "n_gaps": len(gaps_h),
        "mean_h": (sum(gaps_h) / len(gaps_h)) if gaps_h else None,
        "median_h": _percentile(sorted_g, 50) if sorted_g else None,
        "max_h": (max(gaps_h)) if gaps_h else None,
    }

    # Date range
    if timestamps:
        ts_min = min(timestamps).isoformat()
        ts_max = max(timestamps).isoformat()
    else:
        ts_min = ts_max = None

    return {
        "event_type": event_type,
        "total": total,
        "ts_min": ts_min,
        "ts_max": ts_max,
        "by_quarter": by_quarter,
        "by_pair": dict(sorted(by_pair.items(), key=lambda kv: -kv[1])),
        "n_active_days": len(by_day),
        "stats": stats,
        "histogram": hist,
        "gap_stats": gap_stats,
    }


# ─── cross-event analysis ───────────────────────────────────────────────

def cross_event_analysis(
    storage: Storage, start: datetime, end: datetime,
) -> dict:
    """(date, pair) tuples per event_type → complementarity untuk fusion."""
    rows = storage._conn.execute(
        "SELECT ts, symbol, event_type FROM signal_events "
        "WHERE event_type IN ('REGIME_CHANGE','FUNDING_EXTREME',"
        "'FUNDING_FLIP','Z_SPIKE') "
        "AND ts >= ? AND ts < ? ORDER BY ts",
        [start, end],
    ).fetchall()

    # Tuple (date, primary_symbol). Z_SPIKE pair = "A|B" → keep as-is supaya
    # kelihatan beda dari REGIME_CHANGE single-symbol.
    by_type_dp: dict[str, set] = {et: set() for et in TRADING_EVENT_TYPES}
    by_type_d: dict[str, set] = {et: set() for et in TRADING_EVENT_TYPES}  # date-only
    overlap_bars: dict[tuple, set] = {}  # (date, hour, symbol) → set(event_types)

    for ts, symbol, et in rows:
        d = ts.strftime("%Y-%m-%d")
        sym = str(symbol or "")
        by_type_dp[et].add((d, sym))
        by_type_d[et].add(d)
        bar_key = (d, ts.strftime("%H"), sym)
        overlap_bars.setdefault(bar_key, set()).add(et)

    # Complementarity: (date, pair) tuples
    regime_dp = by_type_dp["REGIME_CHANGE"]
    fusion_dp = set()
    for et in TRADING_EVENT_TYPES:
        fusion_dp |= by_type_dp[et]

    # Date-only complementarity
    regime_d = by_type_d["REGIME_CHANGE"]
    fusion_d = set()
    for et in TRADING_EVENT_TYPES:
        fusion_d |= by_type_d[et]

    extra_days_per_type: dict[str, dict] = {}
    for et in TRADING_EVENT_TYPES:
        if et == "REGIME_CHANGE":
            continue
        extra = by_type_d[et] - regime_d
        extra_days_per_type[et] = {
            "extra_days": sorted(extra),
            "n_extra_days": len(extra),
        }

    # Overlap analysis: same (date, hour, symbol) bar with >1 event_type
    multi_type_bars = {
        k: sorted(v) for k, v in overlap_bars.items() if len(v) > 1
    }

    return {
        "regime_only": {
            "n_date_pair_tuples": len(regime_dp),
            "n_unique_days": len(regime_d),
        },
        "fusion_all": {
            "n_date_pair_tuples": len(fusion_dp),
            "n_unique_days": len(fusion_d),
            "uplift_dp_pct": (
                (len(fusion_dp) / len(regime_dp) - 1.0) * 100.0
                if regime_dp else None
            ),
            "uplift_days_pct": (
                (len(fusion_d) / len(regime_d) - 1.0) * 100.0
                if regime_d else None
            ),
        },
        "extra_days_per_type": extra_days_per_type,
        "n_overlap_bars": len(multi_type_bars),
        "overlap_bars_sample": [
            {"date": k[0], "hour": k[1], "symbol": k[2], "event_types": v}
            for k, v in list(multi_type_bars.items())[:20]
        ],
    }


# ─── verdict ────────────────────────────────────────────────────────────

def verdict(inventory: dict, cross: dict) -> dict:
    """Section D — fusion-go / detector-debug / wait."""
    issues: list[str] = []
    notes: list[str] = []

    rc = next(i for i in inventory["by_event_type"] if i["event_type"] == "REGIME_CHANGE")
    rc_total = rc["total"]

    for inv in inventory["by_event_type"]:
        et = inv["event_type"]
        if inv["total"] == 0:
            issues.append(f"DETECTOR_SILENT: {et} count=0 — detector may be misconfigured or unwired")
        elif inv["total"] < 10 and et != "REGIME_CHANGE":
            notes.append(f"LOW_SUPPLY: {et} only {inv['total']} signals — fusion contribution marginal")

    fusion_dp = cross["fusion_all"]["n_date_pair_tuples"]
    regime_dp = cross["regime_only"]["n_date_pair_tuples"]
    uplift_dp = cross["fusion_all"]["uplift_dp_pct"] or 0.0
    uplift_days = cross["fusion_all"]["uplift_days_pct"] or 0.0

    # Decision tree
    if any(i.startswith("DETECTOR_SILENT") for i in issues):
        decision = "DETECTOR_DEBUG"
        reason = "≥1 detector emit 0 signals — fix wiring/config sebelum fusion"
    elif uplift_dp >= 100:
        decision = "FUSION_GO"
        reason = f"fusion uplifts (date,pair) tuples by {uplift_dp:.0f}% (≥2x density) — worth integrating"
    elif uplift_days >= 25 and rc_total > 0:
        decision = "FUSION_GO"
        reason = (
            f"fusion adds {uplift_days:.0f}% unique days vs REGIME_CHANGE alone — "
            "improves temporal coverage even without 2x density"
        )
    elif rc_total < 30:
        decision = "WAIT"
        reason = (
            f"REGIME_CHANGE total {rc_total} < 30 (CLT threshold) — wait for daemon "
            "accumulation before any fusion decision"
        )
    else:
        decision = "FUSION_INCONCLUSIVE"
        reason = (
            f"fusion uplift modest (dp={uplift_dp:+.0f}%, days={uplift_days:+.0f}%) "
            "— fusion plumbing OK but won't move the needle alone"
        )

    return {
        "decision": decision,
        "reason": reason,
        "issues": issues,
        "notes": notes,
        "metrics": {
            "regime_change_total": rc_total,
            "fusion_dp_uplift_pct": uplift_dp,
            "fusion_days_uplift_pct": uplift_days,
            "regime_dp": regime_dp,
            "fusion_dp": fusion_dp,
        },
    }


# ─── runner ────────────────────────────────────────────────────────────

def run_audit(
    db_path: Path, start: datetime, end: datetime, buckets: list[float],
) -> dict:
    src = Storage(db_path=db_path, read_only=True)
    try:
        per_type = [
            inventory_one(src, et, start, end, buckets)
            for et in TRADING_EVENT_TYPES
        ]
        cross = cross_event_analysis(src, start, end)
    finally:
        src.close()

    inv = {
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "n_days": (end - start).days,
        },
        "buckets": [b if not math.isinf(b) else None for b in buckets],
        "by_event_type": per_type,
        "cross_event": cross,
    }
    inv["verdict"] = verdict(inv, cross)
    return inv


# ─── report writer ──────────────────────────────────────────────────────

def write_report(audit: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "inventory.json").write_text(
        json.dumps(audit, indent=2, default=str)
    )

    w = audit["window"]
    lines: list[str] = [
        "# Signal Inventory Pre-Flight — Step 9",
        "",
        f"**Window:** {w['start']} → {w['end']} ({w['n_days']} days)  ",
        f"**Quarters:** 4 equal segments  ",
        f"**Event types audited:** {', '.join(TRADING_EVENT_TYPES)}  ",
        "_Operational events (RISK_*, FEED_DEAD, CYCLE_ABNORMAL) excluded — "
        "tidak relevan utk trading decision._",
        "",
        "## Section A — Inventory Table",
        "",
        "| Event Type | Total | Date Range | Active Days | Pairs | "
        "Median | p75 | p95 | Max | Mean Gap (h) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for inv in audit["by_event_type"]:
        s = inv["stats"]
        gap_mean = inv["gap_stats"]["mean_h"]
        gap_str = f"{gap_mean:.1f}" if gap_mean is not None else "n/a"
        rng = (
            f"{inv['ts_min'][:10]} → {inv['ts_max'][:10]}"
            if inv["ts_min"] else "—"
        )
        lines.append(
            f"| `{inv['event_type']}` | {inv['total']} | {rng} | "
            f"{inv['n_active_days']} | {len(inv['by_pair'])} | "
            f"{s['median']:.2f} | {s['p75']:.2f} | {s['p95']:.2f} | "
            f"{s['max']:.2f} | {gap_str} |"
        )
    lines += [
        "",
        "_Catatan unit strength:_",
        "- `REGIME_CHANGE` → |t-stat| (unitless, ~0..30)",
        "- `Z_SPIKE`       → |z|      (unitless, ~2..6 typical)",
        "- `FUNDING_*`     → |rate|   (percent, ~0.02..0.5%) — beda skala, "
        "histogram default-bucket akan crowd di [0,2).",
        "",
        "### Histogram per event_type (strength buckets)",
        "",
    ]
    for inv in audit["by_event_type"]:
        lines.append(f"**{inv['event_type']}** (n={inv['total']}):")
        lines.append("")
        lines.append("| Bucket | Count | Pct |")
        lines.append("|---|---|---|")
        for h in inv["histogram"]:
            lines.append(f"| `{h['bucket']}` | {h['count']} | {h['pct']:.1f}% |")
        lines.append("")

    lines += [
        "### Per-pair breakdown",
        "",
    ]
    for inv in audit["by_event_type"]:
        if not inv["by_pair"]:
            continue
        pairs_str = ", ".join(
            f"`{p}`={c}" for p, c in inv["by_pair"].items()
        )
        lines.append(f"- **{inv['event_type']}** ({inv['total']}): {pairs_str}")
    lines.append("")

    # Section B — Quarter heatmap
    lines += [
        "## Section B — Quarter Heatmap",
        "",
        "| Event Type | " + " | ".join(
            f"Q{i}" for i in range(1, 5)
        ) + " | Total |",
        "|---|---|---|---|---|---|",
    ]
    for inv in audit["by_event_type"]:
        cells = " | ".join(str(q["count"]) for q in inv["by_quarter"])
        lines.append(f"| `{inv['event_type']}` | {cells} | {inv['total']} |")
    lines.append("")
    lines.append("Quarter windows:")
    if audit["by_event_type"]:
        first = audit["by_event_type"][0]
        for q in first["by_quarter"]:
            lines.append(
                f"- {q['q']}: {q['start'][:10]} → {q['end'][:10]}"
            )
    lines.append("")

    # Section C — Complementarity
    cross = audit["cross_event"]
    lines += [
        "## Section C — Complementarity Score",
        "",
        f"REGIME_CHANGE alone: **{cross['regime_only']['n_date_pair_tuples']}** "
        f"unique (date, pair) tuples · "
        f"**{cross['regime_only']['n_unique_days']}** unique days",
        "",
        f"All event types fused: **{cross['fusion_all']['n_date_pair_tuples']}** "
        f"unique (date, pair) tuples · "
        f"**{cross['fusion_all']['n_unique_days']}** unique days",
        "",
    ]
    upd = cross["fusion_all"]["uplift_dp_pct"]
    upd_str = f"{upd:+.1f}%" if upd is not None else "n/a"
    upday = cross["fusion_all"]["uplift_days_pct"]
    upday_str = f"{upday:+.1f}%" if upday is not None else "n/a"
    lines += [
        f"**Fusion uplift (date, pair):** {upd_str}",
        f"**Fusion uplift (unique days):** {upday_str}",
        "",
        "### Extra days per non-REGIME type (vs REGIME_CHANGE coverage)",
        "",
    ]
    for et, info in cross["extra_days_per_type"].items():
        sample = ", ".join(info["extra_days"][:5])
        if len(info["extra_days"]) > 5:
            sample += f", … (+{len(info['extra_days']) - 5} more)"
        lines.append(
            f"- **{et}** adds {info['n_extra_days']} unique days: {sample or '—'}"
        )
    lines += [
        "",
        f"### Overlap bars (>1 event_type same hour+symbol): "
        f"**{cross['n_overlap_bars']}**",
        "",
    ]
    if cross["overlap_bars_sample"]:
        lines.append("Sample (max 20):")
        lines.append("")
        lines.append("| Date | Hour | Symbol | Event Types |")
        lines.append("|---|---|---|---|")
        for ob in cross["overlap_bars_sample"]:
            lines.append(
                f"| {ob['date']} | {ob['hour']} | {ob['symbol']} | "
                f"{', '.join(ob['event_types'])} |"
            )
        lines.append("")

    # Section D — Verdict
    v = audit["verdict"]
    lines += [
        "## Section D — Verdict",
        "",
        f"### Decision: **{v['decision']}**",
        "",
        f"_{v['reason']}_",
        "",
        "**Key metrics:**",
        f"- REGIME_CHANGE total: {v['metrics']['regime_change_total']}",
        f"- Fusion (date,pair) uplift: "
        f"{v['metrics']['fusion_dp_uplift_pct']:+.1f}% "
        f"({v['metrics']['regime_dp']} → {v['metrics']['fusion_dp']})",
        f"- Fusion unique-days uplift: "
        f"{v['metrics']['fusion_days_uplift_pct']:+.1f}%",
        "",
    ]
    if v["issues"]:
        lines.append("**Issues flagged:**")
        for i in v["issues"]:
            lines.append(f"- ⚠️  {i}")
        lines.append("")
    if v["notes"]:
        lines.append("**Notes:**")
        for n in v["notes"]:
            lines.append(f"- {n}")
        lines.append("")

    # Section E — Recommended Step 10 framing
    lines += [
        "## Section E — Recommended Step 10 Framing",
        "",
    ]
    if v["decision"] == "FUSION_GO":
        lines += [
            "→ **Step 10 = Multi-detector fusion in `auto_advisor.py`.**",
            "",
            "Plumbing tasks:",
            "1. Extend `_load_candidates()` to query all 4 event_types, not just REGIME_CHANGE.",
            "2. Define normalized `confidence` per type (e.g. t-stat / z / |rate*scale|).",
            "3. Dedup logic untuk overlap bars (same hour+symbol multi-type) — pilih event "
            "dengan strength tertinggi atau combine confidence.",
            "4. Tetap respect concurrent cap + per-pair cap dari Step 8.",
            "5. Re-run sweep `step10_fusion_97d` dan compare ke `step8_multitrade_97d`.",
        ]
    elif v["decision"] == "DETECTOR_DEBUG":
        lines += [
            "→ **Step 10 = Detector debug, BUKAN fusion.**",
            "",
            "Tasks:",
            "1. Untuk setiap detector silent: trace daemon log + run_detectors call path.",
            "2. Verify input data ada (funding rows / OHLCV bars / pair candidates di config).",
            "3. Re-run audit setelah fix.",
        ]
    elif v["decision"] == "WAIT":
        lines += [
            "→ **Step 10 = WAIT (data accumulation), atau densify upstream signal source.**",
            "",
            "Pilihan:",
            "1. Biarkan daemon accumulate 60-90+ days lagi sebelum decision fusion.",
            "2. Atau review detector thresholds — apakah `FUNDING_EXTREME=0.001` / `Z_SPIKE=2.0` "
            "/ regime classifier sensitivity kekencengan? (audit ini bukan tempatnya — fokus inventory.)",
        ]
    else:  # FUSION_INCONCLUSIVE
        lines += [
            "→ **Step 10 = Fusion plumbing OK to build, tapi expect modest impact.**",
            "",
            "Catatan: complementarity rendah berarti detector saling redundan. "
            "Fusion bisa build untuk completeness, tapi jangan expect 2x trade count. "
            "Pertimbangkan parallel: tambah pair coverage atau event_type baru.",
        ]
    lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines))


def _print_summary(audit: dict) -> None:
    print(f"\n[inventory] window: {audit['window']['start']} → "
          f"{audit['window']['end']} ({audit['window']['n_days']}d)")
    print("[inventory] per event_type:")
    for inv in audit["by_event_type"]:
        s = inv["stats"]
        print(f"  {inv['event_type']:18s}  "
              f"total={inv['total']:4d}  "
              f"active_days={inv['n_active_days']:3d}  "
              f"pairs={len(inv['by_pair']):2d}  "
              f"med_strength={s['median']:6.2f}  "
              f"p95={s['p95']:6.2f}")
    qrow = "  quarters: " + " | ".join(
        f"Q{i}=" + "/".join(
            str(inv["by_quarter"][i - 1]["count"])
            for inv in audit["by_event_type"]
        )
        for i in range(1, 5)
    )
    print(qrow)
    cross = audit["cross_event"]
    print(f"[inventory] regime-only (date,pair): "
          f"{cross['regime_only']['n_date_pair_tuples']} · "
          f"unique days: {cross['regime_only']['n_unique_days']}")
    print(f"[inventory] fusion (date,pair): "
          f"{cross['fusion_all']['n_date_pair_tuples']} · "
          f"unique days: {cross['fusion_all']['n_unique_days']}")
    upd = cross["fusion_all"]["uplift_dp_pct"]
    upday = cross["fusion_all"]["uplift_days_pct"]
    print(f"[inventory] fusion uplift: dp={upd:+.1f}%  days={upday:+.1f}%")
    print(f"[inventory] overlap bars (multi-type same hour+sym): "
          f"{cross['n_overlap_bars']}")
    v = audit["verdict"]
    print(f"\n[inventory] DECISION: {v['decision']}  → {v['reason']}")
    if v["issues"]:
        for i in v["issues"]:
            print(f"  ⚠️  {i}")


# ─── CLI ────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Signal Inventory Pre-Flight (Step 9)")
    p.add_argument("--start", help="YYYY-MM-DD (UTC). Default: 365d ago.")
    p.add_argument("--end", help="YYYY-MM-DD (UTC). Default: today.")
    p.add_argument("--label", required=True)
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args(argv)

    end = _parse_dt(args.end) if args.end else datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None,
    )
    start = _parse_dt(args.start) if args.start else end - timedelta(days=365)
    if end <= start:
        print("ERROR: end <= start", file=sys.stderr)
        return 2

    audit = run_audit(
        db_path=Path(args.db), start=start, end=end,
        buckets=DEFAULT_BUCKETS,
    )

    out_dir = RESULTS_ROOT / f"signal_inventory_{args.label}"
    write_report(audit, out_dir)
    _print_summary(audit)
    print(f"\n[inventory] artifacts in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
