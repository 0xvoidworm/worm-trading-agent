#!/usr/bin/env python3
"""Detector Replay One-Shot — Step 11 (unblock empty quarters).

Tujuan: feed historical OHLCV+funding ke detector logic dan emit `signal_events`
backdated ke `bar_ts`. Sekali jalan, hasil persistent. Daemon production tetap
forward-only dengan limit=500 (cycle latency normal).

Prerequisites:
  1. Stop daemon: `./scripts/daemonctl.sh stop`
  2. Confirm idle: `./scripts/daemonctl.sh status` → "not running"
  3. Run: `.venv/bin/python scripts/detector_replay.py [--dry-run]`

Procedure (G2 watermark coordination):
  - Replay tulis signal_events dengan ts < daemon's _last_event_ts (semua
    bar_ts ≤ end - 1d, daemon's max_ts mendekati now)
  - Setelah replay sukses, daemon restart → REGIME_CHANGE detector pakai
    `_last_event_ts` per symbol → continue forward-only dari MAX(ts) terbaru
    (yang akan tetap mendekati live, bukan 365d ago, karena replay window
    end < live edge).
  - Funding/Z_SPIKE detector idempotent via dedup id → safe to overlap.

Guardrails:
  G1 — as_of_ts integrity: emit ts = bar_ts (NEVER now).
  G3 — idempotency: dedup id (event_type|symbol|bucket|payload-key) prevents
       double-emit. Two consecutive runs return same emit count.
  G4 — backup: sebelum write, CREATE TABLE signal_events_pre_step11_backup.
  G6 — threshold preservation: import konstanta dari dashboard.signal_log.
  G7 — performance budget: per-symbol 10 min, total 90 min.

NOT covered (intentionally — Step 12+):
  - Threshold recalibration (FUNDING_EXTREME=0.001 too tight per Step 10)
  - Schema flag column for via_replay (added via ALTER TABLE if not present;
    forward-emit rows = NULL, replay-emit rows = TRUE)
"""
from __future__ import annotations

import argparse
import sys
import time
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.storage import Storage  # noqa: E402
from dashboard.regime import classify  # noqa: E402
from dashboard.cointegration import engle_granger  # noqa: E402
# Import constants — G6 threshold preservation
from dashboard.signal_log import (  # noqa: E402
    FUNDING_EXTREME,
    FUNDING_FLIP_MIN_ABS,
    Z_SPIKE,
    Z_ROLL_WINDOW,
    _mk_id,
    _event,
)


DEFAULT_DB = ROOT / "data" / "market.duckdb"
DEFAULT_CFG = ROOT / "config" / "pairs.yaml"
BACKUP_TABLE = "signal_events_pre_step11_backup"


# ─── re-implement detector loops dengan FULL HISTORY (no limit=500) ─────

def replay_regime_changes(
    storage: Storage, sym: str, exchange: str, tf: str,
    start: datetime, end: datetime,
) -> list[dict]:
    """Walk bar-by-bar pada full history, emit REGIME_CHANGE pada transisi.

    Replikasi exact dari `detect_regime_changes` di signal_log.py tapi:
      - Tanpa limit=500 (full history)
      - Tanpa baca last signal — fresh replay, prev_regime=None at start
        (akan emit `from=?` di first non-insufficient bar within window)
    """
    df = storage.get_ohlcv(exchange, sym, tf)  # NO LIMIT
    if len(df) < 30:
        return []
    df_ts = pd.to_datetime(df["ts"])
    events: list[dict] = []
    prev_regime: Optional[str] = None
    for i in range(29, len(df)):
        bar_ts = df_ts.iloc[i].to_pydatetime()
        # Selalu jalankan classify untuk warm-up prev_regime tracker, supaya
        # transisi yang melintasi boundary `start` tidak salah-seed.
        window = df.iloc[: i + 1]
        snap = classify(window)
        if snap.regime == "insufficient_data":
            continue

        in_window = start <= bar_ts <= end
        if prev_regime is None:
            if in_window:
                payload = (
                    f"from=?;to={snap.regime};"
                    f"t={snap.trend_slope_t:.2f}"
                )
                events.append(_event(bar_ts, "REGIME_CHANGE", sym, "info", payload))
            prev_regime = snap.regime
            continue

        if snap.regime == prev_regime:
            continue

        if in_window:
            sev = "warn" if snap.regime == "chaos" else "info"
            payload = (
                f"from={prev_regime};to={snap.regime};"
                f"t={snap.trend_slope_t:.2f}"
            )
            events.append(_event(bar_ts, "REGIME_CHANGE", sym, sev, payload))
        prev_regime = snap.regime
    return events


