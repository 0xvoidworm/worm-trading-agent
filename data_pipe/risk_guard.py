"""Daily-loss killswitch — risk guard layer (Step 4).

Tujuan: stop spiral. 3 SL berturut dalam sehari = -3% capital. Kalau sistem
lanjut buka trade #4 di hari yang sama, drawdown bisa eksponensial.

Layer ini block ENTRY (BUKAN EXIT) ketika daily_pnl_pct ≤ -killswitch_pct.
EXIT tetap jalan supaya bleeding berhenti, bukan diperpanjang.

Two-file separation (sengaja):
  - data/executor.disabled  — manual maintenance, block ALL (existing).
  - data/risk.killswitch    — risk-triggered, block ENTRY only (modul ini).

Manual reset only — tidak ada auto-reset di UTC midnight. False sense of
security itu lebih bahaya daripada sedikit downtime. Operator wajib eksplisit
review sebelum reset.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .portfolio import (
    DEFAULT_STARTING_CAPITAL,
    _TEST_LIKE_PATTERN,
    _portfolio_cfg,
)
from .storage import Storage


KILLSWITCH_FILE = "risk.killswitch"

DEFAULT_DAILY_LOSS_KILLSWITCH_PCT = 2.0
DEFAULT_DAILY_LOSS_WARN_PCT = 1.0


# ─── time helpers ────────────────────────────────────────────────────────

def get_day_start_utc(now_utc: datetime | None = None) -> datetime:
    """Floor `now_utc` ke 00:00:00 UTC hari ini (naive datetime, UTC semantics).

    Storage timestamps disimpan naive UTC, jadi kita kembalikan naive juga
    supaya comparison di SQL konsisten.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)


# ─── PnL queries ─────────────────────────────────────────────────────────

def get_equity_start_of_day(
    storage: Storage, cfg: dict, now_utc: datetime | None = None,
) -> float:
    """Equity di awal hari UTC = starting_capital + realized PnL trade
    yang CLOSED sebelum midnight (exit_ts < day_start), exclude test artifact.
    """
    pcfg = _portfolio_cfg(cfg)
    starting = float(pcfg.get("starting_capital", DEFAULT_STARTING_CAPITAL))
    day_start = get_day_start_utc(now_utc)
    row = storage._conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL "
        "  AND exit_ts < ? "
        "  AND (thesis IS NULL OR thesis NOT LIKE ?)",
        [day_start, _TEST_LIKE_PATTERN],
    ).fetchone()
    realized_before = float(row[0]) if row else 0.0
    return starting + realized_before


def get_realized_pnl_today(
    storage: Storage, now_utc: datetime | None = None,
) -> float:
    """SUM realized PnL trade yang CLOSED hari ini (exit_ts ≥ day_start),
    exclude test artifact.
    """
    day_start = get_day_start_utc(now_utc)
    row = storage._conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
        "WHERE status='CLOSED' AND pnl IS NOT NULL "
        "  AND exit_ts >= ? "
        "  AND (thesis IS NULL OR thesis NOT LIKE ?)",
        [day_start, _TEST_LIKE_PATTERN],
    ).fetchone()
    return float(row[0]) if row else 0.0


def get_unrealized_pnl_now(storage: Storage, cfg: dict) -> float:
    """SUM mark-to-market PnL atas OPEN trades, exclude test artifact.

    PnL per trade = (latest_close - entry) × size × side
    side: LONG=+1, SHORT=-1. PAIR / entry_price NULL di-skip.

    Latest close di-resolve via cfg.exchange + cfg.timeframe.
    """
    exchange = cfg.get("exchange", "bybit")
    tf = cfg.get("timeframe", "1h")
    rows = storage._conn.execute(
        "SELECT pair, direction, entry_price, size FROM trade_journal "
        "WHERE status='OPEN' AND direction IN ('LONG','SHORT') "
        "  AND entry_price IS NOT NULL AND size IS NOT NULL "
        "  AND (thesis IS NULL OR thesis NOT LIKE ?)",
        [_TEST_LIKE_PATTERN],
    ).fetchall()
    if not rows:
        return 0.0

    total = 0.0
    for pair, direction, entry, size in rows:
        close_row = storage._conn.execute(
            "SELECT close FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? "
            "ORDER BY ts DESC LIMIT 1",
            [exchange, str(pair), tf],
        ).fetchone()
        if close_row is None or close_row[0] is None:
            continue
        last = float(close_row[0])
        side = 1.0 if direction == "LONG" else -1.0
        total += (last - float(entry)) * float(size) * side
    return total


# ─── state composer ──────────────────────────────────────────────────────

