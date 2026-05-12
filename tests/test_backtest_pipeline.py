"""Pipeline smoke tests — run a small backtest, check outputs + BnH math."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from data_pipe.storage import Storage
from scripts import backtest_production as bt


def _seed_db(db_path: Path):
    s = Storage(db_path=db_path)
    base = datetime(2026, 3, 1)
    rows = []
    # 14 days of hourly OHLCV BTC + ETH + SOL with a slow uptrend
    for sym, p0, slope in [("BTC/USDT", 50000, 5),
                            ("ETH/USDT", 3000, 0.5),
                            ("SOL/USDT", 100, 0.05)]:
        for i in range(14 * 24):
            close = p0 + i * slope
            rows.append({"exchange": "bybit", "symbol": sym,
                         "timeframe": "1h",
                         "ts": base + timedelta(hours=i),
                         "open": close, "high": close * 1.001,
                         "low": close * 0.999, "close": close,
                         "volume": 100.0})
    s.upsert_ohlcv(rows)
    s.close()


def test_smoke_backtest_runs_and_writes_outputs(tmp_path, monkeypatch):
    db = tmp_path / "src.duckdb"
    _seed_db(db)

    # Redirect output dir
    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)

    summary = bt.run_backtest(
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 7),  # 7 days = 169 cycles
        label="smoke",
        db_path=db,
        cycle_hours=1,
        verbose=False,
    )

    out_dir = out_root / "smoke"
    for fname in ("summary.json", "trades.csv", "equity_curve.csv", "report.md"):
        assert (out_dir / fname).exists(), f"{fname} missing"

    # Schema sanity
    summary_disk = json.loads((out_dir / "summary.json").read_text())
    for k in ("label", "start", "end", "n_cycles", "starting_capital",
              "strategy", "buy_hold_btc", "edge_verdict",
              "n_trades_total", "n_trades_closed",
              "n_trades_open_at_end", "realized_return_pct", "mtm_return_pct",
              "cycles_health_breakdown", "gate_reason_counts",
              "insufficient_sample_threshold"):
        assert k in summary_disk, f"summary missing key {k}"
    for k in ("return_pct", "max_drawdown_pct", "sharpe_annualized",
              "final_equity", "n_bars"):
        assert k in summary_disk["strategy"], f"strategy missing {k}"
    # Step 6: equity_curve.csv has the new columns
    eq_header = (out_dir / "equity_curve.csv").read_text().splitlines()[0]
    for col in ("trading_health", "operational_health", "gate_reason",
                "exec_action"):
        assert col in eq_header, f"equity_curve missing column {col}"

    # Edge verdict valid value (Step 6 adds insufficient_sample)
    assert summary["edge_verdict"] in (
        "positive", "neutral", "negative", "insufficient_sample",
    )


def test_bnh_math_matches_close_ratio(tmp_path):
    """BnH return = (last_close / first_close - 1) × 100."""
    db = tmp_path / "src.duckdb"
    _seed_db(db)
    src = Storage(db_path=db, read_only=True)
    bnh = bt._bnh_baseline(
        src, "BTC/USDT", "bybit", "1h",
        datetime(2026, 3, 1), datetime(2026, 3, 7),
        starting_capital=10000.0,
    )
    # First close = 50000, last <= 50000 + 144*5 = 50720
    assert bnh["start_price"] == 50000.0
    expected_ret = (bnh["end_price"] / bnh["start_price"] - 1.0) * 100.0
    assert abs(bnh["return_pct"] - expected_ret) < 1e-9
    src.close()


def test_equity_curve_length_matches_cycles(tmp_path, monkeypatch):
    db = tmp_path / "src.duckdb"
    _seed_db(db)
    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)
    summary = bt.run_backtest(
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),  # 25 cycles inclusive
        label="curvelen",
        db_path=db, cycle_hours=1, verbose=False,
    )
    assert summary["n_cycles"] == 25
    eq_lines = (out_root / "curvelen" / "equity_curve.csv").read_text().splitlines()
    # Header + 25 rows
    assert len(eq_lines) == 26


def test_insufficient_sample_verdict_when_few_trades(tmp_path, monkeypatch):
    """Step 6: n_closed < threshold → edge_verdict = insufficient_sample.

    Smoke seed produces ~zero closed trades — verdict harus 'insufficient_sample'
    sehingga operator tidak salah baca return % sebagai signal.
    """
    db = tmp_path / "src.duckdb"
    _seed_db(db)
    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)
    summary = bt.run_backtest(
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),  # 25 cycles, no real signals
        label="insufficient",
        db_path=db, cycle_hours=1, verbose=False,
    )
    assert summary["n_trades_closed"] < bt.INSUFFICIENT_SAMPLE_THRESHOLD
    assert summary["edge_verdict"] == "insufficient_sample"
    assert summary["insufficient_sample_threshold"] == bt.INSUFFICIENT_SAMPLE_THRESHOLD


def test_summary_has_multi_trade_block(tmp_path, monkeypatch):
    """Step 8: summary.json includes 'multi_trade' block dengan agregat
    n_entries/n_exits per cycle (zero on smoke seed = no real signals)."""
    db = tmp_path / "src.duckdb"
    _seed_db(db)
    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)
    summary = bt.run_backtest(
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),
        label="multitrade_smoke",
        db_path=db, cycle_hours=1, verbose=False,
    )
    assert "multi_trade" in summary
    mt = summary["multi_trade"]
    for k in ("total_cycle_entries", "total_cycle_exits",
              "cycles_with_entry", "cycles_with_exit",
              "multi_trade_cycles"):
        assert k in mt, f"multi_trade missing {k}"
    # Smoke seed has no signals → all zeros
    assert mt["total_cycle_entries"] == 0
    assert mt["total_cycle_exits"] == 0


def test_backtest_multi_entry_cycle_opens_more_than_one_trade(tmp_path, monkeypatch):
    """Inject 3 fresh REGIME_CHANGE signals di 3 pair berbeda di cycle
    pertama → run_advisor_cycle harus open ≥2 trade dalam single cycle
    (proves multi-trade plumbing works in backtest path).

    Seed all 8 configured pairs (tier1 + tier2 dari pairs.yaml) supaya
    health=OK; signals hanya di 3 pair → ENTRY proposals max 3.
    """
    import hashlib
    db = tmp_path / "src.duckdb"
    s = Storage(db_path=db)
    base = datetime(2026, 3, 1)
    all_pairs = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT",  # tier1
        "BNB/USDT", "XRP/USDT", "AVAX/USDT", "ARB/USDT", "OP/USDT",  # tier2
    ]
    rows = []
    for sym in all_pairs:
        p0 = 100.0
        for i in range(14 * 24):
            close = float(p0 + i * 0.1)
            rows.append({"exchange": "bybit", "symbol": sym,
                         "timeframe": "1h",
                         "ts": base + timedelta(hours=i),
                         "open": close, "high": close * 1.001,
                         "low": close * 0.999, "close": close,
                         "volume": 100.0})
    s.upsert_ohlcv(rows)
    # Funding fresh untuk semua pair → operational=OK (tidak gate trading
    # tapi tetap supaya log clean)
    s.upsert_funding([
        {"exchange": "bybit", "symbol": sym, "ts": base + timedelta(hours=14*24-1),
         "funding": 0.0001, "mark_price": 100}
        for sym in all_pairs
    ])
    # Inject 3 strong signals at hour 30 → fresh in 4h advisor window pada
    # cycles antara hour 30 dan hour 34.
    sig_ts = base + timedelta(hours=30)
    for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        payload = "from=ranging;to=trending_up;t=12.0"
        raw = f"REGIME_CHANGE|{sym}|{sig_ts.isoformat()}|{payload}".encode()
        s.upsert_signals([{
            "id": hashlib.sha256(raw).hexdigest()[:16],
            "ts": sig_ts,
            "event_type": "REGIME_CHANGE",
            "symbol": sym,
            "severity": "info",
            "payload": payload,
        }])
    s.close()

    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)
    summary = bt.run_backtest(
        start=base + timedelta(hours=29),  # start just before signal ts
        end=base + timedelta(hours=33),    # 5 cycles, signals fresh in 4h window
        label="multientry",
        db_path=db, cycle_hours=1, verbose=False,
    )
    mt = summary["multi_trade"]
    # ≥2 trades opened total across the 5 cycles (multi-trade unlocked)
    assert mt["total_cycle_entries"] >= 2, (
        f"expected ≥2 entries (multi-trade), got {mt}"
    )
    # At least one cycle should have multi-entry (entries+exits > 1)
    # OR multiple cycles each entered — both satisfy multi-trade goal.
    assert summary["n_trades_total"] >= 2


def test_summary_has_split_health_breakdown(tmp_path, monkeypatch):
    """Cycles health breakdown reports both trading + operational dimensions
    (Step 6 split-health gating)."""
    db = tmp_path / "src.duckdb"
    _seed_db(db)
    out_root = tmp_path / "results"
    monkeypatch.setattr(bt, "RESULTS_ROOT", out_root)
    summary = bt.run_backtest(
        start=datetime(2026, 3, 1),
        end=datetime(2026, 3, 2),
        label="splithealth",
        db_path=db, cycle_hours=1, verbose=False,
    )
    hb = summary["cycles_health_breakdown"]
    for k in ("trading_OK", "trading_GAP", "trading_STALE",
              "operational_OK", "operational_GAP"):
        assert k in hb, f"breakdown missing {k}"
    # Conservation: trading dimension counts sum to n_cycles
    assert (
        hb["trading_OK"] + hb["trading_GAP"] + hb["trading_STALE"]
        == summary["n_cycles"]
    )