def replay_funding_events(
    storage: Storage, sym: str, exchange: str,
    start: datetime, end: datetime,
    funding_extreme_threshold: float | None = None,
) -> list[dict]:
    """Walk semua funding row (no limit), emit FUNDING_EXTREME / FUNDING_FLIP.

    Threshold dari konstanta signal_log (G6). Filter window pada bar_ts.

    `funding_extreme_threshold` (default None → use signal_log.FUNDING_EXTREME).
    Override dipakai Step 12B recalibration ke p99 = 0.000242.
    """
    fe_thresh = (
        funding_extreme_threshold
        if funding_extreme_threshold is not None
        else FUNDING_EXTREME
    )
    df = storage.get_funding(exchange, sym)  # NO LIMIT
    if len(df) < 2:
        return []
    fts = pd.to_datetime(df["ts"])
    events: list[dict] = []
    for i in range(1, len(df)):
        ts = fts.iloc[i].to_pydatetime()
        in_window = start <= ts <= end
        curr = float(df.iloc[i]["funding"])
        prev = float(df.iloc[i - 1]["funding"])
        bucket = fts.iloc[i].strftime("%Y%m%d%H")

        if in_window and abs(curr) >= fe_thresh:
            side = "long-pays" if curr > 0 else "short-pays"
            payload = (
                f"rate={curr*100:.4f}%;apr={curr*3*365*100:+.1f}%;side={side}"
            )
            ev = _event(ts, "FUNDING_EXTREME", sym, "critical", payload)
            ev["id"] = _mk_id("FUNDING_EXTREME", sym, bucket, f"{curr:.5f}")
            events.append(ev)

        if in_window and (curr > 0) != (prev > 0) and abs(curr) >= FUNDING_FLIP_MIN_ABS:
            payload = f"prev={prev*100:.4f}%;curr={curr*100:.4f}%"
            ev = _event(ts, "FUNDING_FLIP", sym, "warn", payload)
            ev["id"] = _mk_id("FUNDING_FLIP", sym, bucket, f"{curr:.5f}")
            events.append(ev)
    return events


def replay_zspike(
    storage: Storage, pair: list[str], exchange: str, tf: str,
    start: datetime, end: datetime,
) -> list[dict]:
    """OLS fit pada full history, rolling z-score, emit fresh crossings."""
    a, b = pair
    df_a = storage.get_ohlcv(exchange, a, tf)  # NO LIMIT
    df_b = storage.get_ohlcv(exchange, b, tf)
    if df_a.empty or df_b.empty:
        return []
    sa = df_a.set_index("ts")["close"]
    sb = df_b.set_index("ts")["close"]
    y = np.log(sa)
    x = np.log(sb)
    try:
        res = engle_granger(y, x, pair_names=(a, b))
    except Exception:
        return []
    if not res.is_cointegrated:
        return []

    df = pd.concat([y, x], axis=1).dropna()
    df.columns = ["y", "x"]
    if len(df) < Z_ROLL_WINDOW + 2:
        return []
    X = np.column_stack([np.ones(len(df)), df["x"].values])
    m = OLS(df["y"].values, X).fit()
    alpha, beta = m.params
    spread = df["y"] - (alpha + beta * df["x"])
    mean = spread.rolling(Z_ROLL_WINDOW).mean()
    std = spread.rolling(Z_ROLL_WINDOW).std()
    z = (spread - mean) / std
    z = z.dropna()
    if len(z) < 2:
        return []

    events: list[dict] = []
    prev_abs = abs(z.iloc[0])
    ts_index = z.index
    for i in range(1, len(z)):
        zval = float(z.iloc[i])
        abs_now = abs(zval)
        bar_ts = pd.Timestamp(ts_index[i]).to_pydatetime()
        if abs_now >= Z_SPIKE and prev_abs < Z_SPIKE:
            in_window = start <= bar_ts <= end
            if in_window:
                side = "short-spread" if zval > 0 else "long-spread"
                payload = (
                    f"pair={a}|{b};z={zval:+.2f};adf_p={res.adf_pvalue:.3f};"
                    f"hl={res.half_life_bars:.1f};side={side}"
                )
                sev = "critical" if abs_now >= 3 else "warn"
                bucket = pd.Timestamp(ts_index[i]).strftime("%Y%m%d%H")
                ev = _event(bar_ts, "Z_SPIKE", f"{a}|{b}", sev, payload)
                ev["id"] = _mk_id("Z_SPIKE", f"{a}|{b}", bucket, side)
                events.append(ev)
        prev_abs = abs_now
    return events


# ─── DB helpers ─────────────────────────────────────────────────────────