def get_daily_pnl_state(
    storage: Storage, cfg: dict, now_utc: datetime | None = None,
) -> dict:
    """Snapshot daily PnL + status klasifikasi. Status:
        OK      : daily_pnl_pct > -warn_pct
        WARN    : -warn_pct ≥ daily_pnl_pct > -killswitch_pct
        TRIGGER : daily_pnl_pct ≤ -killswitch_pct
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    pcfg = _portfolio_cfg(cfg)
    kill_pct = float(pcfg.get(
        "daily_loss_killswitch_pct", DEFAULT_DAILY_LOSS_KILLSWITCH_PCT,
    ))
    warn_pct = float(pcfg.get(
        "daily_loss_warn_pct", DEFAULT_DAILY_LOSS_WARN_PCT,
    ))

    day_start = get_day_start_utc(now_utc)
    equity_start = get_equity_start_of_day(storage, cfg, now_utc)
    realized = get_realized_pnl_today(storage, now_utc)
    unrealized = get_unrealized_pnl_now(storage, cfg)
    daily = realized + unrealized
    pct = (daily / equity_start * 100.0) if equity_start > 0 else 0.0

    if pct <= -kill_pct:
        status = "TRIGGER"
    elif pct <= -warn_pct:
        status = "WARN"
    else:
        status = "OK"

    return {
        "day_start_utc": day_start,
        "equity_start": equity_start,
        "realized_today": realized,
        "unrealized_now": unrealized,
        "daily_pnl": daily,
        "daily_pnl_pct": pct,
        "status": status,
        "threshold_pct": kill_pct,
        "warn_pct": warn_pct,
    }


# ─── killswitch file ─────────────────────────────────────────────────────

def is_killswitch_active(data_dir: Path) -> bool:
    return (data_dir / KILLSWITCH_FILE).exists()


def read_killswitch_reason(data_dir: Path) -> Optional[dict]:
    p = data_dir / KILLSWITCH_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write_killswitch(data_dir: Path, state: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged_at": datetime.now(timezone.utc).isoformat(),
        "reason": "daily_loss",
        "daily_pnl_pct": round(state["daily_pnl_pct"], 4),
        "realized_today": round(state["realized_today"], 4),
        "unrealized_now": round(state["unrealized_now"], 4),
        "equity_start": round(state["equity_start"], 4),
        "threshold_pct": state["threshold_pct"],
        "day_start_utc": state["day_start_utc"].isoformat(),
    }
    (data_dir / KILLSWITCH_FILE).write_text(json.dumps(payload, indent=2))


def remove_killswitch(data_dir: Path) -> Optional[dict]:
    """Delete killswitch file. Return previous reason payload (atau None)."""
    p = data_dir / KILLSWITCH_FILE
    if not p.exists():
        return None
    prev = read_killswitch_reason(data_dir)
    p.unlink()
    return prev


# ─── engagement (called from daemon cycle) ───────────────────────────────

def _emit_risk_signal(
    storage: Storage,
    event_type: str,
    payload: str,
    severity: str,
    day_start: datetime,
    now_utc: datetime | None = None,
) -> bool:
    """Insert risk signal_event. Dedup per (event_type, UTC day) — sekali per
    engagement per hari. Payload-variance (e.g. -5.77% vs -7.19% di hari yang
    sama) sengaja TIDAK ikut dalam hash supaya operator tidak spam.

    `now_utc` optional — backtest replay supaya signal ts konsisten dengan
    cycle clock (bukan wall clock). Default wall clock untuk production.

    Return True kalau row baru di-insert (False = duplicate sudah ada).
    """
    seed = f"{event_type}|{day_start.date().isoformat()}"
    sig_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    # Cek apakah sudah ada
    exist = storage._conn.execute(
        "SELECT 1 FROM signal_events WHERE id=?", [sig_id],
    ).fetchone()
    if exist:
        return False
    if now_utc is None:
        ts = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        ts = now_utc
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    storage.upsert_signals([{
        "id": sig_id,
        "ts": ts,
        "event_type": event_type,
        "symbol": None,
        "severity": severity,
        "payload": payload,
    }])
    return True


def check_and_engage(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    now_utc: datetime | None = None,
) -> dict:
    """Evaluate daily PnL. Engage killswitch + emit signal kalau TRIGGER.
    Emit RISK_WARN sekali kalau WARN. Idempotent (no double-write same day).

    Return dict: state + 'engaged' bool + 'signal_emitted' bool.
    """
    state = get_daily_pnl_state(storage, cfg, now_utc)
    engaged = False
    signal_emitted = False

    if state["status"] == "TRIGGER":
        # Tulis file kalau belum ada (idempotent — file persist sampai reset)
        if not is_killswitch_active(data_dir):
            _write_killswitch(data_dir, state)
            engaged = True
        # Emit signal sekali per hari
        payload = (
            f"daily_pnl_pct={state['daily_pnl_pct']:.2f} "
            f"≤ -{state['threshold_pct']:.2f} "
            f"realized={state['realized_today']:.2f} "
            f"unrealized={state['unrealized_now']:.2f} "
            f"equity_start={state['equity_start']:.2f}"
        )
        signal_emitted = _emit_risk_signal(
            storage, "RISK_KILLSWITCH", payload,
            "critical", state["day_start_utc"], now_utc=now_utc,
        )
    elif state["status"] == "WARN":
        payload = (
            f"daily_pnl_pct={state['daily_pnl_pct']:.2f} "
            f"≤ -{state['warn_pct']:.2f} (warn level) "
            f"realized={state['realized_today']:.2f} "
            f"unrealized={state['unrealized_now']:.2f}"
        )
        signal_emitted = _emit_risk_signal(
            storage, "RISK_WARN", payload,
            "warn", state["day_start_utc"], now_utc=now_utc,
        )

    state["engaged"] = engaged
    state["signal_emitted"] = signal_emitted
    return state
