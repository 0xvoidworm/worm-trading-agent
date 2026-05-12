"""Step 15A — Slippage Sensitivity Recompute (read-only).

Loads V1/V2/V3 trades.csv from step14b artifacts, applies per-side slippage
in bps to entry+exit notional, reports realized% / sharpe / alpha vs BnH for
4 scenarios: 0bps, 5bps, 10bps, 20bps.

Sharpe approach: reconstruct realized equity = starting_cap + cumsum(pnl-slip)
indexed by exit_ts, daily-resample (ffill), compute daily simple returns,
annualize sqrt(365).

Output: markdown table + decision verdict.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data" / "backtest_results" / "step14b_funding_flip_365d"
STARTING_CAP = 10000.0
SCENARIOS_BPS = [0, 5, 10, 20]
VARIANTS = ["V1", "V2", "V3"]


def _load(variant: str) -> tuple[pd.DataFrame, dict]:
    sub = ART / "per_variant" / f"step14b_funding_flip_365d_{variant}"
    trades = pd.read_csv(sub / "trades.csv", parse_dates=["ts", "exit_ts"])
    summary = json.loads((sub / "summary.json").read_text())
    return trades, summary


def _annualised_sharpe(daily_returns: pd.Series) -> float:
    if len(daily_returns) < 2:
        return float("nan")
    sd = daily_returns.std(ddof=1)
    if sd == 0 or math.isnan(sd):
        return float("nan")
    return float(daily_returns.mean() / sd * math.sqrt(365))


def _max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(-dd.min() * 100.0)


def _scenario(trades: pd.DataFrame, bps: int, end_ts: pd.Timestamp) -> dict:
    df = trades[trades["status"] == "CLOSED"].copy()
    if df.empty:
        return dict(realized_pct=0.0, sharpe=float("nan"),
                    max_dd_pct=0.0, n=0)
    entry_notional = df["entry_price"] * df["size"]
    exit_notional = df["exit_price"] * df["size"]
    slip_cost = (entry_notional + exit_notional) * (bps / 10000.0)
    adj_pnl = df["pnl"] - slip_cost
    realized_pct = float(adj_pnl.sum() / STARTING_CAP * 100.0)

    # Equity series indexed by exit_ts (aggregate same-ts trades), daily resample
    eq = pd.DataFrame({"exit_ts": df["exit_ts"], "delta": adj_pnl})
    eq = eq.groupby("exit_ts", as_index=True)["delta"].sum().sort_index()
    eq = STARTING_CAP + eq.cumsum()
    # Build daily series from window start to end (ffill)
    full_idx = pd.date_range(eq.index.min().normalize(),
                             end_ts.normalize(), freq="D")
    daily = eq.reindex(full_idx, method="ffill").fillna(STARTING_CAP)
    daily_ret = daily.pct_change().dropna()
    sharpe = _annualised_sharpe(daily_ret)
    max_dd = _max_drawdown_pct(daily)
    return dict(realized_pct=realized_pct, sharpe=sharpe,
                max_dd_pct=max_dd, n=len(df), slip_total=float(slip_cost.sum()))


def main() -> int:
    rows = []
    bnh = None
    for v in VARIANTS:
        trades, summary = _load(v)
        if bnh is None:
            bnh = float(summary["buy_hold_btc"]["return_pct"])
        end_ts = pd.Timestamp(summary["end"])
        for bps in SCENARIOS_BPS:
            res = _scenario(trades, bps, end_ts)
            rows.append({
                "variant": v,
                "bps_per_side": bps,
                "n_closed": res["n"],
                "realized_pct": res["realized_pct"],
                "sharpe": res["sharpe"],
                "max_dd_pct": res["max_dd_pct"],
                "alpha_vs_bnh": res["realized_pct"] - bnh,
                "slip_cost_usdt": res.get("slip_total", 0.0),
            })

    df = pd.DataFrame(rows)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    # Decision gate per spec
    v2 = df[df["variant"] == "V2"].set_index("bps_per_side")
    r5 = float(v2.loc[5, "realized_pct"])
    r10 = float(v2.loc[10, "realized_pct"])

    print("\n--- DECISION GATE (V2) ---")
    print(f"V2 @ 5bps  realized = {r5:+.4f}%")
    print(f"V2 @ 10bps realized = {r10:+.4f}%")
    print(f"BnH BTC baseline = {bnh:+.4f}%")
    if r10 > 0:
        verdict = "PROCEED_15B"
        rationale = "V2 @ 10bps masih > 0%"
    elif r5 <= 0:
        verdict = "STOP_PIVOT_REQUIRED"
        rationale = "V2 @ 5bps already ≤ 0% — edge tipis untuk friction real"
    else:
        verdict = "PROCEED_15B_WITH_CONCERN"
        rationale = "V2 @ 5bps positive but @ 10bps negative — borderline"
    print(f"VERDICT: {verdict}")
    print(f"RATIONALE: {rationale}")

    out_dir = ART
    out_md = out_dir / "step15a_slippage_sensitivity.md"
    lines = [
        "# Step 15A — Slippage Sensitivity (Recompute, no re-run)",
        "",
        f"Source: `step14b_funding_flip_365d` V1/V2/V3 trades.csv",
        f"Starting capital: {STARTING_CAP:.0f} USDT",
        f"BnH BTC baseline (1y window): {bnh:+.4f}%",
        "",
        "## Scenarios",
        "",
        "| Variant | bps/side | n | Realized% | Sharpe | MaxDD% | α vs BnH | Slip cost (USDT) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['variant']} | {int(r['bps_per_side'])} | {int(r['n_closed'])} "
            f"| {r['realized_pct']:+.4f} | {r['sharpe']:+.4f} | {r['max_dd_pct']:.4f} "
            f"| {r['alpha_vs_bnh']:+.4f} | {r['slip_cost_usdt']:.2f} |"
        )
    lines += [
        "",
        "## Decision Gate (per spec, V2 anchor)",
        "",
        f"- V2 @ 5bps realized = **{r5:+.4f}%**",
        f"- V2 @ 10bps realized = **{r10:+.4f}%**",
        "",
        f"**VERDICT: `{verdict}`** — {rationale}",
        "",
        "Decision rules:",
        "- V2 @ 10bps > 0% → PROCEED 15B",
        "- V2 @ 5bps ≤ 0% → STOP, pivot strategy (edge tipis untuk friction)",
        "- V2 @ 5bps > 0% but V2 @ 10bps ≤ 0% → PROCEED 15B with concern flag",
        "",
        "## Notes",
        "- Slippage applied 2 sides (entry + exit), proportional to notional",
        "- Sharpe: realized equity series, daily resampled, annualised √365",
        "- MaxDD recomputed on slippage-adjusted equity",
        "- Per-trade `pnl` di trades.csv sudah net of fees (0.11% per side via PaperBroker), slippage = friction tambahan on top of fees",
    ]
    out_md.write_text("\n".join(lines) + "\n")
    print(f"\nReport: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