def ensure_via_replay_column(storage: Storage) -> bool:
    """Add `via_replay BOOLEAN` column if not present. Return True if newly added."""
    cols = storage._conn.execute(
        "PRAGMA table_info(signal_events)"
    ).fetchall()
    has_col = any(c[1] == "via_replay" for c in cols)
    if not has_col:
        storage._conn.execute(
            "ALTER TABLE signal_events ADD COLUMN via_replay BOOLEAN DEFAULT NULL"
        )
        return True
    return False


def make_backup(storage: Storage) -> tuple[int, str]:
    """G4 — CREATE TABLE backup if not exists. Return (row_count, table_name)."""
    pre_count = storage._conn.execute(
        "SELECT COUNT(*) FROM signal_events"
    ).fetchone()[0]
    storage._conn.execute(
        f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS "
        f"SELECT * FROM signal_events"
    )
    backup_count = storage._conn.execute(
        f"SELECT COUNT(*) FROM {BACKUP_TABLE}"
    ).fetchone()[0]
    if backup_count != pre_count:
        # Already-existing backup may have stale count; not necessarily a bug.
        # But warn loudly so operator sees.
        print(f"[replay] WARN: backup row count {backup_count} != live "
              f"{pre_count}. Backup created earlier; not overwritten.")
    return pre_count, BACKUP_TABLE


def upsert_replay_signals(storage: Storage, rows: list[dict]) -> int:
    """INSERT ... ON CONFLICT DO NOTHING with via_replay=TRUE.

    Mirrors `Storage.upsert_signals` but populates extra column.
    """
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    df["via_replay"] = True
    storage._conn.register("df_tmp_replay", df)
    before = storage._conn.execute(
        "SELECT COUNT(*) FROM signal_events"
    ).fetchone()[0]
    storage._conn.execute(
        "INSERT INTO signal_events "
        "(id, ts, event_type, symbol, severity, payload, via_replay) "
        "SELECT id, ts, event_type, symbol, severity, payload, via_replay "
        "FROM df_tmp_replay "
        "ON CONFLICT (id) DO NOTHING"
    )
    storage._conn.unregister("df_tmp_replay")
    after = storage._conn.execute(
        "SELECT COUNT(*) FROM signal_events"
    ).fetchone()[0]
    return after - before


# ─── orchestration ──────────────────────────────────────────────────────

def load_cfg() -> dict:
    return yaml.safe_load(DEFAULT_CFG.read_text())


def resolve_symbols(cfg: dict, arg: str) -> list[str]:
    if arg in ("all", "*"):
        return cfg["tier1"] + cfg["tier2"]
    return [s.strip() for s in arg.split(",") if s.strip()]


def resolve_detectors(arg: str) -> set[str]:
    if arg in ("all", "*"):
        return {"regime", "funding_extreme", "funding_flip", "zspike"}
    return {d.strip() for d in arg.split(",") if d.strip()}


def run_replay(
    db_path: Path, symbols: list[str], detectors: set[str],
    start: datetime, end: datetime, dry_run: bool,
    cointegration_pairs: list[list[str]], exchange: str, tf: str,
    funding_extreme_threshold: float | None = None,
) -> dict:
    """Main orchestrator. Returns stats dict for reporting."""
    storage = Storage(db_path=db_path)  # writeable
    pre_count, backup_table = (None, None)
    schema_extended = False
    try:
        if not dry_run:
            schema_extended = ensure_via_replay_column(storage)
            pre_count, backup_table = make_backup(storage)

        per_symbol: list[dict] = []
        per_pair: list[dict] = []
        total_emitted = 0

        # Per-symbol detectors (regime + funding)
        for sym in symbols:
            t0 = time.time()
            events: list[dict] = []
            counts: dict[str, int] = {}

            if "regime" in detectors:
                rc = replay_regime_changes(storage, sym, exchange, tf, start, end)
                events.extend(rc)
                counts["regime"] = len(rc)

            if "funding_extreme" in detectors or "funding_flip" in detectors:
                fund = replay_funding_events(
                    storage, sym, exchange, start, end,
                    funding_extreme_threshold=funding_extreme_threshold,
                )
                fe = [e for e in fund if e["event_type"] == "FUNDING_EXTREME"]
                ff = [e for e in fund if e["event_type"] == "FUNDING_FLIP"]
                if "funding_extreme" in detectors:
                    events.extend(fe)
                    counts["funding_extreme"] = len(fe)
                if "funding_flip" in detectors:
                    events.extend(ff)
                    counts["funding_flip"] = len(ff)

            elapsed = time.time() - t0
            inserted = (
                0 if dry_run
                else upsert_replay_signals(storage, events)
            )
            total_emitted += len(events)
            per_symbol.append({
                "symbol": sym,
                "elapsed_sec": round(elapsed, 2),
                "would_emit": len(events),
                "actually_inserted": inserted,
                "by_detector": counts,
            })
            print(f"[replay] {sym:14}  {elapsed:6.2f}s  "
                  f"would-emit={len(events):4d}  inserted={inserted:4d}  "
                  f"{counts}")

        # Per-pair (zspike)
        if "zspike" in detectors:
            for pair in cointegration_pairs:
                t0 = time.time()
                z = replay_zspike(storage, pair, exchange, tf, start, end)
                elapsed = time.time() - t0
                inserted = 0 if dry_run else upsert_replay_signals(storage, z)
                total_emitted += len(z)
                per_pair.append({
                    "pair": "|".join(pair),
                    "elapsed_sec": round(elapsed, 2),
                    "would_emit": len(z),
                    "actually_inserted": inserted,
                })
                print(f"[replay] {('|'.join(pair)):20}  "
                      f"{elapsed:6.2f}s  would-emit={len(z):3d}  "
                      f"inserted={inserted:3d}")
    finally:
        storage.close()

    return {
        "dry_run": dry_run,
        "schema_extended": schema_extended,
        "backup_table": backup_table,
        "pre_count": pre_count,
        "per_symbol": per_symbol,
        "per_pair": per_pair,
        "total_emitted": total_emitted,
    }


