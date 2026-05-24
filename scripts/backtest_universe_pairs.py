#!/usr/bin/env python3
"""Universe-expansion pair backtest. ~30 candidate pairs across sectors.

Uses _backtest_pair logic from backtest_spot_pairs.py via import.
Runs all pairs, ranks by recent-year (P3) verdict, writes
data/backtest_results/universe_pairs_<date>.md.

Universe (post backfill_universe.py): ADA, DOT, TON, SUI, APT, NEAR,
STX, POL, FET, WLD, UNI, AAVE, LINK, LDO, ONDO, DOGE, PEPE + existing
BTC, ETH, SOL, BNB, XRP, AVAX, ARB, OP.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Reuse helpers from existing script
from backtest_spot_pairs import (  # noqa: E402
    _backtest_pair, _fetch_pair_ohlcv, _verdict, _walk_forward_periods,
    DEFAULT_LOOKBACK_BARS, DEFAULT_Z_ENTRY, DEFAULT_Z_EXIT,
    DEFAULT_MAX_HOLD_DAYS, DEFAULT_STOP_LOSS_PCT, DEFAULT_FEE_PCT_SIDE,
)

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
OUT_DIR = REPO_ROOT / "data" / "backtest_results"

# Candidate pairs — same-sector intra-pair + selected cross-sector
EXPANSION_PAIRS: list[tuple[str, str]] = [
    # L1 vs L1 (within Layer-1 cluster)
    ("ETH/USDT", "BTC/USDT"),
    ("SOL/USDT", "ETH/USDT"),
    ("AVAX/USDT", "SOL/USDT"),
    ("ADA/USDT", "ETH/USDT"),
    ("ADA/USDT", "SOL/USDT"),
    ("DOT/USDT", "ETH/USDT"),
    ("DOT/USDT", "SOL/USDT"),
    ("TON/USDT", "ETH/USDT"),
    ("SUI/USDT", "ETH/USDT"),
    ("SUI/USDT", "SOL/USDT"),
    ("APT/USDT", "SOL/USDT"),
    ("APT/USDT", "SUI/USDT"),
    ("NEAR/USDT", "SOL/USDT"),
    ("NEAR/USDT", "AVAX/USDT"),
    # L2 vs L2
    ("ARB/USDT", "OP/USDT"),
    ("POL/USDT", "ARB/USDT"),
    ("STX/USDT", "ETH/USDT"),
    # AI vs AI
    ("FET/USDT", "WLD/USDT"),
    ("FET/USDT", "ETH/USDT"),
    ("WLD/USDT", "ETH/USDT"),
    # DeFi vs DeFi
    ("UNI/USDT", "AAVE/USDT"),
    ("UNI/USDT", "LINK/USDT"),
    ("AAVE/USDT", "LINK/USDT"),
    # LSD vs ETH (proxy)
    ("LDO/USDT", "ETH/USDT"),
    # RWA
    ("ONDO/USDT", "ETH/USDT"),
    # CEX vs Store
    ("BNB/USDT", "BTC/USDT"),
    # Memes vs Memes
    ("DOGE/USDT", "PEPE/USDT"),
    ("DOGE/USDT", "BTC/USDT"),
    # Payment
    ("XRP/USDT", "LTC/USDT"),
    # ETH vs BNB
    ("ETH/USDT", "BNB/USDT"),
    # Round 2 expansion (added 2026-05-24 second pass)
    # L1 cluster
    ("TIA/USDT", "ETH/USDT"),
    ("TIA/USDT", "SOL/USDT"),
    ("SEI/USDT", "SOL/USDT"),
    ("INJ/USDT", "ETH/USDT"),
    ("INJ/USDT", "SOL/USDT"),
    ("ATOM/USDT", "ETH/USDT"),
    ("TRX/USDT", "ETH/USDT"),
    ("BCH/USDT", "BTC/USDT"),
    # L2 cluster
    ("STRK/USDT", "ARB/USDT"),
    ("STRK/USDT", "ETH/USDT"),
    # Restaking vs ETH
    ("EIGEN/USDT", "ETH/USDT"),
    # AI-narrative
    ("VIRTUAL/USDT", "ETH/USDT"),
    # DeFi/DEX cluster
    ("JUP/USDT", "UNI/USDT"),
    ("CAKE/USDT", "UNI/USDT"),
    ("PYTH/USDT", "LINK/USDT"),
    # Stablecoin DAO
    ("ENA/USDT", "ETH/USDT"),
    # Memes cluster
    ("SHIB/USDT", "DOGE/USDT"),
    ("WIF/USDT", "BONK/USDT"),
    ("WIF/USDT", "DOGE/USDT"),
    ("SHIB/USDT", "PEPE/USDT"),
    # Gaming
    ("IMX/USDT", "ETH/USDT"),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--periods", type=int, default=3, help="walk-forward split (default 3)")
    p.add_argument("--z", type=float, default=DEFAULT_Z_ENTRY)
    p.add_argument("--hold", type=int, default=DEFAULT_MAX_HOLD_DAYS)
    args = p.parse_args()

    max_hold_h = args.hold * 24
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    all_results = []
    try:
        for i, (pa, pb) in enumerate(EXPANSION_PAIRS, 1):
            merged = _fetch_pair_ohlcv(conn, pa, pb)
            if merged.empty:
                print(f"[{i}/{len(EXPANSION_PAIRS)}] {pa}/{pb}: NO DATA")
                continue
            # Full + per-period
            full = _backtest_pair(
                merged, pa, pb, lookback=DEFAULT_LOOKBACK_BARS,
                z_entry=args.z, z_exit=DEFAULT_Z_EXIT,
                max_hold_h=max_hold_h, sl_pct=DEFAULT_STOP_LOSS_PCT,
                fee_pct_side=DEFAULT_FEE_PCT_SIDE,
            )
            full["pair_label"] = f"{pa}/{pb}"
            full["period"] = "full"
            all_results.append(full)
            # Per-period
            for label, sub in _walk_forward_periods(merged, args.periods):
                r = _backtest_pair(
                    sub, pa, pb, lookback=DEFAULT_LOOKBACK_BARS,
                    z_entry=args.z, z_exit=DEFAULT_Z_EXIT,
                    max_hold_h=max_hold_h, sl_pct=DEFAULT_STOP_LOSS_PCT,
                    fee_pct_side=DEFAULT_FEE_PCT_SIDE,
                )
                r["pair_label"] = f"{pa}/{pb}"
                r["period"] = label.split("(")[0]
                all_results.append(r)
            v = _verdict(full)
            print(f"[{i}/{len(EXPANSION_PAIRS)}] {pa}/{pb}: full {full.get('n_trades', 0)}t verdict={v}")
    finally:
        conn.close()

    # Build report
    lines = []
    lines.append(f"# Universe Pair Backtest — {datetime.now(timezone.utc):%Y-%m-%d}")
    lines.append(f"\n_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append(f"\n**{len(EXPANSION_PAIRS)} candidate pairs**, z entry ±{args.z}, hold {args.hold}d, SL {DEFAULT_STOP_LOSS_PCT}%, fee {DEFAULT_FEE_PCT_SIDE}%/side")
    lines.append("\n## Full-history Summary (3y)")
    lines.append("| Pair | N | Win% | Avg net% | Compound% | Verdict |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for r in all_results:
        if r.get("period") != "full":
            continue
        if r.get("n_trades", 0) == 0:
            lines.append(f"| {r['pair_label']} | 0 | — | — | — | NO_SIGNALS |")
            continue
        v = _verdict(r)
        wrp = r.get("win_rate_pct", 0)
        apn = r.get("avg_pct_net", 0)
        tcp = r.get("total_pct_compound", 0)
        lines.append(
            f"| {r['pair_label']} | {r['n_trades']} | {wrp:.1f} | "
            f"{apn:+.3f} | {tcp:+.2f} | {v} |"
        )

    lines.append(f"\n## Walk-Forward P{args.periods} (Recent Year, Most Relevant)")
    lines.append("| Pair | Period | N | Win% | Avg net% | Compound% | Verdict |")
    lines.append("|---|---|---:|---:|---:|---:|---|")
    # Show only P3 (recent) for each pair, sorted by avg_pct
    p3 = [r for r in all_results if r.get("period") == f"P{args.periods}" and r.get("n_trades", 0) > 0]
    p3.sort(key=lambda r: -r.get("avg_pct_net", -999))
    for r in p3:
        v = _verdict(r)
        tcp = r.get("total_pct_compound", 0)
        wrp = r.get("win_rate_pct", 0)
        apn = r.get("avg_pct_net", 0)
        lines.append(
            f"| {r['pair_label']} | P{args.periods} | {r['n_trades']} | "
            f"{wrp:.1f} | {apn:+.3f} | {tcp:+.2f} | {v} |"
        )

    lines.append("\n## Pair Verdict Across All Periods")
    pairs_seen = sorted({r['pair_label'] for r in all_results})
    lines.append("| Pair | Full | P1 | P2 | P3 (recent) |")
    lines.append("|---|---|---|---|---|")
    for pl in pairs_seen:
        cells = {p: "—" for p in ("full", "P1", "P2", "P3")}
        for r in all_results:
            if r.get("pair_label") != pl:
                continue
            period = r.get("period", "")
            if period in cells:
                if r.get("n_trades", 0) == 0:
                    cells[period] = "NO"
                else:
                    v = _verdict(r)
                    short = {"STABLE_POSITIVE": "POS+", "MARGINAL_POSITIVE": "POS",
                             "STABLE_NEGATIVE": "NEG-", "INCONSISTENT": "INC",
                             "INSUFFICIENT_SAMPLE": "INSF", "NO_DATA": "—",
                             "NO_SIGNALS": "—"}.get(v, v[:4])
                    cells[period] = f"{short}({r['n_trades']})"
        lines.append(f"| {pl} | {cells['full']} | {cells['P1']} | {cells['P2']} | {cells['P3']} |")

    out = OUT_DIR / f"universe_pairs_{datetime.now(timezone.utc):%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    md = "\n".join(lines) + "\n"
    out.write_text(md)
    print(f"\n[universe_pairs] report written: {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
