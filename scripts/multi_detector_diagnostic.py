#!/usr/bin/env python3
"""Multi-detector predictive diagnostic — Step 13A + 13B (read-only).

13A: REGIME inversion test — re-run D3 dengan signal direction di-flip.
13B: predictive power untuk FUNDING_FLIP, FUNDING_EXTREME, Z_SPIKE.

Output:
  - data/backtest_results/multi_detector_diag_step13/report.md
  - data/backtest_results/multi_detector_diag_step13/diag.json

Verdict per detector:
  HAS_EDGE  — top-vs-bot band hit-rate uplift > +0.05 di setidaknya satu horizon
  INVERTED  — sign-flipped version has uplift > +0.05 (signal predictive but reversed)
  FLAT      — no uplift in either direction
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
OUT_DIR = ROOT / "data" / "backtest_results" / "multi_detector_diag_step13"

# t-stat / |z| bands
T_BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 12), (12, 20), (20, 1e9)]
T_LABELS = ["[0,1)", "[1,2)", "[2,3)", "[3,5)", "[5,8)", "[8,12)", "[12,20)", "[20,+)"]

# |z| (Z_SPIKE) bands — biasa 2..6
Z_BUCKETS = [(2.0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 1e9)]
Z_LABELS = ["[2.0,2.5)", "[2.5,3.0)", "[3.0,4.0)", "[4.0,6.0)", "[6.0,+)"]

# |funding_rate| bands (decimal — 0.0001 = 0.01%)
F_BUCKETS = [(0, 0.0001), (0.0001, 0.0003), (0.0003, 0.0005),
             (0.0005, 0.001), (0.001, 0.003), (0.003, 1)]
F_LABELS = ["[0,0.01%)", "[0.01,0.03%)", "[0.03,0.05%)",
            "[0.05,0.10%)", "[0.10,0.30%)", "[0.30%+)"]

PAYLOAD_REGIME = re.compile(r"from=([^;]+);to=([^;]+);t=(-?\d+\.?\d*)")
PAYLOAD_FUND_EXTREME = re.compile(r"rate=(-?\d+\.?\d*)%.*side=(\w+-\w+)")
PAYLOAD_FUND_FLIP = re.compile(r"prev=(-?\d+\.?\d*)%;curr=(-?\d+\.?\d*)%")
PAYLOAD_ZSPIKE = re.compile(
    r"pair=([^|]+)\|([^;]+);z=(-?\d+\.?\d*);"
    r"adf_p=(\d+\.?\d*);hl=(-?\d+\.?\d*);side=(\w+-\w+)"
)


# ─── data loaders ───────────────────────────────────────────────────────

def load_signals(storage: Storage, event_type: str) -> pd.DataFrame:
    df = storage._conn.execute(
        f"SELECT ts, symbol, payload FROM signal_events "
        f"WHERE event_type='{event_type}' ORDER BY symbol, ts"
    ).fetch_df()
    return df


def load_ohlcv_lookup(storage: Storage, symbols: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if "|" in sym:  # pair label, will be split downstream
            continue
        df = storage.get_ohlcv("bybit", sym, "1h")
        df = df[["ts", "close"]].set_index("ts").sort_index()
        out[sym] = df
    return out


# ─── direction & strength extraction per detector ───────────────────────

def _direction_regime(payload: str, invert: bool = False) -> tuple[str | None, float | None]:
    """Returns (direction, abs_strength) for REGIME_CHANGE."""
    m = PAYLOAD_REGIME.search(payload or "")
    if not m:
        return None, None
    to = m.group(2)
    t = float(m.group(3))
    if to == "trending_up":
        direction = "LONG"
    elif to == "trending_down":
        direction = "SHORT"
    else:  # ranging/chaos — no direction
        return None, abs(t)
    if invert:
        direction = "SHORT" if direction == "LONG" else "LONG"
    return direction, abs(t)


def _direction_funding_extreme(payload: str) -> tuple[str | None, float | None]:
    """side=long-pays (rate>0) → over-leveraged longs → expect MEAN REVERT DOWN → SHORT.
    side=short-pays (rate<0) → expect mean revert UP → LONG.
    Strength = |rate| in decimal.
    """
    m = PAYLOAD_FUND_EXTREME.search(payload or "")
    if not m:
        return None, None
    rate_pct = float(m.group(1))  # in percent (e.g., 0.1234)
    rate = rate_pct / 100.0  # back to decimal (0.001234)
    side = m.group(2)
    if side == "long-pays":
        direction = "SHORT"
    elif side == "short-pays":
        direction = "LONG"
    else:
        return None, abs(rate)
    return direction, abs(rate)


def _direction_funding_flip(payload: str) -> tuple[str | None, float | None]:
    """prev→curr sign flip. Direction by sign of curr:
    curr<0 (flipped negative) → bearish sentiment shift → SHORT.
    curr>0 (flipped positive) → bullish sentiment shift → LONG.
    Strength = |curr| in decimal.
    """
    m = PAYLOAD_FUND_FLIP.search(payload or "")
    if not m:
        return None, None
    curr_pct = float(m.group(2))
    curr = curr_pct / 100.0
    if curr > 0:
        direction = "LONG"
    elif curr < 0:
        direction = "SHORT"
    else:
        return None, abs(curr)
    return direction, abs(curr)


def _direction_zspike(payload: str) -> tuple[str | None, float | None, str | None, str | None]:
    """side=short-spread (z>0) → spread A-βB above mean → SHORT A, LONG B.
    side=long-spread (z<0) → LONG A, SHORT B.
    For the predictive test we look at A's signed return.
    Strength = |z|.
    Returns (direction_for_A, |z|, A, B).
    """
    m = PAYLOAD_ZSPIKE.search(payload or "")
    if not m:
        return None, None, None, None
    a, b = m.group(1), m.group(2)
    z = float(m.group(3))
    side = m.group(6)
    if side == "short-spread":
        direction_a = "SHORT"
    elif side == "long-spread":
        direction_a = "LONG"
    else:
        return None, abs(z), a, b
    return direction_a, abs(z), a, b


# ─── predictive computation ─────────────────────────────────────────────

def _bar_floor(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).floor("h")


def _signed_return(prices: pd.DataFrame, base_ts: pd.Timestamp,
                   horizon_h: int, direction: str) -> float | None:
    if base_ts not in prices.index:
        return None
    target = base_ts + pd.Timedelta(hours=horizon_h)
    if target not in prices.index:
        return None
    c0 = float(prices.loc[base_ts, "close"])
    cN = float(prices.loc[target, "close"])
    raw = (cN - c0) / c0
    return raw if direction == "LONG" else -raw


def _band(strength: float, buckets: list[tuple], labels: list[str]) -> str:
    for (lo, hi), lab in zip(buckets, labels):
        if lo <= strength < hi:
            return lab
    return labels[-1]


def predictive_table(
    signals: pd.DataFrame,
    direction_fn,
    ohlcv_lookup: dict[str, pd.DataFrame],
    buckets: list[tuple],
    labels: list[str],
    *,
    pair_split: bool = False,
) -> pd.DataFrame:
    """Compute (band, horizon) → n, mean_signed, hit_rate."""
    rows: list[dict] = []
    for _, ev in signals.iterrows():
        sym = ev["symbol"]
        if pair_split:
            res = direction_fn(ev["payload"])
            if res is None or res[0] is None:
                continue
            direction, strength, a, b = res
            target_sym = a  # use A's price for the predictive test
        else:
            direction, strength = direction_fn(ev["payload"])
            target_sym = sym
        if direction is None or strength is None:
            continue
        if target_sym not in ohlcv_lookup:
            continue
        prices = ohlcv_lookup[target_sym]
        bar_ts = _bar_floor(ev["ts"])
        if bar_ts not in prices.index:
            continue
        band = _band(strength, buckets, labels)
        for h in (1, 4, 24):
            sr = _signed_return(prices, bar_ts, h, direction)
            if sr is None:
                continue
            rows.append({
                "band": band,
                "horizon_h": h,
                "signed_return": sr,
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return (
        df.groupby(["band", "horizon_h"])
        .agg(n=("signed_return", "size"),
             mean_signed=("signed_return", "mean"),
             median_signed=("signed_return", "median"),
             hit_rate=("signed_return", lambda s: (s > 0).mean()))
        .reset_index()
    )


# ─── verdict ────────────────────────────────────────────────────────────

def top_vs_bot_uplift(table: pd.DataFrame, labels: list[str]) -> dict[int, float]:
    """For each horizon, hit_rate of highest-strength band vs lowest band.
    Uses only bands present in table.
    """
    out: dict[int, float] = {}
    for h in (1, 4, 24):
        sub = table[table["horizon_h"] == h]
        if sub.empty:
            continue
        present = sub["band"].tolist()
        # Pick the lowest and highest band that have ≥10 samples (avoid noise)
        ord_present = [b for b in labels if b in present]
        ord_present_with_n = [
            b for b in ord_present
            if int(sub[sub["band"] == b]["n"].iloc[0]) >= 10
        ]
        if len(ord_present_with_n) < 2:
            continue
        bot = ord_present_with_n[0]
        top = ord_present_with_n[-1]
        bot_hr = float(sub[sub["band"] == bot]["hit_rate"].iloc[0])
        top_hr = float(sub[sub["band"] == top]["hit_rate"].iloc[0])
        out[h] = top_hr - bot_hr
    return out


def overall_hit_rate(table: pd.DataFrame) -> dict[int, float]:
    out: dict[int, float] = {}
    for h in (1, 4, 24):
        sub = table[table["horizon_h"] == h]
        if sub.empty:
            continue
        # weighted mean
        w_sum = float((sub["hit_rate"] * sub["n"]).sum())
        n_sum = int(sub["n"].sum())
        if n_sum > 0:
            out[h] = w_sum / n_sum
    return out


def verdict_one(detector: str, fwd_table: pd.DataFrame, labels: list[str],
                inv_table: pd.DataFrame | None = None) -> dict:
    """HAS_EDGE / INVERTED / FLAT.
    HAS_EDGE: forward overall hit-rate > 0.55 di ≥1 horizon, OR uplift > +0.05.
    INVERTED: inverted overall hit-rate > 0.55 OR inverted uplift > +0.05.
    FLAT: neither.
    """
    fwd_uplift = top_vs_bot_uplift(fwd_table, labels)
    fwd_hit = overall_hit_rate(fwd_table)
    fwd_edge = (
        any(v > 0.55 for v in fwd_hit.values())
        or any(v > 0.05 for v in fwd_uplift.values())
    )
    inv_uplift = {}
    inv_hit = {}
    if inv_table is not None and not inv_table.empty:
        inv_uplift = top_vs_bot_uplift(inv_table, labels)
        inv_hit = overall_hit_rate(inv_table)
    inv_edge = (
        any(v > 0.55 for v in inv_hit.values())
        or any(v > 0.05 for v in inv_uplift.values())
    )

    if fwd_edge and not inv_edge:
        v = "HAS_EDGE"
        reason = "forward hit-rate or uplift exceeds threshold"
    elif inv_edge and not fwd_edge:
        v = "INVERTED"
        reason = "inverted hit-rate or uplift exceeds threshold (signal predictive but sign reversed)"
    elif fwd_edge and inv_edge:
        v = "AMBIGUOUS"
        reason = "both forward AND inverted show edge — unstable"
    else:
        v = "FLAT"
        reason = "neither forward nor inverted exceeds 0.55 hit-rate or +0.05 uplift"
    return {
        "detector": detector,
        "verdict": v,
        "reason": reason,
        "fwd_hit_rate": fwd_hit,
        "fwd_uplift": fwd_uplift,
        "inv_hit_rate": inv_hit,
        "inv_uplift": inv_uplift,
    }


# ─── report writer ──────────────────────────────────────────────────────

def fmt_table(df: pd.DataFrame, labels: list[str]) -> list[str]:
    lines = ["| Band | h=1 (n / hit / mean) | h=4 (n / hit / mean) | h=24 (n / hit / mean) |",
             "|---|---|---|---|"]
    for lab in labels:
        sub = df[df["band"] == lab]
        if sub.empty:
            lines.append(f"| `{lab}` | — | — | — |")
            continue
        cells = []
        for h in (1, 4, 24):
            row = sub[sub["horizon_h"] == h]
            if row.empty:
                cells.append("—")
            else:
                r = row.iloc[0]
                cells.append(f"{int(r['n'])} / {r['hit_rate']:.3f} / {r['mean_signed']*100:+.3f}%")
        lines.append(f"| `{lab}` | {cells[0]} | {cells[1]} | {cells[2]} |")
    return lines


def write_report(out_dir: Path, results: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = []
    md.append("# Multi-Detector Predictive Diagnostic — Step 13A + 13B")
    md.append("")
    md.append(f"_Generated: {datetime.utcnow().isoformat()}_")
    md.append("")
    md.append("Read-only. Predictive test: untuk tiap signal, signed forward return "
              "(direction-aligned) di 1h, 4h, 24h. Hit-rate = % return > 0 setelah "
              "sign-correction.")
    md.append("")

    # Section A — 13A REGIME inversion
    md.append("## Section A — 13A REGIME Inversion Test")
    md.append("")
    md.append("Original direction: `to=trending_up`→LONG, `to=trending_down`→SHORT.")
    md.append("Inverted: flip both.")
    md.append("")
    md.append("### Original direction")
    md.append("")
    md.extend(fmt_table(results["regime_fwd"], T_LABELS))
    md.append("")
    md.append("### Inverted direction")
    md.append("")
    md.extend(fmt_table(results["regime_inv"], T_LABELS))
    md.append("")

    rv = results["regime_verdict"]
    md.append("### 13A Verdict")
    md.append("")
    md.append(f"- Forward overall hit-rate: " +
              ", ".join(f"h={k}→{v:.3f}" for k, v in rv["fwd_hit_rate"].items()))
    md.append(f"- Forward top-vs-bot uplift: " +
              ", ".join(f"h={k}→{v:+.3f}" for k, v in rv["fwd_uplift"].items()))
    md.append(f"- Inverted overall hit-rate: " +
              ", ".join(f"h={k}→{v:.3f}" for k, v in rv["inv_hit_rate"].items()))
    md.append(f"- Inverted top-vs-bot uplift: " +
              ", ".join(f"h={k}→{v:+.3f}" for k, v in rv["inv_uplift"].items()))
    md.append("")
    if rv["verdict"] == "INVERTED":
        outcome = "**INVERSION_HELPS**"
    elif rv["verdict"] == "HAS_EDGE":
        outcome = "**INVERSION_HURTS** (forward already has edge)"
    elif rv["verdict"] == "AMBIGUOUS":
        outcome = "**AMBIGUOUS** (both sign-directions show edge — unstable)"
    else:
        outcome = "**INVERSION_NEUTRAL** (S3 confirmed: no edge in either direction)"
    md.append(f"→ {outcome}")
    md.append("")

    # Section B — 13B multi-detector
    md.append("## Section B — 13B Multi-Detector Predictive Power")
    md.append("")
    detectors_meta = [
        ("FUNDING_EXTREME", "funding_extreme", F_LABELS,
         "side=long-pays→SHORT, side=short-pays→LONG. Strength = |rate|."),
        ("FUNDING_FLIP", "funding_flip", F_LABELS,
         "curr>0→LONG (bullish flip), curr<0→SHORT (bearish flip). Strength = |curr|."),
        ("Z_SPIKE", "zspike", Z_LABELS,
         "side=short-spread→SHORT A, side=long-spread→LONG A. Strength = |z|. "
         "Predictive on first-symbol price."),
    ]
    for name, key, labels, descr in detectors_meta:
        md.append(f"### {name}")
        md.append("")
        md.append(f"_{descr}_")
        md.append("")
        md.extend(fmt_table(results[key], labels))
        md.append("")
        v = results[f"{key}_verdict"]
        md.append(f"- Overall hit-rate: " +
                  ", ".join(f"h={k}→{v_:.3f}" for k, v_ in v["fwd_hit_rate"].items()))
        md.append(f"- Top-vs-bot uplift: " +
                  ", ".join(f"h={k}→{v_:+.3f}" for k, v_ in v["fwd_uplift"].items()))
        md.append(f"- **Verdict: {v['verdict']}** ({v['reason']})")
        md.append("")

    md.append("## Section B Summary")
    md.append("")
    md.append("| Detector | Verdict | Best uplift | Best hit-rate |")
    md.append("|---|---|---|---|")
    summary_rows = [
        ("REGIME_CHANGE (forward)", results["regime_verdict"]),
        ("REGIME_CHANGE (inverted)", {
            "verdict": results["regime_verdict"]["verdict"]
                       if results["regime_verdict"]["verdict"] == "INVERTED"
                       else "—",
            "fwd_uplift": results["regime_verdict"]["inv_uplift"],
            "fwd_hit_rate": results["regime_verdict"]["inv_hit_rate"],
        }),
        ("FUNDING_EXTREME", results["funding_extreme_verdict"]),
        ("FUNDING_FLIP", results["funding_flip_verdict"]),
        ("Z_SPIKE", results["zspike_verdict"]),
    ]
    for label, v in summary_rows:
        ups = v.get("fwd_uplift") or {}
        hrs = v.get("fwd_hit_rate") or {}
        best_up = max(ups.values(), default=float("nan"))
        best_hr = max(hrs.values(), default=float("nan"))
        md.append(f"| {label} | {v.get('verdict','—')} | "
                  f"{best_up:+.3f} | {best_hr:.3f} |")
    md.append("")

    out_md = out_dir / "report.md"
    out_md.write_text("\n".join(md))
    out_json = out_dir / "diag.json"

    def _ser(o):
        if isinstance(o, (np.floating, np.integer)):
            return float(o)
        if isinstance(o, pd.DataFrame):
            return o.to_dict(orient="records")
        return str(o)

    json_payload = {
        k: (v.to_dict(orient="records")
            if isinstance(v, pd.DataFrame) else v)
        for k, v in results.items()
    }
    out_json.write_text(json.dumps(json_payload, indent=2, default=_ser))
    print(f"[multi-diag] artifacts in: {out_dir}")


# ─── CLI ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Multi-detector predictive diagnostic (Step 13)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--out", default=str(OUT_DIR))
    args = p.parse_args(argv)

    storage = Storage(db_path=Path(args.db), read_only=True)
    try:
        # Collect all symbols from any signal
        all_syms_query = storage._conn.execute(
            "SELECT DISTINCT symbol FROM signal_events "
            "WHERE event_type IN ('REGIME_CHANGE','FUNDING_EXTREME',"
            "'FUNDING_FLIP','Z_SPIKE')"
        ).fetchall()
        # Z_SPIKE symbols are 'A|B'; split for ohlcv loading
        flat: set[str] = set()
        for (s,) in all_syms_query:
            if not s:
                continue
            for part in s.split("|"):
                flat.add(part)
        symbols = sorted(flat)
        print(f"[multi-diag] symbols touched: {len(symbols)}")
        ohlcv = load_ohlcv_lookup(storage, symbols)

        # 13A REGIME forward + inverted
        regime_sigs = load_signals(storage, "REGIME_CHANGE")
        print(f"[multi-diag] REGIME_CHANGE: {len(regime_sigs)}")
        regime_fwd = predictive_table(
            regime_sigs, lambda p: _direction_regime(p, invert=False),
            ohlcv, T_BUCKETS, T_LABELS,
        )
        regime_inv = predictive_table(
            regime_sigs, lambda p: _direction_regime(p, invert=True),
            ohlcv, T_BUCKETS, T_LABELS,
        )
        regime_verdict = verdict_one("REGIME_CHANGE", regime_fwd, T_LABELS, regime_inv)

        # 13B per detector
        fe_sigs = load_signals(storage, "FUNDING_EXTREME")
        print(f"[multi-diag] FUNDING_EXTREME: {len(fe_sigs)}")
        fe_table = predictive_table(
            fe_sigs, _direction_funding_extreme,
            ohlcv, F_BUCKETS, F_LABELS,
        )
        fe_verdict = verdict_one("FUNDING_EXTREME", fe_table, F_LABELS)

        ff_sigs = load_signals(storage, "FUNDING_FLIP")
        print(f"[multi-diag] FUNDING_FLIP: {len(ff_sigs)}")
        ff_table = predictive_table(
            ff_sigs, _direction_funding_flip,
            ohlcv, F_BUCKETS, F_LABELS,
        )
        ff_verdict = verdict_one("FUNDING_FLIP", ff_table, F_LABELS)

        zs_sigs = load_signals(storage, "Z_SPIKE")
        print(f"[multi-diag] Z_SPIKE: {len(zs_sigs)}")
        zs_table = predictive_table(
            zs_sigs, _direction_zspike,
            ohlcv, Z_BUCKETS, Z_LABELS,
            pair_split=True,
        )
        zs_verdict = verdict_one("Z_SPIKE", zs_table, Z_LABELS)

        results = {
            "regime_fwd": regime_fwd,
            "regime_inv": regime_inv,
            "regime_verdict": regime_verdict,
            "funding_extreme": fe_table,
            "funding_extreme_verdict": fe_verdict,
            "funding_flip": ff_table,
            "funding_flip_verdict": ff_verdict,
            "zspike": zs_table,
            "zspike_verdict": zs_verdict,
        }
        write_report(Path(args.out), results)

        print()
        print(f"[multi-diag] 13A REGIME verdict: {regime_verdict['verdict']}")
        print(f"[multi-diag] 13B FUNDING_EXTREME: {fe_verdict['verdict']}")
        print(f"[multi-diag] 13B FUNDING_FLIP:    {ff_verdict['verdict']}")
        print(f"[multi-diag] 13B Z_SPIKE:         {zs_verdict['verdict']}")
    finally:
        storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
