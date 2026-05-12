"""Pilot Executor — semi-otonom trade opener & closer.

Dua path verdict:
  ENTRY → buka trade baru (paper). Gates fail-closed (urut):
    1.   Kill switch manual `data/executor.disabled` tidak ada
    2.   `data/advisor.latest.json` ada DAN umurnya < VERDICT_MAX_AGE_MIN
    0.5  Risk killswitch `data/risk.killswitch` tidak ada (ENTRY-only)
    3.   health status = OK (re-check langsung, tidak percaya verdict saja)
    4.   signal_id valid, confidence ≥ CONFIDENCE_THRESHOLD
    5.   Dedup: signal_id belum pernah dilink ke trade apa pun
    6.   Per-symbol cap: tidak boleh ada OPEN trade di pair yang sama
    8.   Exposure recheck (3 caps) di execution time

  EXIT  → tutup trade existing. Gates fail-closed (urut):
    1. Kill switch manual (NB: risk killswitch TIDAK block EXIT — bleeding stop)
    2. Verdict age ≤ VERDICT_MAX_AGE_MIN
    3. Health = OK (re-check)
    4. trade_id ada di verdict
    5. Trade lookup di journal: must exist AND status="OPEN"
    6. Dedup 5-min: tidak ada CLOSE sukses utk trade_id sama dlm 5 menit log

Output: `data/executor.latest.json` (untuk TUI) + append `data/executor.log`.
Action enum: SKIP / OPEN / CLOSE / ERROR.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .advisor import read_latest_verdict
from .auto_advisor import parse_confidence
from .health import run_health_check
from .portfolio import get_account_state
from .risk_guard import is_killswitch_active, read_killswitch_reason
from .storage import Storage


CONFIDENCE_THRESHOLD = 8.0      # t-stat REGIME; aligned dengan auto_advisor
VERDICT_MAX_AGE_MIN = 15        # di atas ini verdict dianggap basi
KILL_SWITCH = "executor.disabled"
DEFAULT_SIZE = 1.0
EXIT_DEDUP_MINUTES = 5          # window dedup CLOSE log → anti double-exit


def _persist(result: dict, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "executor.latest.json").write_text(
        json.dumps(result, indent=2, default=str)
    )
    ts = result["timestamp"][:19].replace("T", " ")
    conf = result.get("confidence")
    conf_s = f" conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
    tid = f" #{result['trade_id']}" if result.get("trade_id") else ""
    line = (
        f"[{ts} UTC] {result['action']:<5}{tid}{conf_s}  "
        f"{result['reason']}\n"
    )
    with (data_dir / "executor.log").open("a") as f:
        f.write(line)


def _skip(reason: str, **extra: Any) -> dict:
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "SKIP",
        "reason": reason,
        "trade_id": None,
        "signal_id": None,
        "confidence": None,
    }
    result.update(extra)
    return result


_CLOSE_LINE_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s+CLOSE\s+#(\d+)\b"
)


def _recent_close_for_trade(
    data_dir: Path, trade_id: int, within_min: int = EXIT_DEDUP_MINUTES,
) -> bool:
    """True jika executor.log punya CLOSE untuk trade_id ini dlm `within_min` menit.

    Defensive gate untuk skenario race: dua advisor cycle overlap → keduanya
    propose EXIT untuk trade yg sama sebelum status DB sempat CLOSED. Status
    check (gate #5) masih primary defense; gate ini complementary.
    """
    log = data_dir / "executor.log"
    if not log.exists():
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_min)
    try:
        with log.open() as f:
            lines = f.readlines()[-500:]
    except Exception:
        return False
    for line in reversed(lines):
        m = _CLOSE_LINE_RE.match(line)
        if not m:
            continue
        if int(m.group(2)) != trade_id:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            continue
        if ts >= cutoff:
            return True
    return False


def _exposure_recheck(
    storage: Storage, cfg: dict, size: float, entry_price: float,
) -> str | None:
    """Re-evaluate caps SAAT execution. Return reason string kalau gagal."""
    state = get_account_state(storage, cfg)
    notional = abs(size * entry_price)
    max_pos_notional = state["equity"] * state["max_position_pct"] / 100.0
    if notional > max_pos_notional:
        return (
            f"exposure recheck: notional {notional:.2f} > "
            f"max_position {max_pos_notional:.2f}"
        )
    new_gross = state["gross_notional"] + notional
    max_gross_notional = state["equity"] * state["max_gross_pct"] / 100.0
    if new_gross > max_gross_notional:
        return (
            f"exposure recheck: gross would be {new_gross:.2f} > "
            f"{max_gross_notional:.2f}"
        )
    if state["n_open"] + 1 > state["max_concurrent"]:
        return (
            f"exposure recheck: open count would be "
            f"{state['n_open'] + 1} > {state['max_concurrent']}"
        )
    return None


def _latest_close_price(storage: Storage, exchange: str, symbol: str, tf: str):
    row = storage._conn.execute(
        "SELECT close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? "
        "ORDER BY ts DESC LIMIT 1",
        [exchange, symbol, tf],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


# ─── ENTRY path ───────────────────────────────────────────────────────────

def _run_entry(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    verdict: dict,
    threshold: float,
    now_utc=None,
) -> dict:
    proposed = verdict.get("proposed") or {}
    pair = proposed.get("pair")
    direction = proposed.get("direction")
    sig_id = proposed.get("signal_id")
    if not pair or not direction or sig_id is None:
        return _skip("ENTRY verdict missing pair/direction/signal_id")

    # Gate #0.5 — risk killswitch (ENTRY-only). Manual `executor.disabled`
    # ditangani di gate #1 (block ALL); modul ini block ENTRY saja supaya
    # EXIT bisa tetap menutup posisi rugi.
    if is_killswitch_active(data_dir):
        info = read_killswitch_reason(data_dir) or {}
        pct = info.get("daily_pnl_pct")
        pct_s = f" daily={pct:.2f}%" if isinstance(pct, (int, float)) else ""
        return _skip(
            f"risk_killswitch_active: {info.get('reason', 'unknown')}{pct_s}",
            signal_id=sig_id,
        )

    health = run_health_check(storage, cfg, now_utc=now_utc)
    if health["status"] != "OK":
        return _skip(f"health={health['status']}", signal_id=sig_id)

    sig = storage.get_signal_by_num(int(sig_id))
    if sig is None:
        return _skip(f"signal #{sig_id} not found", signal_id=sig_id)

    confidence = parse_confidence(sig.get("payload"), sig.get("event_type"))
    if confidence is None:
        return _skip(
            f"unparseable confidence for SIG#{sig_id}", signal_id=sig_id,
        )
    if confidence < threshold:
        return _skip(
            f"confidence {confidence:.2f} < {threshold:.2f}",
            signal_id=sig_id, confidence=confidence,
        )

    linked = storage.get_trades_by_signal(int(sig_id))
    if not linked.empty:
        return _skip(
            f"SIG#{sig_id} sudah punya trade (dedup)",
            signal_id=sig_id, confidence=confidence,
        )

    open_df = storage.get_trades(status="OPEN")
    if not open_df.empty and pair in open_df["pair"].astype(str).tolist():
        return _skip(
            f"already OPEN trade on {pair}",
            signal_id=sig_id, confidence=confidence,
        )

    entry_price = proposed.get("entry_price")
    size = proposed.get("size_hint") or DEFAULT_SIZE
    thesis = (verdict.get("thesis") or "")[:200]

    # Gate #8 — exposure recheck DI EXECUTION TIME (jangan percaya
    # snapshot advisor: trade lain bisa terbuka di antara verdict & execute
    # kalau ada race). Pakai size dari proposed langsung — kalau kita
    # re-calc, decision boundary bisa berbeda dari verdict, bikin debugging
    # ngeselin. Lebih baik SKIP dengan reason transparent kalau racing.
    if entry_price is not None and size:
        recheck_skip = _exposure_recheck(storage, cfg, float(size), float(entry_price))
        if recheck_skip:
            return _skip(recheck_skip, signal_id=sig_id, confidence=confidence)

    trade_id = storage.add_trade(
        pair=pair,
        direction=direction,
        entry_price=entry_price,
        size=size,
        thesis=thesis,
        signal_id=int(sig_id),
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "OPEN",
        "reason": f"opened #{trade_id} {pair} {direction} @ {entry_price}",
        "trade_id": int(trade_id),
        "signal_id": int(sig_id),
        "confidence": float(confidence),
        "pair": pair,
        "direction": direction,
        "entry_price": entry_price,
        "size": size,
    }


# ─── EXIT path ────────────────────────────────────────────────────────────

def _run_exit(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    verdict: dict,
    now_utc=None,
) -> dict:
    # Gate #4 — trade_id ada
    trade_id = verdict.get("trade_id")
    if trade_id is None:
        return _skip("EXIT verdict missing trade_id")
    try:
        trade_id = int(trade_id)
    except (TypeError, ValueError):
        return _skip(f"EXIT verdict trade_id invalid: {trade_id!r}")

    # Gate #3 — health (re-check)
    health = run_health_check(storage, cfg, now_utc=now_utc)
    if health["status"] != "OK":
        return _skip(f"health={health['status']}", trade_id=trade_id)

    # Gate #5 — trade lookup, status OPEN
    row = storage._conn.execute(
        "SELECT id, pair, status FROM trade_journal WHERE id=?",
        [trade_id],
    ).fetchone()
    if row is None:
        return _skip(f"trade #{trade_id} not found", trade_id=trade_id)
    _id, pair, status = row
    if status != "OPEN":
        return _skip(
            f"trade #{trade_id} no longer OPEN (status={status})",
            trade_id=trade_id,
        )

    # Gate #6 — 5-min dedup against executor.log
    if _recent_close_for_trade(data_dir, trade_id):
        return _skip(
            f"recent CLOSE for #{trade_id} in last {EXIT_DEDUP_MINUTES}min (dedup)",
            trade_id=trade_id,
        )

    # Lolos semua → close trade pakai latest close price
    exchange = cfg.get("exchange", "bybit")
    tf = cfg.get("timeframe", "1h")
    exit_price = _latest_close_price(storage, exchange, str(pair), tf)
    if exit_price is None:
        return _skip(
            f"no OHLCV price for {pair} → cannot price exit",
            trade_id=trade_id,
        )

    notes = (verdict.get("thesis") or "")[:200]
    try:
        result = storage.close_trade(
            trade_id=trade_id,
            exit_price=exit_price,
            notes=notes,
        )
    except ValueError as e:
        # race: someone else closed it between our check and update
        return _skip(f"close_trade race: {e}", trade_id=trade_id)

    pnl = result.get("pnl")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "CLOSE",
        "reason": (
            f"closed #{trade_id} {pair} @ {exit_price} "
            f"pnl={pnl:+.2f}" if pnl is not None
            else f"closed #{trade_id} {pair} @ {exit_price} pnl=n/a"
        ),
        "trade_id": int(trade_id),
        "signal_id": None,
        "confidence": None,
        "pair": pair,
        "exit_price": exit_price,
        "pnl": pnl,
    }


# ─── dispatcher ───────────────────────────────────────────────────────────

def run_executor(
    storage: Storage,
    cfg: dict,
    data_dir: Path,
    *,
    threshold: float = CONFIDENCE_THRESHOLD,
    max_age_min: int = VERDICT_MAX_AGE_MIN,
    now_utc=None,
) -> dict:
    """Satu eksekusi tick. Persist hasil. Return result dict.

    `now_utc`: optional override untuk replay/backtest. Production lewatkan
    `None` → freshness gate pakai `datetime.now()`. Backtest pass cycle ts
    supaya gate #2 (verdict age) konsisten dengan `generated_at` cycle ts.
    """
    # Gate #1 — kill switch (universal)
    if (data_dir / KILL_SWITCH).exists():
        result = _skip(f"killswitch: {KILL_SWITCH} present")
        _persist(result, data_dir)
        return result

    # Load verdict
    payload = read_latest_verdict(data_dir)
    if payload is None:
        result = _skip("no advisor verdict yet")
        _persist(result, data_dir)
        return result

    try:
        gen = datetime.fromisoformat(payload["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
    except Exception as e:
        result = _skip(f"unparseable verdict timestamp: {e}")
        _persist(result, data_dir)
        return result

    # Gate #2 — verdict freshness (universal)
    eval_now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if getattr(eval_now, "tzinfo", None) is None:
        eval_now = eval_now.replace(tzinfo=timezone.utc)
    age_min = (eval_now - gen).total_seconds() / 60
    if age_min > max_age_min:
        result = _skip(f"verdict stale ({age_min:.1f}m > {max_age_min}m)")
        _persist(result, data_dir)
        return result

    verdict = payload.get("verdict") or {}
    v_type = verdict.get("verdict")

    if v_type == "ENTRY":
        result = _run_entry(storage, cfg, data_dir, verdict, threshold, now_utc=now_utc)
    elif v_type == "EXIT":
        result = _run_exit(storage, cfg, data_dir, verdict, now_utc=now_utc)
    else:
        result = _skip(f"verdict={v_type} (not ENTRY/EXIT)")

    _persist(result, data_dir)
    return result


def read_latest_execution(data_dir: Path) -> dict | None:
    p = data_dir / "executor.latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None
