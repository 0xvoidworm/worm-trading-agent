"""Data quality monitor — freshness, gap detection, feed liveness.

Entry point: `run_health_check(storage, cfg) -> dict`

Status levels (priority tertinggi menang):
  STALE  — ada symbol dengan OHLCV terbaru > 2 jam → data dead (CRITICAL)
  GAP    — ada OHLCV gap, funding basi, atau RSS feed mati 24h (WARN)
  OK     — semua sehat

Threshold:
  OHLCV stale  : 2h  (timeframe 1h → expected 1 bar tiap jam)
  FUNDING stale: 12h (cadence 8h + buffer)
  FEED window  : 24h (0 item dari satu sumber = dead)
  OHLCV gap    : diff > expected * 1.5 (50% tolerance utk market pause)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .storage import Storage


OHLCV_STALE_SEC = 2 * 3600
FUNDING_STALE_SEC = 12 * 3600
FEED_WINDOW_HOURS = 24

_TF_SEC = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def _expected_seconds(tf: str) -> int:
    return _TF_SEC.get(tf, 3600)


def _age_sec(ts, now: datetime) -> float:
    """Selisih detik antara now (aware UTC) dan ts (DuckDB naive UTC)."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return (now - t.to_pydatetime()).total_seconds()


def check_ohlcv_gaps(
    storage: Storage, exchange: str, symbols: list[str], tf: str,
) -> list[dict]:
    """Return list per-symbol yang punya ≥1 gap (interval antar-bar > expected * 1.5).

    Agregat via SQL (DuckDB LAG + date_diff) supaya scan 1× saja.

    Defensive: kalau `symbols` kosong, return [] tanpa hit DB. Tanpa guard ini,
    SQL `symbol IN ()` jadi syntax error (caller seperti backtest harness atau
    cfg dengan tier1/tier2 kosong akan crash).
    """
    if not symbols:
        return []
    expected = _expected_seconds(tf)
    tolerance = expected * 1.5
    sql = (
        "WITH diffs AS ("
        "  SELECT symbol, "
        "    date_diff('second', LAG(ts) OVER (PARTITION BY symbol ORDER BY ts), ts) AS d "
        "  FROM ohlcv "
        "  WHERE exchange = ? AND timeframe = ? AND symbol IN ({ph}) "
        ") "
        "SELECT symbol, "
        "  COUNT(*) FILTER (WHERE d > ?) AS gap_count, "
        "  MAX(d) AS max_gap_sec, "
        "  COUNT(*) AS bar_intervals "
        "FROM diffs "
        "GROUP BY symbol "
        "HAVING COUNT(*) FILTER (WHERE d > ?) > 0 "
        "ORDER BY symbol"
    ).format(ph=",".join("?" * len(symbols)))
    params = [exchange, tf] + list(symbols) + [tolerance, tolerance]
    df = storage._conn.execute(sql, params).fetchdf()
    if df.empty:
        return []
    return [
        {
            "symbol": r["symbol"],
            "timeframe": tf,
            "gap_count": int(r["gap_count"]),
            "max_gap_hours": float(r["max_gap_sec"]) / 3600,
            "bar_intervals": int(r["bar_intervals"]),
        }
        for _, r in df.iterrows()
    ]


