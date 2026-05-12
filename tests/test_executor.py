"""Tests untuk auto_advisor + executor (Phase 2)."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_pipe.advisor import apply_verdict, build_context
from data_pipe.auto_advisor import parse_confidence, run_advisor, run_auto_advisor
from data_pipe.executor import KILL_SWITCH, run_executor
from data_pipe.storage import Storage


_CFG = {
    "exchange": "bybit",
    "timeframe": "1h",
    "tier1": ["SOL/USDT", "BTC/USDT", "ETH/USDT"],
    "tier2": [],
}


# ─── fixtures / helpers ───────────────────────────────────────────────────

def _seed_healthy(s: Storage) -> pd.Timestamp:
    """Fresh OHLCV + funding + 1 berita per feed supaya health=OK."""
    now = pd.Timestamp.now("UTC").tz_localize(None).floor("h")
    rows = []
    for sym in _CFG["tier1"]:
        rows.extend([
            {
                "exchange": "bybit", "symbol": sym, "timeframe": "1h",
                "ts": now - pd.Timedelta(hours=(9 - i)),
                "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100 + i,
                "volume": 1.0,
            }
            for i in range(10)
        ])
    s.upsert_ohlcv(rows)
    s.upsert_funding([
        {"exchange": "bybit", "symbol": sym, "ts": now,
         "funding": 0.0001, "mark_price": 100}
        for sym in _CFG["tier1"]
    ])
    from data_pipe.ingest_news import FEEDS
    now_dt = datetime.now(timezone.utc)
    s.upsert_news([
        {"id": f"n-{i}", "source": name, "title": f"h{i}", "url": f"u{i}",
         "published_at": now_dt - timedelta(hours=1),
         "fetched_at": now_dt, "symbols": "BTC,SOL,ETH"}
        for i, (name, _) in enumerate(FEEDS)
    ])
    return now


def _inject_signal(
    s: Storage, sig_id: str, ts: pd.Timestamp, symbol: str,
    event_type: str = "REGIME_CHANGE", payload: str | None = None,
):
    if payload is None:
        payload = "from=ranging;to=trending_up;t=10.5"
    s.upsert_signals([{
        "id": sig_id, "ts": ts, "event_type": event_type,
        "symbol": symbol, "severity": "info", "payload": payload,
    }])


# ─── parse_confidence ─────────────────────────────────────────────────────

def test_parse_confidence_regime_and_zspike():
    assert parse_confidence("from=ranging;to=trending_up;t=10.48", "REGIME_CHANGE") == pytest.approx(10.48)
    assert parse_confidence("from=trending_up;to=trending_down;t=-9.1", "REGIME_CHANGE") == pytest.approx(9.1)
    assert parse_confidence("pair=A|B;z=+2.57;adf_p=0.01", "Z_SPIKE") == pytest.approx(2.57)
    assert parse_confidence("pair=A|B;z=-3.0", "Z_SPIKE") == pytest.approx(3.0)
    assert parse_confidence(None, "REGIME_CHANGE") is None
    assert parse_confidence("garbage", "REGIME_CHANGE") is None


# ─── auto_advisor ─────────────────────────────────────────────────────────

def test_auto_advisor_wait_when_no_signals():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "WAIT"
            assert "proposed" not in v


def test_auto_advisor_skips_below_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            # Confidence kecil (t=3.0 < 8.0) → WAIT
            _inject_signal(
                s, "weak", pd.Timestamp(now).tz_localize(None),
                "SOL/USDT", payload="from=ranging;to=trending_up;t=3.0",
            )
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "WAIT"


def test_auto_advisor_proposes_entry_on_strong_signal():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(
                s, "strong", pd.Timestamp(now).tz_localize(None),
                "SOL/USDT", payload="from=ranging;to=trending_up;t=11.5",
            )
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "ENTRY"
            assert v["proposed"]["pair"] == "SOL/USDT"
            assert v["proposed"]["direction"] == "LONG"
            assert v["summary"] == "Bullish"
            assert v["proposed"]["entry_price"] is not None


def test_auto_advisor_picks_highest_confidence():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "a", pd.Timestamp(now).tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=9.0")
            _inject_signal(s, "b", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=12.0")
            v = run_auto_advisor(s, _CFG)
            assert v["proposed"]["pair"] == "ETH/USDT"


def test_auto_advisor_dedup_skips_already_linked_signals():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "used", pd.Timestamp(now).tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=10.0")
            # sig_num will be 1 (first signal)
            s.add_trade(pair="SOL/USDT", direction="LONG",
                        entry_price=100, size=1, signal_id=1)
            v = run_auto_advisor(s, _CFG)
            # Semua kandidat sudah dipakai → WAIT
            assert v["verdict"] == "WAIT"


def test_auto_advisor_short_on_trending_down():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "bear", pd.Timestamp(now).tz_localize(None),
                           "BTC/USDT", payload="from=trending_up;to=trending_down;t=-10.0")
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "ENTRY"
            assert v["proposed"]["direction"] == "SHORT"
            assert v["summary"] == "Bearish"


# ─── executor ─────────────────────────────────────────────────────────────

def _write_verdict(
    data_dir: Path, verdict: dict, *, age_minutes: float = 0,
    ctx: dict | None = None,
):
    """Helper: tulis advisor.latest.json dengan umur tertentu."""
    gen = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    payload = {
        "generated_at": gen.isoformat(),
        "verdict": verdict,
        "context_snapshot": {"health_status": "OK"},
    }
    (data_dir / "advisor.latest.json").write_text(json.dumps(payload))


def _full_entry_verdict(signal_id: int, pair: str = "SOL/USDT") -> dict:
    return {
        "summary": "Bullish",
        "verdict": "ENTRY",
        "thesis": "test thesis",
        "proposed": {
            "pair": pair, "direction": "LONG",
            "signal_id": signal_id,
            "entry_price": 100.0, "size_hint": 1.0,
        },
    }


def test_executor_skip_when_no_verdict():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "no advisor verdict" in r["reason"]


def test_executor_skip_when_verdict_stale():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "a", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=10.0")
            _write_verdict(data_dir, _full_entry_verdict(1), age_minutes=30)
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "stale" in r["reason"]


def test_executor_skip_when_verdict_wait():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _write_verdict(data_dir, {
                "summary": "Bullish", "verdict": "WAIT", "thesis": "chill",
            })
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "not ENTRY" in r["reason"]


def test_executor_skip_when_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        (data_dir / KILL_SWITCH).touch()
        with Storage(Path(tmp) / "t.duckdb") as s:
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "killswitch" in r["reason"]


def test_executor_skip_when_confidence_below_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "weak", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=3.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "confidence" in r["reason"]


def test_executor_opens_trade_on_happy_path():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "OPEN"
            assert r["trade_id"] is not None
            # Trade tercatat
            df = s.get_trades(status="OPEN")
            assert len(df) == 1
            assert df.iloc[0]["pair"] == "SOL/USDT"
            assert int(df.iloc[0]["signal_id"]) == 1
            # Latest file written
            assert (data_dir / "executor.latest.json").exists()
            assert (data_dir / "executor.log").exists()


def test_executor_dedup_same_signal():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            r1 = run_executor(s, _CFG, data_dir)
            assert r1["action"] == "OPEN"
            # Second tick — same verdict, same signal → dedup
            _write_verdict(data_dir, _full_entry_verdict(1))
            r2 = run_executor(s, _CFG, data_dir)
            assert r2["action"] == "SKIP"
            assert "dedup" in r2["reason"].lower() or "sudah" in r2["reason"]


def test_executor_per_symbol_cap():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "a", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _inject_signal(s, "b",
                           pd.Timestamp.now("UTC").tz_localize(None) - pd.Timedelta(minutes=5),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=12.0")
            # OPEN manual trade on SOL/USDT first (no signal link)
            s.add_trade(pair="SOL/USDT", direction="LONG",
                        entry_price=99, size=1)
            # Verdict proposes ENTRY on SOL/USDT with SIG#1
            _write_verdict(data_dir, _full_entry_verdict(1, "SOL/USDT"))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "OPEN" in r["reason"] and "SOL/USDT" in r["reason"]


def test_executor_skip_when_health_not_ok():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            # sengaja TIDAK seed → health STALE/GAP
            _inject_signal(s, "a", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "health" in r["reason"]


# ─── end-to-end (auto_advisor → apply_verdict → executor) ────────────────

def test_end_to_end_auto_pipeline_opens_trade():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "x", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=12.3")

            ctx = build_context(s, _CFG)
            verdict = run_auto_advisor(s, _CFG)
            apply_verdict(verdict, ctx, data_dir)
            result = run_executor(s, _CFG, data_dir)

            assert verdict["verdict"] == "ENTRY"
            assert verdict["proposed"]["pair"] == "ETH/USDT"
            assert result["action"] == "OPEN"
            assert result["pair"] == "ETH/USDT"


# ─── EXIT path (Step 2) ──────────────────────────────────────────────────


def _exit_verdict(trade_id: int, summary: str = "Bearish",
                  thesis: str = "stop loss hit") -> dict:
    return {
        "summary": summary,
        "verdict": "EXIT",
        "trade_id": trade_id,
        "thesis": thesis,
    }


def _open_dummy_trade(s: Storage, pair: str, direction: str = "LONG",
                      entry: float = 100.0, size: float = 1.0) -> int:
    return s.add_trade(pair=pair, direction=direction, entry_price=entry, size=size)


def test_executor_exit_happy_path_closes_trade():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = _open_dummy_trade(s, "SOL/USDT", entry=100.0, size=1.0)
            _write_verdict(data_dir, _exit_verdict(tid))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "CLOSE"
            assert r["trade_id"] == tid
            # trade_journal updated
            df = s.get_trades(status="OPEN")
            assert df.empty
            df_all = s.get_trades(status="CLOSED")
            assert tid in df_all["id"].astype(int).tolist()


def test_executor_exit_skips_when_trade_id_not_found():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _write_verdict(data_dir, _exit_verdict(9999))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "9999" in r["reason"] and "not found" in r["reason"]


def test_executor_exit_skips_when_trade_already_closed():
    """Idempotent: kalau trade sudah CLOSED, second EXIT SKIP."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = _open_dummy_trade(s, "SOL/USDT")
            s.close_trade(trade_id=tid, exit_price=99.0, notes="manual")
            _write_verdict(data_dir, _exit_verdict(tid))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "no longer OPEN" in r["reason"]


