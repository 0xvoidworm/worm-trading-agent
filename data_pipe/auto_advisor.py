"""Heuristic auto-advisor — deterministic verdict dari sinyal segar.

Dipakai daemon supaya setiap cycle bisa "mikir" tanpa panggil LLM eksternal.
MVP fokus pada REGIME_CHANGE (paling robust signal): t-stat ≥ threshold.

Step 14B: opt-in FUNDING_FLIP-only mode via cfg `advisor.detector_whitelist`.
Direction mapping (per Step 13B D3 evidence):
    curr > 0 → LONG  (bullish flip; positive funding favors longs paying)
    curr < 0 → SHORT (bearish flip; negative funding favors shorts paying)
Dominant-band filter |curr| ∈ [band_min, band_max) — Step 13 D3 menunjukkan
edge concentrated di band [0.01%, 0.03%) (n=28, hit 0.71/0.68/0.61 di h=1/4/24).

Return schema sama dengan `data_pipe.advisor.validate_verdict`:
    {"summary": Bullish|Bearish|Chaos,
     "verdict": WAIT|ENTRY|EXIT,
     "thesis": str,
     "proposed": {...}}   # hanya untuk ENTRY
"""
from __future__ import annotations

import re
from typing import Optional

from .storage import Storage


# Default threshold — t-stat regime shift. User requirement: ">8.0".
REGIME_T_THRESHOLD = 8.0
# Window pencarian sinyal kandidat (jam). Sinyal di luar window dianggap basi.
CANDIDATE_WINDOW_HOURS = 4

# Step 14B — detector dispatch
DEFAULT_DETECTOR_WHITELIST = ["REGIME_CHANGE"]
# Dominant band per Step 13B D3: |curr_decimal| in [0.0001, 0.0003) i.e. 0.01% to <0.03%
DEFAULT_FUNDING_FLIP_BAND_MIN = 0.0001
DEFAULT_FUNDING_FLIP_BAND_MAX = 0.0003

_FFLIP_PAYLOAD_RE = re.compile(r"curr=([+-]?[\d.]+)%")


def parse_confidence(payload: str | None, event_type: str | None) -> Optional[float]:
    """Extract skor kepercayaan (absolute) dari payload string.

    REGIME_CHANGE: pola 't=<float>'                    → |t|
    Z_SPIKE:       pola 'z=<float>'                    → |z|
    FUNDING_FLIP:  pola 'curr=<float>%' (Step 14B)     → |curr_decimal|
    Lain:          cari 't=' atau 'z=' sebagai fallback
    """
    if not payload:
        return None
    if event_type == "REGIME_CHANGE":
        m = re.search(r"\bt=(-?[\d.]+)", payload)
    elif event_type == "Z_SPIKE":
        m = re.search(r"\bz=([+-]?[\d.]+)", payload)
    elif event_type == "FUNDING_FLIP":
        # Strength = |curr| in DECIMAL units. Aligns dengan band-filter di
        # _run_funding_flip_advisor; executor gate `confidence ≥ threshold`
        # bekerja kalau threshold = band_min (~0.0001).
        return _funding_flip_strength(payload)
    else:
        m = re.search(r"\b[tz]=([+-]?[\d.]+)", payload)
    if not m:
        return None
    try:
        return abs(float(m.group(1)))
    except ValueError:
        return None


def _regime_direction(payload: str) -> str | None:
    """REGIME_CHANGE payload → LONG / SHORT / None (filter trending only)."""
    if "to=trending_up" in payload:
        return "LONG"
    if "to=trending_down" in payload:
        return "SHORT"
    return None


def _funding_flip_curr_decimal(payload: str | None) -> float | None:
    """Extract `curr` from FUNDING_FLIP payload, return as DECIMAL (not %).

    Payload format (signal_log.detect_funding_events):
        "prev=<X>%;curr=<Y>%"  where X,Y already multiplied by 100.
    Return None if unparseable.
    """
    if not payload:
        return None
    m = _FFLIP_PAYLOAD_RE.search(payload)
    if not m:
        return None
    try:
        return float(m.group(1)) / 100.0
    except ValueError:
        return None


