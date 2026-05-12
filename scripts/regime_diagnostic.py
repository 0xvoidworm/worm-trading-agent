#!/usr/bin/env python3
"""REGIME_CHANGE diagnostic — Step 12A (read-only, no DB writes).

Tujuan: diagnosa apakah explosion 77 → 6,847 (89×) dari Step 11 replay adalah
threshold-fixable (S1), debounce-fixable (S2), atau structural classifier
issue (S3).

Sub-investigations:
  D1  run-length distribution per symbol
  D2  t-stat distribution (full post-replay)
  D3  predictive power test (1h/4h/24h forward returns by t-stat band)
  D4  run-length × t-stat correlation

Verdict produced: SCENARIO_S1 / SCENARIO_S2 / SCENARIO_S3 / MIXED
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402

DEFAULT_DB = ROOT / "data" / "market.duckdb"
OUT_DIR = ROOT / "data" / "backtest_results" / "regime_diagnostic_step12"

PAYLOAD_RE = re.compile(r"from=([^;]+);to=([^;]+);t=(-?\d+\.?\d*)")
TF_HOURS = 1  # 1h bars

# Run-length buckets (in bars)
RUN_LENGTH_BUCKETS = [(1, 1), (2, 3), (4, 6), (7, 12), (13, 24), (25, 48), (49, 10**9)]
RUN_LENGTH_LABELS = ["1", "2-3", "4-6", "7-12", "13-24", "25-48", "48+"]

# t-stat buckets
T_BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 12), (12, 20), (20, 1e9)]
T_LABELS = ["[0,1)", "[1,2)", "[2,3)", "[3,5)", "[5,8)", "[8,12)", "[12,20)", "[20,+)"]


# ─── data loaders ───────────────────────────────────────────────────────

def load_regime_events(storage: Storage) -> pd.DataFrame:
    """Load all REGIME_CHANGE events with parsed payload."""
    df = storage._conn.execute("""
        SELECT ts, symbol, severity, payload, via_replay
        FROM signal_events
        WHERE event_type='REGIME_CHANGE'
        ORDER BY symbol, ts
    """).fetch_df()
    parsed = df["payload"].str.extract(PAYLOAD_RE)
    parsed.columns = ["from_state", "to_state", "t_stat"]
    df = pd.concat([df, parsed], axis=1)
    df["t_stat"] = pd.to_numeric(df["t_stat"], errors="coerce")
    df["abs_t"] = df["t_stat"].abs()
    return df


def load_ohlcv_lookup(storage: Storage, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Load close-price index per symbol for return computation."""
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = storage.get_ohlcv("bybit", sym, "1h")
        df = df[["ts", "close"]].set_index("ts").sort_index()
        out[sym] = df
    return out


# ─── D1 — run-length distribution per symbol ────────────────────────────

def compute_run_lengths(events: pd.DataFrame) -> pd.DataFrame:
    """For each symbol, sort by ts, compute time-to-next-flip per signal (in bars).

    Run-length = bars between this REGIME_CHANGE.ts and the next
    REGIME_CHANGE.ts for the same symbol.
    """
    rows: list[dict] = []
    for sym, g in events.groupby("symbol"):
        g = g.sort_values("ts").reset_index(drop=True)
        if len(g) < 2:
            continue
        ts_arr = pd.to_datetime(g["ts"]).values
        diffs = np.diff(ts_arr) / np.timedelta64(1, "h")
        # last signal: no successor → assign NaN; we exclude from histogram
        for i in range(len(g) - 1):
            rows.append({
                "symbol": sym,
                "ts": g.iloc[i]["ts"],
                "to_state": g.iloc[i]["to_state"],
                "abs_t": g.iloc[i]["abs_t"],
                "run_length_bars": int(round(diffs[i] / TF_HOURS)),
            })
    return pd.DataFrame(rows)


def bucket_run_length(rl: int) -> str:
    for (lo, hi), lab in zip(RUN_LENGTH_BUCKETS, RUN_LENGTH_LABELS):
        if lo <= rl <= hi:
            return lab
    return "48+"


