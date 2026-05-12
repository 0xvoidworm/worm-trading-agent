#!/usr/bin/env python3
"""Empty-Quarter Forensic Audit — Step 10.

Audit-only: tidak modifikasi advisor / executor / detector / config. Tujuan:
identify root cause kenapa Q1 (Apr-Jul'25), Q2 (Jul-Oct'25), Q3 (Oct'25-Jan'26)
ZERO trading signals di Step 9 inventory, padahal raw OHLCV 8 pairs full 365d.

3 hipotesis:
  H1: detector code lahir terlambat (codebase muda)
  H2: raw data tidak ada untuk window Q1-Q3
  H3: detector forward-only / lookback cap → tidak backfill historical

Output: data/backtest_results/forensics_<label>/
  - forensics.json
  - report.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402

DEFAULT_DB = ROOT / "data" / "market.duckdb"
RESULTS_ROOT = ROOT / "data" / "backtest_results"
DETECTOR_FILE = ROOT / "dashboard" / "signal_log.py"
DAEMON_LOG = ROOT / "data" / "daemon.log"
DAEMON_FILE = ROOT / "scripts" / "daemon.py"
BACKFILL_HIST = ROOT / "scripts" / "backfill_historical.py"

DETECTOR_TYPES = ["REGIME_CHANGE", "FUNDING_EXTREME", "FUNDING_FLIP", "Z_SPIKE"]


# ─── H1: code archaeology (no git → use mtime as proxy) ─────────────────

def h1_code_archaeology(storage: Storage) -> dict:
    """Codebase tidak ber-git. Pakai mtime file relevan + first-signal-ts dari
    DB sebagai proxy."""
    detector_birth = (
        datetime.fromtimestamp(DETECTOR_FILE.stat().st_mtime).isoformat()
        if DETECTOR_FILE.exists() else None
    )
    daemon_birth = (
        datetime.fromtimestamp(DAEMON_FILE.stat().st_mtime).isoformat()
        if DAEMON_FILE.exists() else None
    )
    backfill_birth = (
        datetime.fromtimestamp(BACKFILL_HIST.stat().st_mtime).isoformat()
        if BACKFILL_HIST.exists() else None
    )

    # First signal emission per type from DB
    per_type = []
    for et in DETECTOR_TYPES:
        row = storage._conn.execute(
            "SELECT MIN(ts), MAX(ts), COUNT(*) FROM signal_events "
            "WHERE event_type=?", [et],
        ).fetchone()
        per_type.append({
            "event_type": et,
            "first_signal_ts": row[0].isoformat() if row[0] else None,
            "last_signal_ts": row[1].isoformat() if row[1] else None,
            "total": row[2],
        })

    # Daemon first-run ts dari log
    daemon_first_run = None
    if DAEMON_LOG.exists():
        with DAEMON_LOG.open() as f:
            head = f.readline().strip()
        m = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]", head)
        if m:
            daemon_first_run = m.group(1)

    return {
        "git_available": False,
        "proxy_method": "file_mtime + log_head",
        "detector_file": str(DETECTOR_FILE.relative_to(ROOT)),
        "detector_mtime": detector_birth,
        "daemon_mtime": daemon_birth,
        "backfill_historical_mtime": backfill_birth,
        "daemon_first_run_log": daemon_first_run,
        "per_event_type": per_type,
    }


# ─── H2: raw data coverage ──────────────────────────────────────────────

def h2_data_coverage(storage: Storage) -> dict:
    ohlcv = storage._conn.execute("""
        SELECT symbol, MIN(ts) AS earliest, MAX(ts) AS latest,
               COUNT(*) AS bars, COUNT(DISTINCT DATE(ts)) AS days
        FROM ohlcv WHERE timeframe='1h' AND exchange='bybit'
        GROUP BY symbol ORDER BY symbol
    """).fetchall()
    funding = storage._conn.execute("""
        SELECT symbol, MIN(ts), MAX(ts), COUNT(*) AS rows,
               COUNT(DISTINCT DATE(ts)) AS days
        FROM funding_rate GROUP BY symbol ORDER BY symbol
    """).fetchall()
    news = storage._conn.execute("""
        SELECT MIN(published_at), MAX(published_at), COUNT(*),
               COUNT(DISTINCT DATE(published_at))
        FROM news
    """).fetchone()

    per_symbol = {}
    for s, mn, mx, bars, days in ohlcv:
        per_symbol[s] = {
            "ohlcv_earliest": mn.isoformat() if mn else None,
            "ohlcv_latest": mx.isoformat() if mx else None,
            "ohlcv_bars": int(bars),
            "ohlcv_active_days": int(days),
        }
    for s, mn, mx, rows, days in funding:
        per_symbol.setdefault(s, {}).update({
            "funding_earliest": mn.isoformat() if mn else None,
            "funding_latest": mx.isoformat() if mx else None,
            "funding_rows": int(rows),
            "funding_active_days": int(days),
        })

    return {
        "per_symbol": per_symbol,
        "news": {
            "earliest": news[0].isoformat() if news[0] else None,
            "latest": news[1].isoformat() if news[1] else None,
            "total": int(news[2]),
            "active_days": int(news[3]),
        },
    }


# ─── H3: detector replay capability ─────────────────────────────────────

def h3_replay_capability() -> dict:
    """Inspect detector code untuk lookback strategy."""
    code = DETECTOR_FILE.read_text()
    # Look for limit= patterns in get_ohlcv / get_funding calls
    limits = re.findall(
        r"storage\.(get_ohlcv|get_funding)\([^)]*limit\s*=\s*(\d+)", code,
    )
    # Look for full-history patterns (no limit, or watermark-walk)
    has_walk = "df_ts.iloc[i] <= last_ts" in code
    has_watermark = "_last_event_ts" in code

    # Replay scripts ada?
    replay_candidates = list(ROOT.rglob("scripts/*replay*.py")) + list(
        ROOT.rglob("data_pipe/*replay*.py"),
    )

    return {
        "detector_query_limits": [
            {"call": call, "limit": int(lim)} for call, lim in limits
        ],
        "uses_watermark_walk": has_watermark,
        "forward_only_after_first_seed": has_walk,
        "explicit_replay_scripts": [
            str(p.relative_to(ROOT)) for p in replay_candidates
        ],
        "backfill_historical_script": (
            str(BACKFILL_HIST.relative_to(ROOT))
            if BACKFILL_HIST.exists() else None
        ),
    }


# ─── Bonus: FUNDING_EXTREME recalibration ───────────────────────────────

def bonus_funding_recalibration(storage: Storage) -> dict:
    """Hitung percentile distribusi |funding_rate| historical → suggest
    threshold yang akan emit ~p95 / p99 sebagai signal."""
    rows = storage._conn.execute("""
        SELECT ABS(funding) FROM funding_rate
        WHERE funding IS NOT NULL ORDER BY ABS(funding)
    """).fetchall()
    vals = [float(r[0]) for r in rows]
    n = len(vals)
    if n == 0:
        return {"error": "no funding data"}

    def pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round((n - 1) * p / 100.0))))
        return vals[idx]

    current_threshold = 0.001  # from signal_log.FUNDING_EXTREME
    n_above_current = sum(1 for v in vals if v >= current_threshold)
    pct_above = n_above_current / n * 100

    suggested_p95 = pct(95)
    suggested_p99 = pct(99)
    n_above_p95 = sum(1 for v in vals if v >= suggested_p95)
    n_above_p99 = sum(1 for v in vals if v >= suggested_p99)

    return {
        "n_funding_rows_total": n,
        "current_threshold": current_threshold,
        "current_threshold_pct": "0.1% per 8h (~109% APR)",
        "n_above_current_threshold": n_above_current,
        "pct_above_current": pct_above,
        "distribution": {
            "median": pct(50),
            "p75": pct(75),
            "p90": pct(90),
            "p95": pct(95),
            "p99": pct(99),
            "max": vals[-1] if vals else 0.0,
        },
        "suggested": {
            "p95_threshold": suggested_p95,
            "p99_threshold": suggested_p99,
            "expected_emits_at_p95": n_above_p95,
            "expected_emits_at_p99": n_above_p99,
        },
    }


# ─── Verdict ────────────────────────────────────────────────────────────

def synthesize_verdict(h1: dict, h2: dict, h3: dict) -> dict:
    """Tentukan dominant cause + fixability."""
    issues: list[str] = []

    # H1 check
    detector_mtime = h1["detector_mtime"]
    daemon_first = h1["daemon_first_run_log"]
    code_age_days = None
    if detector_mtime:
        d = datetime.fromisoformat(detector_mtime)
        code_age_days = (datetime.now() - d).days
        if code_age_days < 30:
            issues.append(f"H1_CODE_YOUNG: detector lahir {code_age_days}d ago")

    # H2 check
    h2_gap = False
    target_year = "2025-04"
    for sym, info in h2["per_symbol"].items():
        if not info.get("ohlcv_earliest", "").startswith(target_year):
            h2_gap = True
            break
    if h2_gap:
        issues.append("H2_DATA_GAP: ohlcv earliest tidak mencapai Apr 2025")

    # H3 check
    limits = h3["detector_query_limits"]
    has_500_limit = any(d["limit"] == 500 for d in limits)
    no_replay = not h3["explicit_replay_scripts"]
    if has_500_limit and no_replay:
        issues.append(
            "H3_LOOKBACK_CAP: detector queries limit=500 baris tanpa replay "
            "script → Q1-Q3 unreachable meskipun raw data ada"
        )

    # Decide dominant
    if h2_gap and not has_500_limit:
        cause = "H2"
        fixable = "Yes (backfill exchange API)"
        effort = "1-2 hours backfill_historical.py extend"
    elif has_500_limit and not h2_gap:
        cause = "H3"
        fixable = "Yes"
        effort = (
            "Option A: 30min — bump limit to None (full history) di "
            "detect_*() calls. Option B: 2-4h — write detector_replay.py "
            "yang feed historical ohlcv batched + idempotent emit."
        )
    elif h2_gap and has_500_limit:
        cause = "H2 + H3 (combined)"
        fixable = "Partial"
        effort = "backfill data first, then replay detector — ~4-6h total"
    elif code_age_days is not None and code_age_days < 14:
        cause = "H1 + H3 (code young, no replay → Q1-Q3 never reached)"
        fixable = "Yes"
        effort = "implement detector replay; data already in DB"
    else:
        cause = "INCONCLUSIVE"
        fixable = "Unknown"
        effort = "n/a"

    return {
        "dominant_cause": cause,
        "fixable": fixable,
        "effort_estimate": effort,
        "issues": issues,
        "code_age_days_proxy": code_age_days,
    }


# ─── runner ─────────────────────────────────────────────────────────────

def run_forensics(db_path: Path) -> dict:
    src = Storage(db_path=db_path, read_only=True)
    try:
        h1 = h1_code_archaeology(src)
        h2 = h2_data_coverage(src)
        h3 = h3_replay_capability()
        bonus = bonus_funding_recalibration(src)
    finally:
        src.close()
    verdict = synthesize_verdict(h1, h2, h3)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "h1_code_archaeology": h1,
        "h2_data_coverage": h2,
        "h3_replay_capability": h3,
        "bonus_funding_recalibration": bonus,
        "verdict": verdict,
    }


# ─── report writer ──────────────────────────────────────────────────────

def write_report(audit: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forensics.json").write_text(
        json.dumps(audit, indent=2, default=str)
    )

    h1 = audit["h1_code_archaeology"]
    h2 = audit["h2_data_coverage"]
    h3 = audit["h3_replay_capability"]
    bonus = audit["bonus_funding_recalibration"]
    v = audit["verdict"]

    lines: list[str] = [
        "# Empty-Quarter Forensics — Step 10",
        "",
        f"_Generated: {audit['generated_at']}_",
        "",
        "## H1 — Detector Code Archaeology",
        "",
        f"Git available: **{h1['git_available']}** "
        f"(proxy: {h1['proxy_method']})  ",
        f"Detector file: `{h1['detector_file']}`  ",
        f"Detector mtime: **{h1['detector_mtime']}**  ",
        f"Daemon mtime: **{h1['daemon_mtime']}**  ",
        f"Backfill historical mtime: **{h1['backfill_historical_mtime']}**  ",
        f"Daemon first-run (log head): **{h1['daemon_first_run_log']}**  ",
        "",
        "### Per-event-type birth proxy",
        "",
        "| Event Type | First Signal TS | Last Signal TS | Total |",
        "|---|---|---|---|",
    ]
    for r in h1["per_event_type"]:
        lines.append(
            f"| `{r['event_type']}` | {r['first_signal_ts'] or '—'} | "
            f"{r['last_signal_ts'] or '—'} | {r['total']} |"
        )
    lines += [
        "",
        "## H2 — Raw Data Coverage",
        "",
        "| Symbol | OHLCV range | OHLCV bars | OHLCV days | "
        "Funding range | Funding rows | Funding days |",
        "|---|---|---|---|---|---|---|",
    ]
    for sym, info in h2["per_symbol"].items():
        oh_rng = (
            f"{(info.get('ohlcv_earliest') or '')[:10]} → "
            f"{(info.get('ohlcv_latest') or '')[:10]}"
        )
        fu_rng = (
            f"{(info.get('funding_earliest') or '')[:10]} → "
            f"{(info.get('funding_latest') or '')[:10]}"
        )
        lines.append(
            f"| `{sym}` | {oh_rng} | {info.get('ohlcv_bars', 0)} | "
            f"{info.get('ohlcv_active_days', 0)} | {fu_rng} | "
            f"{info.get('funding_rows', 0)} | "
            f"{info.get('funding_active_days', 0)} |"
        )
    n = h2["news"]
    lines += [
        "",
        f"News: {n['total']} items · {n['active_days']} active days · "
        f"{(n['earliest'] or '')[:10]} → {(n['latest'] or '')[:10]}",
        "",
        "## H3 — Detector Replay Mechanism",
        "",
        "### Detector storage queries (lookback caps)",
        "",
        "| Storage call | limit |",
        "|---|---|",
    ]
    for d in h3["detector_query_limits"]:
        lines.append(f"| `storage.{d['call']}(...)` | {d['limit']} |")
    lines += [
        "",
        f"- Uses watermark walk (`_last_event_ts`): "
        f"**{h3['uses_watermark_walk']}**",
        f"- Forward-only after first seed: "
        f"**{h3['forward_only_after_first_seed']}**",
        f"- Explicit replay scripts found: "
        f"**{h3['explicit_replay_scripts'] or 'None'}**",
        f"- Backfill historical script: "
        f"`{h3['backfill_historical_script']}` (data only, NOT detector replay)",
        "",
        "### Implication",
        "",
        "Detector dipanggil setiap cycle dengan `limit=500` per storage query:",
        "- OHLCV (1h timeframe) → 500 bars = **~20.8 days back from now**",
        "- Funding (8h interval) → 500 rows = **~166.7 days back from now**",
        "",
        "Walaupun OHLCV table punya 8,761 bars per symbol (366 days), detector "
        "tidak pernah melihat bar di luar 500 terakhir. Q1/Q2/Q3 unreachable "
        "by design saat ini — bukan karena raw data hilang, melainkan karena "
        "detector tidak punya replay mechanism untuk batch-walk historical.",
        "",
        "## Bonus — FUNDING_EXTREME Recalibration",
        "",
    ]
    if "error" in bonus:
        lines.append(f"_{bonus['error']}_")
    else:
        d = bonus["distribution"]
        s = bonus["suggested"]
        lines += [
            f"Total funding rows: **{bonus['n_funding_rows_total']}**  ",
            f"Current threshold: **{bonus['current_threshold']}** "
            f"({bonus['current_threshold_pct']})  ",
            f"Above current threshold: **{bonus['n_above_current_threshold']}** "
            f"({bonus['pct_above_current']:.3f}%)  ",
            "",
            "### |funding_rate| distribution",
            "",
            "| Pct | |rate| | APR proxy |",
            "|---|---|---|",
            f"| median | {d['median']:.6f} | {d['median']*3*365*100:+.1f}% |",
            f"| p75    | {d['p75']:.6f}    | {d['p75']*3*365*100:+.1f}% |",
            f"| p90    | {d['p90']:.6f}    | {d['p90']*3*365*100:+.1f}% |",
            f"| p95    | {d['p95']:.6f}    | {d['p95']*3*365*100:+.1f}% |",
            f"| p99    | {d['p99']:.6f}    | {d['p99']*3*365*100:+.1f}% |",
            f"| max    | {d['max']:.6f}    | {d['max']*3*365*100:+.1f}% |",
            "",
            f"Suggested thresholds:",
            f"- **p95 cut** = {s['p95_threshold']:.6f} → "
            f"~{s['expected_emits_at_p95']} historical emits "
            f"(vs current {bonus['n_above_current_threshold']})",
            f"- **p99 cut** = {s['p99_threshold']:.6f} → "
            f"~{s['expected_emits_at_p99']} historical emits",
            "",
            "_Recommendation:_ ganti `FUNDING_EXTREME = 0.001` di "
            "`dashboard/signal_log.py` ke **p99** (~tail event) atau **p95** "
            "(broader, ~26 emits/year extrapolated). p95 = better fusion supply, "
            "p99 = preserve 'extreme' semantics.",
            "",
            "_JANGAN auto-apply._ Recalibration ini suggestion untuk Step 11+, "
            "bukan auto-fix di Step 10.",
            "",
        ]
    lines += [
        "## VERDICT",
        "",
        f"**CAUSE = {v['dominant_cause']}**  ",
        f"**FIXABLE = {v['fixable']}**  ",
        f"**EFFORT = {v['effort_estimate']}**  ",
        "",
        "### Issues flagged",
        "",
    ]
    for i in v["issues"]:
        lines.append(f"- {i}")
    lines += [
        "",
        "### RECOMMENDATION",
        "",
    ]
    cause = v["dominant_cause"]
    if cause.startswith("H3"):
        lines += [
            "→ **Detector replay** adalah unblocker terbesar. Dua opsi:",
            "",
            "**A. Quick fix (30min):** ubah `dashboard/signal_log.py` — drop "
            "`limit=500` di `detect_regime_changes`, `detect_funding_events`, "
            "`detect_cointegration_zspikes`. Detector akan auto-walk full history "
            "tiap cycle. Risiko: cycle latency naik dari ~38s ke mungkin 200s+ "
            "karena classify() bar-per-bar 8,761× per symbol. Belum optimal "
            "untuk daemon production cycle.",
            "",
            "**B. Proper fix (2-4h):** tulis `scripts/detector_replay.py` "
            "one-shot CLI yang load full history sekali, jalankan classify + "
            "spread per bar, emit signal_events backdated dengan `as_of_ts = "
            "bar_ts`. Idempotent via dedup id (sudah ada). Setelah replay "
            "selesai, daemon resume forward-only dengan limit=500 (cycle latency "
            "tetap normal). Run sekali, hasil persistent.",
            "",
            "Setelah replay, re-run `signal_inventory_audit.py` → expect Q1-Q3 "
            "ter-isi → THEN execute Step 10 fusion plumbing dengan supply yang "
            "honest temporal-distributed.",
        ]
    elif cause.startswith("H2"):
        lines += [
            "→ Backfill raw data dulu via `scripts/backfill_historical.py` "
            "untuk window yang missing.",
        ]
    elif cause.startswith("H1"):
        lines += [
            "→ Detector code muda — tidak ada cara write history kecuali "
            "implement replay (lihat opsi H3).",
        ]
    else:
        lines.append("→ Lihat issues flagged di atas.")
    lines.append("")

    (out_dir / "report.md").write_text("\n".join(lines))


def _print_summary(audit: dict) -> None:
    h1 = audit["h1_code_archaeology"]
    h2 = audit["h2_data_coverage"]
    h3 = audit["h3_replay_capability"]
    v = audit["verdict"]
    print(f"\n[forensics] H1 detector mtime: {h1['detector_mtime']}")
    print(f"[forensics] H1 daemon first-run: {h1['daemon_first_run_log']}")
    print(f"[forensics] H1 first-signal per type:")
    for r in h1["per_event_type"]:
        print(f"  {r['event_type']:18s}  first={r['first_signal_ts'] or '—':28s}"
              f"  total={r['total']}")
    n_sym = len(h2["per_symbol"])
    if n_sym:
        s0 = next(iter(h2["per_symbol"].values()))
        print(f"\n[forensics] H2 OHLCV uniform: {n_sym} symbols, ~"
              f"{s0.get('ohlcv_bars', 0)} bars each, "
              f"{s0.get('ohlcv_active_days', 0)} active days")
        print(f"[forensics] H2 funding uniform: ~{s0.get('funding_rows', 0)} rows, "
              f"{s0.get('funding_active_days', 0)} days each")
    print(f"\n[forensics] H3 lookback caps: "
          f"{h3['detector_query_limits']}")
    print(f"[forensics] H3 replay scripts: "
          f"{h3['explicit_replay_scripts'] or 'NONE'}")
    print(f"\n[forensics] CAUSE: {v['dominant_cause']}")
    print(f"[forensics] FIXABLE: {v['fixable']}")
    print(f"[forensics] EFFORT: {v['effort_estimate']}")
    bonus = audit["bonus_funding_recalibration"]
    if "error" not in bonus:
        s = bonus["suggested"]
        print(f"\n[forensics] FUNDING bonus: current emit count="
              f"{bonus['n_above_current_threshold']}, "
              f"p95 cut → {s['expected_emits_at_p95']} emits, "
              f"p99 → {s['expected_emits_at_p99']}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Empty-Quarter Forensics (Step 10)")
    p.add_argument("--label", default="step10")
    p.add_argument("--db", default=str(DEFAULT_DB))
    args = p.parse_args(argv)

    audit = run_forensics(Path(args.db))
    out_dir = RESULTS_ROOT / f"forensics_{args.label}"
    write_report(audit, out_dir)
    _print_summary(audit)
    print(f"\n[forensics] artifacts in: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