def _funding_flip_direction(payload: str | None) -> str | None:
    """FUNDING_FLIP → LONG (curr>0) / SHORT (curr<0). Step 13B D3 evidence."""
    curr = _funding_flip_curr_decimal(payload)
    if curr is None:
        return None
    if curr > 0:
        return "LONG"
    if curr < 0:
        return "SHORT"
    return None


def _funding_flip_strength(payload: str | None) -> float | None:
    """Strength = |curr| in DECIMAL units. Used for dominant-band gating."""
    curr = _funding_flip_curr_decimal(payload)
    return None if curr is None else abs(curr)


def _latest_close(storage: Storage, exchange: str, symbol: str, tf: str) -> Optional[float]:
    row = storage._conn.execute(
        "SELECT close FROM ohlcv "
        "WHERE exchange=? AND symbol=? AND timeframe=? "
        "ORDER BY ts DESC LIMIT 1",
        [exchange, symbol, tf],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _propose_entry(
    storage: Storage,
    cfg: dict,
    *,
    best: dict,
    summary: str,
    detector_label: str,
    threshold_repr: str,
    confidence_fmt: str,
) -> dict:
    """Common tail: latest_close → sizing → ENTRY/WAIT verdict assembly.

    Shared by REGIME_CHANGE and FUNDING_FLIP entry paths (Step 14B).
    """
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]
    entry_price = _latest_close(storage, exchange, best["symbol"], tf)

    from .portfolio import get_account_state
    from .sizing import DEFAULT_SL_PCT, calc_position_size

    state = get_account_state(storage, cfg)
    sl_pct = float((cfg.get("exits") or {}).get("stop_loss_pct", DEFAULT_SL_PCT))
    sizing = calc_position_size(
        entry_price=entry_price,
        sl_pct=sl_pct,
        direction=best["direction"],
        equity=state["equity"],
        current_exposure={
            "count": state["n_open"],
            "gross_notional": state["gross_notional"],
        },
        cfg=cfg,
    )

    if sizing["size"] is None:
        return {
            "summary": summary,
            "verdict": "WAIT",
            "thesis": (
                f"WAIT: sizing blocked ({sizing['reason_skip']}). "
                f"candidate={best['symbol']} {best['direction']} "
                f"conf={best['confidence']:{confidence_fmt}}. bias: {summary}."
            )[:200],
        }

    thesis = (
        f"{detector_label} SIG#{best['sig_num']} {best['symbol']} "
        f"{best['direction']} conf={best['confidence']:{confidence_fmt}} {threshold_repr}. "
        f"size={sizing['size']:g} notional={sizing['notional']:.0f}. "
        f"bias: {summary}."
    )[:200]

    return {
        "summary": summary,
        "verdict": "ENTRY",
        "thesis": thesis,
        "proposed": {
            "pair": best["symbol"],
            "direction": best["direction"],
            "signal_id": best["sig_num"],
            "entry_price": entry_price,
            "size_hint": sizing["size"],
            "notional": sizing["notional"],
            "risk_amount": sizing["risk_amount"],
        },
    }


def _filter_fresh(
    storage: Storage, candidates: list[dict],
) -> tuple[list[dict], set[str]]:
    """Dedup: skip signal yang sudah link ke trade ATAU symbol yang sudah OPEN."""
    open_trades = storage.get_trades(status="OPEN")
    open_pairs = (
        set(open_trades["pair"].astype(str).tolist())
        if not open_trades.empty else set()
    )
    fresh: list[dict] = []
    for c in candidates:
        linked = storage.get_trades_by_signal(c["sig_num"])
        if not linked.empty:
            continue
        if c["symbol"] in open_pairs:
            continue
        fresh.append(c)
    return fresh, open_pairs


def _summary_from_dirs(all_dirs: list[Optional[str]]) -> str:
    up = sum(1 for d in all_dirs if d == "LONG")
    down = sum(1 for d in all_dirs if d == "SHORT")
    if up >= down * 2 and up > 0:
        return "Bullish"
    if down >= up * 2 and down > 0:
        return "Bearish"
    return "Chaos"