def check_staleness(
    storage: Storage, exchange: str, symbols: list[str], tf: str,
    *, now_utc: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return (ohlcv_stale, funding_stale) — entries yang melewati threshold.

    `now_utc` optional override untuk replay/backtest. Default wall clock.
    """
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if getattr(now, "tzinfo", None) is None:
        now = now.replace(tzinfo=timezone.utc)
    ohlcv_stale: list[dict] = []
    funding_stale: list[dict] = []

    for sym in symbols:
        last = storage.latest_ohlcv_ts(exchange, sym, tf)
        if last is None:
            ohlcv_stale.append(
                {"symbol": sym, "timeframe": tf, "last_ts": None, "age_hours": None}
            )
            continue
        age = _age_sec(last, now)
        if age > OHLCV_STALE_SEC:
            ohlcv_stale.append({
                "symbol": sym, "timeframe": tf, "last_ts": last,
                "age_hours": age / 3600,
            })

    for sym in symbols:
        last = storage.latest_funding_ts(exchange, sym)
        if last is None:
            continue  # funding kadang belum tersedia — jangan alarm
        age = _age_sec(last, now)
        if age > FUNDING_STALE_SEC:
            funding_stale.append({
                "symbol": sym, "last_ts": last, "age_hours": age / 3600,
            })

    return ohlcv_stale, funding_stale


def check_feeds(
    storage: Storage, window_hours: int = FEED_WINDOW_HOURS,
    *, now_utc: datetime | None = None,
) -> list[dict]:
    """Untuk tiap RSS feed terdaftar, hitung item dalam `window_hours` terakhir.

    `now_utc` optional override untuk replay/backtest. Default wall clock.
    """
    from .ingest_news import FEEDS  # local import — hindari circular jika nanti ditukar

    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if getattr(now, "tzinfo", None) is None:
        now = now.replace(tzinfo=timezone.utc)
    df = storage._conn.execute(
        "SELECT source, COUNT(*) AS n FROM news "
        "WHERE published_at >= ? - INTERVAL (?) HOUR "
        "GROUP BY source",
        [now, window_hours],
    ).fetchdf()
    counts: dict[str, int] = dict(zip(df["source"], df["n"])) if not df.empty else {}
    return [
        {
            "source": name,
            "count_24h": int(counts.get(name, 0)),
            "ok": counts.get(name, 0) > 0,
        }
        for name, _url in FEEDS
    ]


def run_health_check(
    storage: Storage, cfg: dict, *, now_utc: datetime | None = None,
) -> dict:
    """Full check. Return dict siap dirender ke CLI/TUI/log.

    `now_utc` optional — kalau diberikan dipakai sebagai anchor untuk
    semua freshness/feed window check (backtest replay). Production lewat
    None → wall clock.
    """
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    symbols = (cfg.get("tier1") or []) + (cfg.get("tier2") or [])

    gaps = check_ohlcv_gaps(storage, exchange, symbols, tf)
    ohlcv_stale, funding_stale = check_staleness(
        storage, exchange, symbols, tf, now_utc=now_utc,
    )
    feeds = check_feeds(storage, now_utc=now_utc)
    dead_feeds = [f for f in feeds if not f["ok"]]

    if ohlcv_stale:
        status = "STALE"
    elif gaps or funding_stale or dead_feeds:
        status = "GAP"
    else:
        status = "OK"

    n = len(symbols)
    summary = (
        f"ohlcv {n - len(ohlcv_stale)}/{n} fresh · "
        f"{len(gaps)} gap-symbols · "
        f"funding {n - len(funding_stale)}/{n} fresh · "
        f"feeds {len(feeds) - len(dead_feeds)}/{len(feeds)} live"
    )

    return {
        "status": status,
        "generated_at": (
            now_utc if now_utc is not None else datetime.now(timezone.utc)
        ),
        "exchange": exchange,
        "timeframe": tf,
        "symbols_checked": symbols,
        "ohlcv_gaps": gaps,
        "ohlcv_stale": ohlcv_stale,
        "funding_stale": funding_stale,
        "feeds": feeds,
        "summary": summary,
    }


def run_health_check_split(
    storage: Storage, cfg: dict, *, now_utc: datetime | None = None,
) -> dict:
    """Two-dimensional health check — separates **trading** vs **operational**.

    Step 6 motivation: production daemon dengan satu combined status nge-gate
    executor on operational issues (e.g. dead RSS feed) padahal auto_advisor
    saat ini hanya konsumsi ohlcv data. Backtest replay terutama kena karena
    historical news sparsity bikin feed-dead → GAP → ENTRY blocked di 94%
    cycles. Memisah dimensi supaya backtest harness bisa gate hanya
    berdasarkan trading_health, sementara operational tetap di-record untuk
    transparency.

    Dimensi:
      - **trading**: ohlcv freshness + ohlcv gap (data yang DIBUTUHKAN
        auto_advisor untuk decide). Status: OK | GAP | STALE.
      - **operational**: funding freshness + RSS feed liveness (dashboard /
        future signal types). Status: OK | GAP.

    Backward compat: `combined_status` mirror logic `run_health_check` lama
    (STALE > GAP > OK) supaya caller existing tidak break kalau migrasi
    inkremental.
    """
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    symbols = (cfg.get("tier1") or []) + (cfg.get("tier2") or [])

    gaps = check_ohlcv_gaps(storage, exchange, symbols, tf)
    ohlcv_stale, funding_stale = check_staleness(
        storage, exchange, symbols, tf, now_utc=now_utc,
    )
    feeds = check_feeds(storage, now_utc=now_utc)
    dead_feeds = [f for f in feeds if not f["ok"]]

    # Trading dimension
    if ohlcv_stale:
        trading_status = "STALE"
    elif gaps:
        trading_status = "GAP"
    else:
        trading_status = "OK"

    # Operational dimension
    if funding_stale or dead_feeds:
        operational_status = "GAP"
    else:
        operational_status = "OK"

    # Combined status (backward compat priority STALE > GAP > OK)
    if trading_status == "STALE":
        combined = "STALE"
    elif trading_status == "GAP" or operational_status == "GAP":
        combined = "GAP"
    else:
        combined = "OK"

    n = len(symbols)
    summary = (
        f"ohlcv {n - len(ohlcv_stale)}/{n} fresh · "
        f"{len(gaps)} gap-symbols · "
        f"funding {n - len(funding_stale)}/{n} fresh · "
        f"feeds {len(feeds) - len(dead_feeds)}/{len(feeds)} live"
    )

    return {
        "trading": {
            "status": trading_status,
            "ohlcv_stale": ohlcv_stale,
            "ohlcv_gaps": gaps,
        },
        "operational": {
            "status": operational_status,
            "funding_stale": funding_stale,
            "feeds": feeds,
            "dead_feeds": dead_feeds,
        },
        "combined_status": combined,
        "generated_at": (
            now_utc if now_utc is not None else datetime.now(timezone.utc)
        ),
        "exchange": exchange,
        "timeframe": tf,
        "symbols_checked": symbols,
        "summary": summary,
    }


def append_log(report: dict, log_path: Path) -> None:
    """Append satu baris ringkasan ke data/health.log."""
    ts = report["generated_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {report['status']:<5}  {report['summary']}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(line)
