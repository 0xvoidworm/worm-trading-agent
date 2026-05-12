"""Advisor Layer — gather market context for LLM, persist verdicts.

Dua entry utama:
  build_context(storage, cfg) -> dict
    Return paket data siap dikonsumsi LLM: health snapshot, 3 sinyal terbaru,
    open trades, berita relevan terhadap symbol-symbol di sinyal.

  apply_verdict(verdict, context, data_dir) -> Path
    Tulis verdict + context ke `data/advisor.latest.json` (untuk TUI) dan
    append satu baris ringkas ke `data/advisor.log`.

Schema verdict (dari LLM):
  {
    "summary": "Bullish" | "Bearish" | "Chaos",
    "verdict": "WAIT" | "ENTRY" | "EXIT",
    "thesis": "<=200 char penjelasan",
    "trade_id": <int, optional — untuk EXIT>,
    "proposed": {                       # optional — untuk ENTRY
        "pair": "SOL/USDT",
        "direction": "LONG|SHORT|PAIR",
        "signal_id": 385,
        "entry_price": 145.2,
        "size_hint": 10
    }
  }
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .health import run_health_check
from .storage import Storage


RECENT_SIGNALS = 3
RECENT_NEWS = 5
VALID_SUMMARY = {"Bullish", "Bearish", "Chaos"}
VALID_VERDICT = {"WAIT", "ENTRY", "EXIT"}


def _extract_tickers(symbol_field) -> list[str]:
    """'SOL/USDT' → ['SOL']; 'SOL/USDT|ETH/USDT' → ['SOL','ETH']; None → [].

    Defensive: accept any value (str/None/NaN/pd.NA/numeric). Non-string atau
    pandas-NaN sentinel di-treat sebagai "no ticker" (silent degrade) supaya
    advisor cycle tidak crash kalau signal punya `symbol=NULL` (e.g. risk
    signals RISK_KILLSWITCH/RISK_WARN/RISK_RESET pasca Step 4).

    Filosofi sama dengan resilience layer: fail soft, return [], biarkan
    caller skip/fallback.
    """
    if symbol_field is None:
        return []
    # pandas/numpy NaN: float('nan') != float('nan'); also catches np.nan.
    # Use try/except untuk pd.NA (boolean ambiguous) dan untyped non-string.
    if not isinstance(symbol_field, str):
        try:
            # NaN check tanpa import pandas (works for math.nan & np.nan)
            if symbol_field != symbol_field:  # noqa: PLR0124  # NaN-only true
                return []
        except Exception:
            pass
        return []
    if not symbol_field.strip():
        return []
    tickers = []
    for part in symbol_field.split("|"):
        base = part.split("/")[0].strip().upper()
        if base and base not in tickers:
            tickers.append(base)
    return tickers


def _serialize_df(df: pd.DataFrame) -> list[dict]:
    """DataFrame → JSON-safe list-of-dict (ts jadi ISO string)."""
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        row = {}
        for k, v in r.items():
            if pd.isna(v):
                row[k] = None
            elif isinstance(v, (pd.Timestamp, datetime)):
                row[k] = pd.Timestamp(v).isoformat()
            elif hasattr(v, "item"):
                row[k] = v.item()  # numpy scalars → native
            else:
                row[k] = v
        out.append(row)
    return out


def build_context(storage: Storage, cfg: dict, *, now_utc=None) -> dict:
    """Agregasi data untuk dikonsumsi LLM advisor.

    Return dict dengan:
      health        : output run_health_check (status + detail)
      signals       : 3 sinyal terbaru (list of dict, urut terbaru dulu)
      open_trades   : semua trade status=OPEN
      news          : ≤5 berita terbaru yang simbol-nya overlap dengan
                      ticker-ticker di `signals`. Kalau tidak ada overlap,
                      jatuh balik ke 5 headline terbaru apa pun.
      symbols_of_interest : list ticker hasil agregasi dari sinyal
    """
    health = run_health_check(storage, cfg, now_utc=now_utc)

    sig_df = storage.get_signals(limit=RECENT_SIGNALS)
    # get_signals returns oldest-first when limit is set (per tail-then-reverse);
    # kita mau terbaru di atas utk LLM.
    if not sig_df.empty:
        sig_df = sig_df.sort_values("ts", ascending=False).reset_index(drop=True)

    tickers: list[str] = []
    for _, r in sig_df.iterrows():
        for t in _extract_tickers(r.get("symbol")):
            if t not in tickers:
                tickers.append(t)

    open_trades_df = storage.get_trades(status="OPEN")

    # News: filter where symbols column contains any of our tickers (CSV string).
    if tickers:
        ph = ",".join("?" * len(tickers))
        # Build OR of INSTR matches; DuckDB supports `contains(symbols, ?)`.
        sql = (
            "SELECT published_at, source, title, url, symbols FROM news "
            f"WHERE {' OR '.join(['contains(symbols, ?)'] * len(tickers))} "
            "ORDER BY published_at DESC LIMIT ?"
        )
        news_df = storage._conn.execute(sql, [*tickers, RECENT_NEWS]).fetchdf()
    else:
        news_df = pd.DataFrame()

    # Fallback: kalau tidak ada news terpanggil, ambil 5 headline terbaru saja.
    if news_df.empty:
        news_df = storage.get_news(limit=RECENT_NEWS)

    eff_now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if getattr(eff_now, "tzinfo", None) is None:
        eff_now = eff_now.replace(tzinfo=timezone.utc)
    return {
        "generated_at": eff_now.isoformat(),
        "exchange": cfg.get("exchange"),
        "timeframe": cfg.get("timeframe"),
        "health": {
            "status": health["status"],
            "summary": health["summary"],
            "ohlcv_stale": health["ohlcv_stale"],
            "ohlcv_gaps": health["ohlcv_gaps"],
            "feeds": health["feeds"],
        },
        "signals": _serialize_df(sig_df),
        "open_trades": _serialize_df(open_trades_df),
        "news": _serialize_df(news_df),
        "symbols_of_interest": tickers,
    }


def validate_verdict(verdict: dict) -> None:
    """Raise ValueError kalau schema verdict invalid."""
    if not isinstance(verdict, dict):
        raise ValueError("verdict must be a dict")
    for k in ("summary", "verdict", "thesis"):
        if k not in verdict:
            raise ValueError(f"missing key: {k}")
    if verdict["summary"] not in VALID_SUMMARY:
        raise ValueError(f"summary must be one of {VALID_SUMMARY}")
    if verdict["verdict"] not in VALID_VERDICT:
        raise ValueError(f"verdict must be one of {VALID_VERDICT}")
    if not isinstance(verdict["thesis"], str) or not verdict["thesis"].strip():
        raise ValueError("thesis must be a non-empty string")
    if verdict["verdict"] == "EXIT" and "trade_id" not in verdict:
        raise ValueError("EXIT verdict requires trade_id")


def apply_verdict(
    verdict: dict, context: dict, data_dir: Path, *, now_utc=None,
) -> Path:
    """Persist verdict + context snapshot. Return path ke advisor.latest.json.

    `now_utc` optional — backtest replay supaya `generated_at` align dengan
    cycle clock. Default wall clock untuk production daemon.
    """
    validate_verdict(verdict)
    data_dir.mkdir(parents=True, exist_ok=True)

    eff_now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if getattr(eff_now, "tzinfo", None) is None:
        eff_now = eff_now.replace(tzinfo=timezone.utc)

    payload = {
        "generated_at": eff_now.isoformat(),
        "verdict": verdict,
        "context_snapshot": {
            "health_status": context.get("health", {}).get("status"),
            "n_signals": len(context.get("signals", [])),
            "n_open_trades": len(context.get("open_trades", [])),
            "symbols_of_interest": context.get("symbols_of_interest", []),
        },
    }
    latest = data_dir / "advisor.latest.json"
    latest.write_text(json.dumps(payload, indent=2))

    log = data_dir / "advisor.log"
    ts = eff_now.strftime("%Y-%m-%d %H:%M:%S UTC")
    line = (
        f"[{ts}] {verdict['summary']:<8} {verdict['verdict']:<5}  "
        f"{verdict['thesis'][:140]}\n"
    )
    with log.open("a") as f:
        f.write(line)

    return latest


def read_latest_verdict(data_dir: Path) -> dict | None:
    """Baca advisor.latest.json. Return None kalau belum pernah ada verdict."""
    p = data_dir / "advisor.latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None