def _run_regime_advisor(
    storage: Storage, cfg: dict, threshold: float, window_hours: int,
) -> dict:
    """REGIME_CHANGE entry path — pre-Step-14B logic, preserved verbatim."""
    sig_df = storage.get_signals(
        limit=100, event_type="REGIME_CHANGE", since_hours=window_hours,
    )

    if sig_df.empty:
        return {
            "summary": "Chaos",
            "verdict": "WAIT",
            "thesis": f"No fresh REGIME_CHANGE signals in last {window_hours}h — no conviction.",
        }

    candidates: list[dict] = []
    for _, s in sig_df.iterrows():
        conf = parse_confidence(s.get("payload"), s.get("event_type"))
        direction = _regime_direction(str(s.get("payload") or ""))
        if conf is None or direction is None:
            continue
        if conf < threshold:
            continue
        candidates.append({
            "sig_num": int(s["sig_num"]),
            "symbol": s["symbol"],
            "direction": direction,
            "confidence": float(conf),
        })

    all_dirs = [
        _regime_direction(str(s.get("payload") or ""))
        for _, s in sig_df.iterrows()
    ]
    summary = _summary_from_dirs(all_dirs)

    fresh, _ = _filter_fresh(storage, candidates)
    if not fresh:
        return {
            "summary": summary,
            "verdict": "WAIT",
            "thesis": (
                f"{len(candidates)} signals ≥t={threshold}, "
                f"semuanya sudah dipakai atau pair sudah OPEN. "
                f"market bias: {summary}."
            )[:200],
        }

    fresh.sort(
        key=lambda c: (c["confidence"], c["sig_num"]),
        reverse=True,
    )
    best = fresh[0]
    return _propose_entry(
        storage, cfg,
        best=best, summary=summary,
        detector_label="REGIME_CHANGE",
        threshold_repr=f"≥ {threshold}",
        confidence_fmt=".2f",
    )


def _run_funding_flip_advisor(
    storage: Storage, cfg: dict, advisor_cfg: dict, window_hours: int,
) -> dict:
    """Step 14B — FUNDING_FLIP entry path with dominant-band gating.

    Direction: curr>0→LONG, curr<0→SHORT (Step 13B D3 confirmed sign-aligned
    positive returns h=1/4/24 in dominant band).

    Filter: |curr_decimal| ∈ [band_min, band_max). Default = [0.0001, 0.0003)
    matches Step 13 D3 dominant band [0.01%, 0.03%) where n=28 hit-rate
    0.71/0.68/0.61. Outside this band: small-n outliers, NOT included.
    """
    ff_cfg = advisor_cfg.get("funding_flip") or {}
    band_min = float(ff_cfg.get("band_min", DEFAULT_FUNDING_FLIP_BAND_MIN))
    band_max = float(ff_cfg.get("band_max", DEFAULT_FUNDING_FLIP_BAND_MAX))

    sig_df = storage.get_signals(
        limit=100, event_type="FUNDING_FLIP", since_hours=window_hours,
    )

    if sig_df.empty:
        return {
            "summary": "Chaos",
            "verdict": "WAIT",
            "thesis": f"No fresh FUNDING_FLIP signals in last {window_hours}h — no conviction.",
        }

    candidates: list[dict] = []
    for _, s in sig_df.iterrows():
        payload = str(s.get("payload") or "")
        direction = _funding_flip_direction(payload)
        strength = _funding_flip_strength(payload)
        if direction is None or strength is None:
            continue
        # Dominant-band filter — half-open interval [min, max)
        if not (band_min <= strength < band_max):
            continue
        candidates.append({
            "sig_num": int(s["sig_num"]),
            "symbol": s["symbol"],
            "direction": direction,
            "confidence": float(strength),
        })

    all_dirs = [
        _funding_flip_direction(str(s.get("payload") or ""))
        for _, s in sig_df.iterrows()
    ]
    summary = _summary_from_dirs(all_dirs)

    fresh, _ = _filter_fresh(storage, candidates)
    if not fresh:
        return {
            "summary": summary,
            "verdict": "WAIT",
            "thesis": (
                f"{len(candidates)} FUNDING_FLIP signals lolos band "
                f"[{band_min*100:.3f}%, {band_max*100:.3f}%), "
                f"semuanya sudah dipakai atau pair sudah OPEN. "
                f"market bias: {summary}."
            )[:200],
        }

    fresh.sort(
        key=lambda c: (c["confidence"], c["sig_num"]),
        reverse=True,
    )
    best = fresh[0]
    return _propose_entry(
        storage, cfg,
        best=best, summary=summary,
        detector_label="FUNDING_FLIP",
        threshold_repr=f"in [{band_min*100:.3f}%,{band_max*100:.3f}%)",
        confidence_fmt=".5f",
    )