def test_executor_exit_skips_when_health_not_ok():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            # tidak seed → health bukan OK
            tid = _open_dummy_trade(s, "SOL/USDT")
            _write_verdict(data_dir, _exit_verdict(tid))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "health" in r["reason"]


def test_executor_exit_dedup_5min_via_log():
    """Gate #6: log scan menemukan recent CLOSE → SKIP kedua call meskipun
    trade masih OPEN (skenario race overlap heartbeat)."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = _open_dummy_trade(s, "SOL/USDT")
            # tulis fake log entry CLOSE 1 menit lalu utk trade ini
            now = datetime.now(timezone.utc)
            recent = (now - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
            (data_dir / "executor.log").write_text(
                f"[{recent} UTC] CLOSE #{tid}  closed #{tid} SOL/USDT @ 100 pnl=+0.00\n"
            )
            _write_verdict(data_dir, _exit_verdict(tid))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "dedup" in r["reason"].lower() or "recent" in r["reason"].lower()
            # trade tetap OPEN (gate #6 lebih dulu sebelum close_trade)
            assert not s.get_trades(status="OPEN").empty


def test_executor_exit_skips_when_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        (data_dir / KILL_SWITCH).touch()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = _open_dummy_trade(s, "SOL/USDT")
            _write_verdict(data_dir, _exit_verdict(tid))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "killswitch" in r["reason"]


# ─── Step 3: sizing integration ──────────────────────────────────────────


def test_executor_gate8_blocks_when_concurrent_cap_full():
    """Gate #8: 3 OPEN trades dummy → executor recheck SKIP cap concurrent."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            # fill caps with 3 unrelated trades
            for sym in ("BTC/USDT", "ETH/USDT", "BNB/USDT"):
                s.add_trade(pair=sym, direction="LONG",
                            entry_price=100, size=1)
            _inject_signal(s, "x", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            # Full ENTRY verdict for SOL/USDT (different pair → per-symbol cap OK)
            _write_verdict(data_dir, _full_entry_verdict(1, "SOL/USDT"))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "exposure recheck" in r["reason"]


def test_executor_gate8_blocks_when_oversized_position():
    """Gate #8: notional > max_position cap → SKIP."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "x", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            # craft verdict with absurd size: 100 SOL × $100 = $10000 > 25% cap ($2500)
            verdict = _full_entry_verdict(1, "SOL/USDT")
            verdict["proposed"]["size_hint"] = 100.0
            verdict["proposed"]["entry_price"] = 100.0
            _write_verdict(data_dir, verdict)
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "exposure recheck" in r["reason"]
            assert "max_position" in r["reason"]


def test_executor_uses_proposed_size_directly():
    """Executor TIDAK re-calc size; size dari proposed dipakai apa adanya."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "x", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            verdict = _full_entry_verdict(1, "SOL/USDT")
            verdict["proposed"]["size_hint"] = 0.42
            verdict["proposed"]["entry_price"] = 100.0
            _write_verdict(data_dir, verdict)
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "OPEN"
            assert r["size"] == pytest.approx(0.42)
            df = s.get_trades(status="OPEN")
            assert df.iloc[0]["size"] == pytest.approx(0.42)


# ─── Step 3: auto_advisor sizing integration ─────────────────────────────


def test_auto_advisor_entry_includes_sized_proposal():
    """ENTRY proposal harus include size_hint, notional, risk_amount dari sizing."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=11.5")
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "ENTRY"
            p = v["proposed"]
            # ETH latest close ~109 (from _seed_healthy), risk 1% × 10k = 100,
            # risk_per_unit = 109 × 0.05 = 5.45 → size ≈ 18.35, notional ≈ 2000
            assert p["size_hint"] is not None and p["size_hint"] > 0
            assert "notional" in p and p["notional"] > 0
            assert "risk_amount" in p
            assert p["risk_amount"] == pytest.approx(100.0)
            # Within Cap A (25% × 10000 = 2500)
            assert p["notional"] <= 2500.01


def test_auto_advisor_falls_back_to_wait_when_sizing_blocked_concurrent():
    """3 OPEN trades sebelum advisor → ENTRY → sizing concurrent cap → WAIT."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            for sym in ("BTC/USDT", "BNB/USDT", "XRP/USDT"):
                s.add_trade(pair=sym, direction="LONG",
                            entry_price=50, size=1)
            _inject_signal(s, "x", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=10.0")
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "WAIT"
            assert "sizing blocked" in v["thesis"] or "concurrent" in v["thesis"]


def test_auto_advisor_falls_back_to_wait_when_sizing_blocked_gross():
    """Gross sudah penuh (10000/10000) → sizing returns size=None gross → WAIT."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            # 2 OPEN trades dengan total notional = 10000 (fill 100% gross)
            s.add_trade(pair="BTC/USDT", direction="LONG",
                        entry_price=5000, size=1)   # 5000
            s.add_trade(pair="BNB/USDT", direction="LONG",
                        entry_price=5000, size=1)   # 5000
            _inject_signal(s, "x", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=10.0")
            v = run_auto_advisor(s, _CFG)
            assert v["verdict"] == "WAIT"
            assert "gross" in v["thesis"].lower() or "sizing blocked" in v["thesis"]


# ─── run_advisor chainer ─────────────────────────────────────────────────

def test_run_advisor_returns_exit_when_open_trade_hit_sl():
    """run_advisor: cek EXIT dulu, kalau ada → return EXIT (skip ENTRY logic)."""
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            # OHLCV terakhir SOL = 100+9 = 109 (lihat _seed_healthy loop)
            # Buka trade LONG entry 200 → -45% → SL fire
            tid = s.add_trade(pair="SOL/USDT", direction="LONG",
                              entry_price=200.0, size=1.0)
            v = run_advisor(s, _CFG)
            assert v["verdict"] == "EXIT"
            assert v["trade_id"] == tid


def test_run_advisor_falls_back_to_entry_when_no_exits():
    with tempfile.TemporaryDirectory() as tmp:
        with Storage(Path(tmp) / "t.duckdb") as s:
            now = _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp(now).tz_localize(None),
                           "ETH/USDT", payload="from=ranging;to=trending_up;t=12.3")
            v = run_advisor(s, _CFG)
            # no open trade → no EXIT → ENTRY fallback
            assert v["verdict"] == "ENTRY"
            assert v["proposed"]["pair"] == "ETH/USDT"


# ─── Step 4: risk killswitch integration ─────────────────────────────────

def _write_risk_killswitch(data_dir: Path, pct: float = -3.0) -> None:
    """Helper: drop risk.killswitch payload."""
    from data_pipe.risk_guard import KILLSWITCH_FILE
    (data_dir / KILLSWITCH_FILE).write_text(json.dumps({
        "engaged_at": datetime.now(timezone.utc).isoformat(),
        "reason": "daily_loss",
        "daily_pnl_pct": pct,
        "threshold_pct": 2.0,
    }))


def test_executor_entry_blocked_by_risk_killswitch():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            _write_risk_killswitch(data_dir, pct=-2.5)
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "risk_killswitch_active" in r["reason"]
            # Trade tidak terbuka
            assert s.get_trades(status="OPEN").empty


def test_executor_exit_NOT_blocked_by_risk_killswitch():
    """Risk killswitch HANYA block ENTRY. EXIT harus tetap jalan supaya
    bleeding berhenti, bukan diperpanjang."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = s.add_trade(pair="SOL/USDT", direction="LONG",
                              entry_price=200.0, size=1.0)
            _write_risk_killswitch(data_dir, pct=-2.5)
            _write_verdict(data_dir, {
                "summary": "Bearish", "verdict": "EXIT",
                "thesis": "force close",
                "trade_id": tid,
            })
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "CLOSE"
            assert r["trade_id"] == tid


def test_executor_manual_disabled_blocks_both_entry_and_exit():
    """Manual `executor.disabled` block-ALL — jangan bingung dengan risk
    killswitch. Behavior existing harus dipertahankan."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        (data_dir / KILL_SWITCH).touch()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            tid = s.add_trade(pair="SOL/USDT", direction="LONG",
                              entry_price=200.0, size=1.0)
            # ENTRY
            _write_verdict(data_dir, _full_entry_verdict(1))
            r1 = run_executor(s, _CFG, data_dir)
            assert r1["action"] == "SKIP"
            assert "killswitch" in r1["reason"]
            # EXIT
            _write_verdict(data_dir, {
                "summary": "Bearish", "verdict": "EXIT",
                "thesis": "x", "trade_id": tid,
            })
            r2 = run_executor(s, _CFG, data_dir)
            assert r2["action"] == "SKIP"
            assert "killswitch" in r2["reason"]
            # Trade tetap OPEN
            assert not s.get_trades(status="OPEN").empty


def test_executor_entry_allowed_after_risk_killswitch_removed():
    """Setelah operator reset (file dihapus), ENTRY harus bisa lagi."""
    from data_pipe.risk_guard import KILLSWITCH_FILE
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            _inject_signal(s, "strong", pd.Timestamp.now("UTC").tz_localize(None),
                           "SOL/USDT", payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1))
            _write_risk_killswitch(data_dir, pct=-2.5)
            r1 = run_executor(s, _CFG, data_dir)
            assert r1["action"] == "SKIP"
            # Operator reset: hapus file
            (data_dir / KILLSWITCH_FILE).unlink()
            # Re-write fresh verdict (advisor cycle baru)
            _write_verdict(data_dir, _full_entry_verdict(1))
            r2 = run_executor(s, _CFG, data_dir)
            assert r2["action"] == "OPEN"


# ─── Step 8: sequential multi-trade per-cycle simulation ────────────────

def test_executor_sequential_three_entries_pass_within_cycle():
    """Simulasi 3 ENTRY berturut dalam single cycle (yang nantinya
    di-orchestrate run_advisor_cycle). Trade #1 BTC, #2 ETH, #3 SOL —
    semua lolos gate per-pair + cap 3."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            # Inject 3 distinct signals di 3 pair berbeda
            ts_now = pd.Timestamp.now("UTC").tz_localize(None)
            _inject_signal(s, "a", ts_now, "BTC/USDT",
                           payload="from=ranging;to=trending_up;t=11.0")
            _inject_signal(s, "b", ts_now - pd.Timedelta(seconds=1),
                           "ETH/USDT",
                           payload="from=ranging;to=trending_up;t=11.0")
            _inject_signal(s, "c", ts_now - pd.Timedelta(seconds=2),
                           "SOL/USDT",
                           payload="from=ranging;to=trending_up;t=11.0")

            # Sequential calls — each with its own verdict (bermimik
            # run_advisor_cycle iterations).
            results = []
            for sig_num, pair in [(1, "BTC/USDT"), (2, "ETH/USDT"),
                                   (3, "SOL/USDT")]:
                v = _full_entry_verdict(sig_num, pair)
                # Use small notional supaya gross cap tidak ketabrak
                v["proposed"]["size_hint"] = 1.0
                v["proposed"]["entry_price"] = 100.0
                _write_verdict(data_dir, v)
                r = run_executor(s, _CFG, data_dir)
                results.append(r)

            actions = [r["action"] for r in results]
            assert actions == ["OPEN", "OPEN", "OPEN"], (
                f"expected 3 OPEN, got {actions}: "
                f"{[r.get('reason') for r in results]}"
            )
            assert len(s.get_trades(status="OPEN")) == 3


def test_executor_fourth_entry_blocked_by_concurrent_cap():
    """Setelah 3 OPEN trade dari iter sebelumnya, iter ke-4 di pair baru
    → executor gate #8 SKIP (exposure recheck cap concurrent)."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        with Storage(Path(tmp) / "t.duckdb") as s:
            _seed_healthy(s)
            # Pre-fill 3 OPEN
            for sym in ("BTC/USDT", "ETH/USDT", "BNB/USDT"):
                s.add_trade(pair=sym, direction="LONG",
                            entry_price=100, size=1)
            ts_now = pd.Timestamp.now("UTC").tz_localize(None)
            _inject_signal(s, "x", ts_now, "SOL/USDT",
                           payload="from=ranging;to=trending_up;t=11.0")
            _write_verdict(data_dir, _full_entry_verdict(1, "SOL/USDT"))
            r = run_executor(s, _CFG, data_dir)
            assert r["action"] == "SKIP"
            assert "exposure recheck" in r["reason"]
            assert "open count" in r["reason"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