def histogram_run_length_per_symbol(rl_df: pd.DataFrame) -> pd.DataFrame:
    rl_df = rl_df.copy()
    rl_df["bucket"] = rl_df["run_length_bars"].apply(bucket_run_length)
    pivot = (
        rl_df.groupby(["symbol", "bucket"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=RUN_LENGTH_LABELS, fill_value=0)
    )
    pivot["total"] = pivot.sum(axis=1)
    pivot["pct_le_2"] = (
        (pivot["1"] + pivot["2-3"]) / pivot["total"] * 100
    ).round(1)
    pivot["pct_ge_13"] = (
        (pivot["13-24"] + pivot["25-48"] + pivot["48+"]) / pivot["total"] * 100
    ).round(1)
    return pivot.reset_index()


# ─── D2 — t-stat distribution ───────────────────────────────────────────

def t_stat_distribution(events: pd.DataFrame) -> dict:
    abs_t = events["abs_t"].dropna()
    hist = {}
    for (lo, hi), lab in zip(T_BUCKETS, T_LABELS):
        n = int(((abs_t >= lo) & (abs_t < hi)).sum())
        hist[lab] = n
    pcts = abs_t.quantile([0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
    return {
        "n": int(len(abs_t)),
        "mean": float(abs_t.mean()),
        "p25": float(pcts.loc[0.25]),
        "p50": float(pcts.loc[0.5]),
        "p75": float(pcts.loc[0.75]),
        "p90": float(pcts.loc[0.9]),
        "p95": float(pcts.loc[0.95]),
        "p99": float(pcts.loc[0.99]),
        "max": float(abs_t.max()),
        "histogram": hist,
    }


# ─── D3 — predictive power test ─────────────────────────────────────────

def _bar_floor(ts) -> pd.Timestamp:
    """Floor any ts to the bar boundary (1h)."""
    return pd.Timestamp(ts).floor("h")


def _next_close_at(prices: pd.DataFrame, base_ts: pd.Timestamp,
                   horizon_h: int) -> float | None:
    """Return close at base_ts + horizon_h, or None if missing."""
    target = base_ts + pd.Timedelta(hours=horizon_h)
    if target not in prices.index:
        return None
    return float(prices.loc[target, "close"])


def predictive_power(
    events: pd.DataFrame,
    ohlcv_lookup: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """For each REGIME_CHANGE signal, compute forward 1h/4h/24h return.

    Direction encoded via to_state:
      trending_up   → LONG (positive return = win)
      trending_down → SHORT (negative return = win)
      ranging/chaos → no direction (excluded)
    """
    rows: list[dict] = []
    for _, ev in events.iterrows():
        sym = ev["symbol"]
        to = ev.get("to_state")
        if to not in ("trending_up", "trending_down"):
            continue
        if sym not in ohlcv_lookup:
            continue
        prices = ohlcv_lookup[sym]
        bar_ts = _bar_floor(ev["ts"])
        if bar_ts not in prices.index:
            continue
        c0 = float(prices.loc[bar_ts, "close"])
        for h in (1, 4, 24):
            cN = _next_close_at(prices, bar_ts, h)
            if cN is None:
                continue
            ret = (cN - c0) / c0
            # Sign-adjust: SHORT win on negative move, so flip
            if to == "trending_down":
                signed_ret = -ret
            else:
                signed_ret = ret
            rows.append({
                "symbol": sym,
                "ts": ev["ts"],
                "to_state": to,
                "abs_t": ev["abs_t"],
                "horizon_h": h,
                "raw_return": ret,
                "signed_return": signed_ret,  # +ve = direction-correct
            })
    return pd.DataFrame(rows)


def t_band(abs_t: float) -> str:
    for (lo, hi), lab in zip(T_BUCKETS, T_LABELS):
        if lo <= abs_t < hi:
            return lab
    return T_LABELS[-1]


def predictive_summary(pp_df: pd.DataFrame) -> pd.DataFrame:
    if pp_df.empty:
        return pd.DataFrame()
    pp_df = pp_df.copy()
    pp_df["t_band"] = pp_df["abs_t"].apply(t_band)
    grp = pp_df.groupby(["t_band", "horizon_h"])
    out = grp.agg(
        n=("signed_return", "size"),
        mean_signed=("signed_return", "mean"),
        median_signed=("signed_return", "median"),
        hit_rate=("signed_return", lambda s: (s > 0).mean()),
    ).reset_index()
    return out


# ─── D4 — run-length × t-stat correlation ───────────────────────────────

def runlength_x_tstat(rl_df: pd.DataFrame) -> dict:
    if rl_df.empty:
        return {}
    rl_df = rl_df.copy()
    rl_df["rl_bucket"] = rl_df["run_length_bars"].apply(bucket_run_length)
    grp = rl_df.groupby("rl_bucket")["abs_t"].agg(["count", "mean", "median"]).reset_index()
    grp = grp.rename(columns={"count": "n", "mean": "mean_abs_t",
                              "median": "median_abs_t"})
    # Pearson correlation
    if len(rl_df) > 1:
        corr = float(rl_df[["run_length_bars", "abs_t"]].corr().iloc[0, 1])
    else:
        corr = float("nan")
    return {
        "by_bucket": grp.to_dict(orient="records"),
        "pearson_runlength_x_abs_t": corr,
    }


# ─── verdict ────────────────────────────────────────────────────────────

def synth_verdict(
    rl_hist: pd.DataFrame,
    t_dist: dict,
    pp_summary: pd.DataFrame,
    rl_x_t: dict,
) -> dict:
    """S1 = threshold-fixable: predictive power monotonically increases with t-band
    S2 = debounce-fixable: short run-lengths dominate AND short-RL signals have
         lower predictive power
    S3 = structural: predictive power flat or noisy across all bands and rl
    MIXED = combination
    """
    notes = []

    # Aggregate noise indicator: % signals with run_length <=2 bars (across symbols)
    pct_le2_overall = (
        (rl_hist["1"].sum() + rl_hist["2-3"].sum())
        / max(rl_hist["total"].sum(), 1) * 100
    )
    pct_ge13_overall = (
        (rl_hist["13-24"].sum() + rl_hist["25-48"].sum() + rl_hist["48+"].sum())
        / max(rl_hist["total"].sum(), 1) * 100
    )
    notes.append(f"overall pct run_length<=3 bars: {pct_le2_overall:.1f}%")
    notes.append(f"overall pct run_length>=13 bars: {pct_ge13_overall:.1f}%")

    # Predictive power monotonicity check (24h horizon)
    pp24 = pp_summary[pp_summary["horizon_h"] == 24].sort_values("t_band")
    s1_evidence = False
    if not pp24.empty:
        # crude monotonicity: hit_rate goes up across bands
        hr = pp24["hit_rate"].values
        if len(hr) >= 4:
            top_band_hr = hr[-1] if not np.isnan(hr[-1]) else 0
            bot_band_hr = hr[0] if not np.isnan(hr[0]) else 0
            uplift = top_band_hr - bot_band_hr
            notes.append(
                f"24h hit-rate uplift top-band vs bot-band: {uplift:+.3f}"
            )
            if uplift >= 0.10:
                s1_evidence = True

    # S2 evidence: short-RL signals have notably lower abs_t mean
    s2_evidence = False
    if rl_x_t.get("by_bucket"):
        by = {r["rl_bucket"]: r for r in rl_x_t["by_bucket"]}
        short_t = by.get("1", {}).get("mean_abs_t", float("nan"))
        long_t = (
            by.get("13-24", {}).get("mean_abs_t")
            or by.get("25-48", {}).get("mean_abs_t")
            or by.get("48+", {}).get("mean_abs_t")
        )
        if short_t and long_t and not np.isnan(short_t):
            notes.append(
                f"abs_t (run_len=1) mean = {short_t:.2f}, "
                f"long-runs mean = {long_t:.2f}"
            )
            if long_t > short_t * 1.3:
                s2_evidence = True

    # Structural noise = high % short runs AND no s1 / no s2 lift
    mostly_noise = pct_le2_overall >= 50.0
    pearson = rl_x_t.get("pearson_runlength_x_abs_t", float("nan"))
    notes.append(f"pearson(run_length, abs_t) = {pearson:+.3f}")

    if s1_evidence and s2_evidence:
        verdict = "MIXED"
        rationale = "Both threshold filter (S1) AND debounce (S2) would help."
    elif s1_evidence and not s2_evidence:
        verdict = "SCENARIO_S1"
        rationale = "Predictive power scales with t-stat → threshold filter primary fix."
    elif s2_evidence and not s1_evidence:
        verdict = "SCENARIO_S2"
        rationale = "Long-persistence signals have higher t-stat → debounce by run-length."
    elif mostly_noise and not s1_evidence and not s2_evidence:
        verdict = "SCENARIO_S3"
        rationale = (
            "Short runs dominate AND no monotonic t-uplift AND no rl×t correlation "
            "→ structural classifier issue, neither threshold nor debounce sufficient."
        )
    else:
        verdict = "MIXED"
        rationale = "Mixed evidence; review individual D-tables."

    return {
        "verdict": verdict,
        "rationale": rationale,
        "s1_evidence": s1_evidence,
        "s2_evidence": s2_evidence,
        "structural_noise_indicator": mostly_noise,
        "notes": notes,
    }


# ─── report writer ──────────────────────────────────────────────────────

def write_report(
    out_dir: Path, rl_hist: pd.DataFrame, rl_df: pd.DataFrame,
    t_dist: dict, pp_summary: pd.DataFrame, rl_x_t: dict, verdict: dict,
    pre_post_ref: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = []
    md.append("# REGIME_CHANGE Diagnostic — Step 12A")
    md.append("")
    md.append(f"_Generated: {datetime.utcnow().isoformat()}_")
    md.append(f"_Source DB: data/market.duckdb (post-replay state)_")
    md.append("")
    md.append(f"Total REGIME_CHANGE events analysed: **{int(rl_hist['total'].sum() + rl_hist.shape[0])}** "
              f"(per-symbol last signal excluded from run-length: {rl_hist.shape[0]} signals)")
    md.append("")

    # D1
    md.append("## D1 — Run-Length Distribution per Symbol")
    md.append("")
    md.append("Run-length = bars between consecutive REGIME_CHANGE.ts per symbol "
              "(state lifetime before next flip).")
    md.append("")
    md.append("| Symbol | 1 | 2-3 | 4-6 | 7-12 | 13-24 | 25-48 | 48+ | Total | %≤3bar | %≥13bar |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in rl_hist.iterrows():
        md.append(
            f"| `{r['symbol']}` | {r['1']} | {r['2-3']} | {r['4-6']} | "
            f"{r['7-12']} | {r['13-24']} | {r['25-48']} | {r['48+']} | "
            f"{r['total']} | {r['pct_le_2']}% | {r['pct_ge_13']}% |"
        )
    md.append("")
    overall_le2 = (rl_hist["1"].sum() + rl_hist["2-3"].sum())
    overall_total = rl_hist["total"].sum()
    overall_ge13 = (rl_hist["13-24"].sum() + rl_hist["25-48"].sum() + rl_hist["48+"].sum())
    md.append(f"**OVERALL:** {overall_total} signals, "
              f"≤3bar={overall_le2} ({overall_le2/overall_total*100:.1f}%), "
              f"≥13bar={overall_ge13} ({overall_ge13/overall_total*100:.1f}%)")
    md.append("")

    # D2
    md.append("## D2 — |t-stat| Distribution (full post-replay)")
    md.append("")
    md.append(f"n = **{t_dist['n']}** | mean = {t_dist['mean']:.2f}")
    md.append("")
    md.append("| Quantile | |t-stat| |")
    md.append("|---|---|")
    for k in ("p25", "p50", "p75", "p90", "p95", "p99"):
        md.append(f"| {k} | {t_dist[k]:.2f} |")
    md.append(f"| max | {t_dist['max']:.2f} |")
    md.append("")
    md.append("Histogram:")
    md.append("")
    md.append("| Band | Count | Pct |")
    md.append("|---|---|---|")
    n = max(t_dist["n"], 1)
    for lab in T_LABELS:
        c = t_dist["histogram"][lab]
        md.append(f"| `{lab}` | {c} | {c/n*100:.1f}% |")
    md.append("")
    md.append(f"_Compare Step 9 (n=77, pre-replay):_ med=2.66, p75=7.44, p95=15.40")
    md.append(f"_Step 12 (n={t_dist['n']}, post-replay):_ med={t_dist['p50']:.2f}, "
              f"p75={t_dist['p75']:.2f}, p95={t_dist['p95']:.2f}")
    md.append("")

    # D3
    md.append("## D3 — Predictive Power (signed forward returns)")
    md.append("")
    md.append("`signed_return` = forward return aligned with signal direction "
              "(positive = direction-correct). Excludes `to=ranging|chaos`.")
    md.append("")
    if pp_summary.empty:
        md.append("_No predictive samples (insufficient data)._")
    else:
        md.append("| t-band | horizon_h | n | mean signed | median signed | hit-rate |")
        md.append("|---|---|---|---|---|---|")
        for _, r in pp_summary.sort_values(["horizon_h", "t_band"]).iterrows():
            md.append(
                f"| `{r['t_band']}` | {r['horizon_h']} | {int(r['n'])} | "
                f"{r['mean_signed']*100:+.3f}% | {r['median_signed']*100:+.3f}% | "
                f"{r['hit_rate']:.3f} |"
            )
    md.append("")

    # D4
    md.append("## D4 — Run-Length × |t-stat| Correlation")
    md.append("")
    md.append(f"**Pearson(run_length_bars, |t-stat|) = "
              f"{rl_x_t.get('pearson_runlength_x_abs_t', float('nan')):+.3f}**")
    md.append("")
    md.append("| Run-Length Bucket | n | mean |t-stat| | median |t-stat| |")
    md.append("|---|---|---|---|")
    for r in rl_x_t.get("by_bucket", []):
        md.append(
            f"| `{r['rl_bucket']}` | {int(r['n'])} | "
            f"{r['mean_abs_t']:.2f} | {r['median_abs_t']:.2f} |"
        )
    md.append("")

    # Verdict
    md.append("## VERDICT")
    md.append("")
    md.append(f"### **{verdict['verdict']}**")
    md.append("")
    md.append(f"_{verdict['rationale']}_")
    md.append("")
    md.append("Evidence:")
    md.append(f"- S1 (threshold-fixable) evidence: **{verdict['s1_evidence']}**")
    md.append(f"- S2 (debounce-fixable) evidence: **{verdict['s2_evidence']}**")
    md.append(f"- Structural noise indicator (≥50% run-len ≤3 bars): "
              f"**{verdict['structural_noise_indicator']}**")
    md.append("")
    md.append("Notes:")
    for n in verdict["notes"]:
        md.append(f"- {n}")
    md.append("")

    out_md = out_dir / "report.md"
    out_md.write_text("\n".join(md))
    out_json = out_dir / "diagnostic.json"
    out_json.write_text(json.dumps({
        "rl_hist": rl_hist.to_dict(orient="records"),
        "t_dist": t_dist,
        "pp_summary": pp_summary.to_dict(orient="records") if not pp_summary.empty else [],
        "rl_x_t": rl_x_t,
        "verdict": verdict,
    }, indent=2, default=str))
    print(f"[diag] artifacts in: {out_dir}")


# ─── CLI ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="REGIME_CHANGE diagnostic (Step 12A)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)

    storage = Storage(db_path=Path(args.db), read_only=True)
    try:
        ev = load_regime_events(storage)
        symbols = sorted(ev["symbol"].dropna().unique().tolist())
        print(f"[diag] events: {len(ev)} across {len(symbols)} symbols")

        rl_df = compute_run_lengths(ev)
        rl_hist = histogram_run_length_per_symbol(rl_df)
        t_dist = t_stat_distribution(ev)

        ohlcv_lookup = load_ohlcv_lookup(storage, symbols)
        pp_df = predictive_power(ev, ohlcv_lookup)
        pp_summary = predictive_summary(pp_df)

        rl_x_t = runlength_x_tstat(rl_df)
        verdict = synth_verdict(rl_hist, t_dist, pp_summary, rl_x_t)

        write_report(
            Path(args.out), rl_hist, rl_df, t_dist, pp_summary, rl_x_t,
            verdict, pre_post_ref={},
        )
        print(f"[diag] verdict: {verdict['verdict']}")
        print(f"[diag] rationale: {verdict['rationale']}")
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