def run_auto_advisor(
    storage: Storage,
    cfg: dict,
    *,
    threshold: float = REGIME_T_THRESHOLD,
    window_hours: int = CANDIDATE_WINDOW_HOURS,
) -> dict:
    """Evaluasi sinyal segar → verdict dict (validator-compatible).

    Step 14B: detector dispatch via cfg["advisor"]["detector_whitelist"].
    Default `["REGIME_CHANGE"]` preserves pre-14B behavior. Set to
    `["FUNDING_FLIP"]` to enable Step 14B paper backtest mode.

    Logic per branch:
      REGIME_CHANGE — t-stat ≥ threshold + sign filter (trending_up/down).
      FUNDING_FLIP  — |curr_decimal| ∈ [band_min, band_max) + sign-of-curr.

    Common tail (both branches):
      - Market summary from sig_df direction tally.
      - Dedup: skip signals already linked to trade or pair already OPEN.
      - Pick highest-confidence fresh candidate → sizing → ENTRY proposal.
    """
    advisor_cfg = cfg.get("advisor") or {}
    whitelist = advisor_cfg.get(
        "detector_whitelist", DEFAULT_DETECTOR_WHITELIST,
    )

    if whitelist == ["FUNDING_FLIP"]:
        return _run_funding_flip_advisor(
            storage, cfg, advisor_cfg, window_hours,
        )
    # Default — REGIME_CHANGE (also covers explicit ["REGIME_CHANGE"]).
    return _run_regime_advisor(storage, cfg, threshold, window_hours)


def run_advisor(
    storage: Storage,
    cfg: dict,
    *,
    threshold: float = REGIME_T_THRESHOLD,
    window_hours: int = CANDIDATE_WINDOW_HOURS,
    now_utc=None,
) -> dict:
    """Chained advisor: EXIT first, ENTRY fallback.

    Cek semua OPEN trade dulu via auto_exit.check_exits — kalau ada trigger
    fire, return EXIT verdict (close lebih prioritas dari open). Else jalanin
    ENTRY logic existing via run_auto_advisor.

    LEGACY entry point — pre-Step-8. Single verdict per call (1 trade max
    per daemon cycle). Untuk multi-trade-per-cycle pakai `run_advisor_cycle`
    yang loop sampai cap concurrent / WAIT.

    `now_utc`: optional override untuk replay/backtest (look-ahead prevention).
    Production tetap pakai `None` → check_exits pakai `datetime.now()`. Backtest
    pass cycle timestamp supaya time-stop trigger nge-evaluate `age_h` dari
    sudut pandang bar yang sedang di-replay, bukan wall clock.
    """
    # Lazy import — auto_exit import _latest_close & _regime_direction dari sini.
    from .auto_exit import check_exits

    exit_v = check_exits(storage, cfg, now_utc=now_utc)
    if exit_v is not None:
        return exit_v
    return run_auto_advisor(
        storage, cfg, threshold=threshold, window_hours=window_hours,
    )