# ─── CLI ────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=None)


def main(argv: Optional[list[str]] = None) -> int:
    cfg = load_cfg()
    p = argparse.ArgumentParser(description="Detector Replay One-Shot (Step 11)")
    p.add_argument("--symbols", default="all",
                   help="comma-list atau 'all'. Default: all 8 dari config.")
    p.add_argument("--start", default=None,
                   help="YYYY-MM-DD. Default: min(ohlcv.ts).")
    p.add_argument("--end", default=None,
                   help="YYYY-MM-DD. Default: max(ohlcv.ts) - 1d.")
    p.add_argument("--detectors", default="all",
                   help="comma-list dari {regime,funding_extreme,funding_flip,"
                        "zspike} atau 'all'.")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute & log emit counts, do NOT write.")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--funding-extreme-threshold", type=float, default=None,
                   help="Override FUNDING_EXTREME threshold "
                        "(default: signal_log.FUNDING_EXTREME=0.001). "
                        "Step 12B uses 0.000242 (p99 per Step 10 distribution).")
    args = p.parse_args(argv)

    symbols = resolve_symbols(cfg, args.symbols)
    detectors = resolve_detectors(args.detectors)
    pairs = cfg.get("cointegration_candidates", [])
    exchange = cfg.get("exchange", "bybit")
    tf = cfg.get("timeframe", "1h")

    # Resolve start/end from data extent if unspecified
    src = Storage(db_path=Path(args.db), read_only=True)
    try:
        row = src._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM ohlcv WHERE timeframe=? AND exchange=?",
            [tf, exchange],
        ).fetchone()
    finally:
        src.close()
    data_min, data_max = row
    start = _parse_dt(args.start) if args.start else data_min
    end = _parse_dt(args.end) if args.end else (data_max - timedelta(days=1))
    if end <= start:
        print("ERROR: end <= start", file=sys.stderr)
        return 2

    print(f"\n[replay] mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print(f"[replay] window: {start} → {end}")
    print(f"[replay] symbols ({len(symbols)}): {symbols}")
    print(f"[replay] detectors: {sorted(detectors)}")
    print(f"[replay] zspike pairs: {pairs}")
    if args.funding_extreme_threshold is not None:
        print(f"[replay] FUNDING_EXTREME threshold OVERRIDE: "
              f"{args.funding_extreme_threshold} (default {FUNDING_EXTREME})")
    print()

    t0 = time.time()
    stats = run_replay(
        db_path=Path(args.db), symbols=symbols, detectors=detectors,
        start=start, end=end, dry_run=args.dry_run,
        cointegration_pairs=pairs, exchange=exchange, tf=tf,
        funding_extreme_threshold=args.funding_extreme_threshold,
    )
    total_elapsed = time.time() - t0

    print(f"\n[replay] DONE in {total_elapsed:.1f}s "
          f"({total_elapsed/60:.1f} min)")
    print(f"[replay] total would-emit: {stats['total_emitted']}")
    if not args.dry_run:
        print(f"[replay] backup table: {stats['backup_table']} "
              f"(pre_count={stats['pre_count']})")
        print(f"[replay] schema extended (via_replay col): "
              f"{stats['schema_extended']}")

    # G7 budget check
    budget_total = 90 * 60
    if total_elapsed > budget_total:
        print(f"[replay] WARN: exceeded total budget "
              f"({budget_total}s) — review performance")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