def run_advisor_cycle(
    storage: Storage,
    cfg: dict,
    data_dir,
    *,
    threshold: float = REGIME_T_THRESHOLD,
    window_hours: int = CANDIDATE_WINDOW_HOURS,
    now_utc=None,
    max_entries: Optional[int] = None,
) -> dict:
    """Step 8 — multi-trade-per-cycle orchestrator.

    EXIT pass dulu (semua OPEN trade yg trigger di-close), lalu ENTRY pass
    (sampai concurrent cap atau WAIT). Setiap iterasi: pilih next-best
    candidate, persist verdict via apply_verdict, eksekusi via run_executor.

    Termination guarantees:
      - EXIT loop: break ketika `check_exits` return None (tidak ada trigger
        baru) ATAU executor return non-CLOSE (SKIP/ERR) — defensive supaya
        trade yg stuck (mis. dedup race) tidak bikin infinite loop.
      - ENTRY loop: break ketika `run_auto_advisor` return WAIT (no candidate
        lolos sizing/dedup/threshold) ATAU executor return non-OPEN ATAU
        sudah `max_entries` iterasi.

    `max_entries` defensive ceiling — default = `cfg.portfolio.max_concurrent_trades`
    (3). Actual concurrent enforcement tetap via sizing.calc_position_size +
    executor._exposure_recheck (Step 3 caps unchanged).

    Per-pair cap (1 OPEN per pair) tetap berlaku via:
      - run_auto_advisor.fresh filter (open_pairs)
      - executor gate #6 (re-check at execution time)

    Return:
        {"n_exits":   int (count of CLOSE actions),
         "n_entries": int (count of OPEN actions),
         "exits":     [exec_result dict, ...],
         "entries":   [exec_result dict, ...]}
    """
    from .advisor import apply_verdict, build_context
    from .auto_exit import check_exits
    from .executor import CONFIDENCE_THRESHOLD, run_executor
    from .portfolio import DEFAULT_MAX_CONCURRENT_TRADES

    if max_entries is None:
        pcfg = cfg.get("portfolio") or {}
        max_entries = int(pcfg.get(
            "max_concurrent_trades", DEFAULT_MAX_CONCURRENT_TRADES,
        ))

    # Step 14B: derive executor confidence-gate threshold dari active detector.
    # FUNDING_FLIP mode → executor pakai band_min (≈0.0001 decimal) supaya
    # signal in-band lolos gate `confidence ≥ threshold` (|curr| ≥ band_min).
    # REGIME_CHANGE mode → executor default CONFIDENCE_THRESHOLD=8.0 (t-stat).
    advisor_cfg = cfg.get("advisor") or {}
    whitelist = advisor_cfg.get(
        "detector_whitelist", DEFAULT_DETECTOR_WHITELIST,
    )
    if whitelist == ["FUNDING_FLIP"]:
        ff_cfg = advisor_cfg.get("funding_flip") or {}
        exec_threshold = float(
            ff_cfg.get("band_min", DEFAULT_FUNDING_FLIP_BAND_MIN),
        )
    else:
        exec_threshold = CONFIDENCE_THRESHOLD

    exits: list[dict] = []
    entries: list[dict] = []

    # ─── EXIT pass ────────────────────────────────────────────────
    # Defensive ceiling — number of OPEN trades is bounded; this just
    # protects against pathological recursion.
    exit_safety = max(max_entries, 1) * 4
    while exit_safety > 0:
        exit_safety -= 1
        ev = check_exits(storage, cfg, now_utc=now_utc)
        if ev is None:
            break
        ctx = build_context(storage, cfg, now_utc=now_utc)
        apply_verdict(ev, ctx, data_dir, now_utc=now_utc)
        result = run_executor(storage, cfg, data_dir, now_utc=now_utc)
        exits.append(result)
        if result.get("action") != "CLOSE":
            # SKIP/ERR — trade still OPEN; break to avoid infinite re-fire
            break

    # ─── ENTRY pass ───────────────────────────────────────────────
    for _ in range(max(max_entries, 1)):
        ev = run_auto_advisor(
            storage, cfg, threshold=threshold, window_hours=window_hours,
        )
        if ev.get("verdict") != "ENTRY":
            break
        ctx = build_context(storage, cfg, now_utc=now_utc)
        apply_verdict(ev, ctx, data_dir, now_utc=now_utc)
        result = run_executor(
            storage, cfg, data_dir, now_utc=now_utc,
            threshold=exec_threshold,
        )
        entries.append(result)
        if result.get("action") != "OPEN":
            # SKIP (cap recheck, dedup race, etc) — break
            break

    return {
        "n_exits": sum(1 for r in exits if r.get("action") == "CLOSE"),
        "n_entries": sum(1 for r in entries if r.get("action") == "OPEN"),
        "exits": exits,
        "entries": entries,
    }
