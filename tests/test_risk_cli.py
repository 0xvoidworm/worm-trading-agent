"""Tests untuk scripts/risk.py CLI (Step 4)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe.risk_guard import KILLSWITCH_FILE


SCRIPT = ROOT / "scripts" / "risk.py"
PY = sys.executable


def _run(args, input_text=None, timeout=15):
    """Run risk.py CLI; return CompletedProcess."""
    return subprocess.run(
        [PY, str(SCRIPT), *args],
        input=input_text, capture_output=True, text=True, timeout=timeout,
        cwd=str(ROOT),
    )


# ─── status ──────────────────────────────────────────────────────────────

def test_status_renders_without_error():
    res = _run(["status"])
    assert res.returncode == 0, res.stderr
    out = res.stdout
    # Bloomberg-table sections
    assert "RISK GUARD" in out
    assert "equity_start" in out
    assert "daily_pnl_pct" in out
    assert "kill_threshold" in out
    assert "killswitch_file" in out


def test_status_shows_engaged_when_killswitch_present():
    """Kalau file killswitch ada, status output harus tampilin marker."""
    data_dir = ROOT / "data"
    ks = data_dir / KILLSWITCH_FILE
    backup = ks.read_text() if ks.exists() else None
    try:
        ks.write_text(json.dumps({
            "engaged_at": datetime.now(timezone.utc).isoformat(),
            "reason": "daily_loss",
            "daily_pnl_pct": -2.5,
            "threshold_pct": 2.0,
        }))
        res = _run(["status"])
        assert res.returncode == 0
        assert "ENGAGED" in res.stdout
        assert "daily_loss" in res.stdout
    finally:
        if backup is None:
            ks.unlink(missing_ok=True)
        else:
            ks.write_text(backup)


# ─── reset ───────────────────────────────────────────────────────────────

def test_reset_no_op_when_no_killswitch():
    data_dir = ROOT / "data"
    ks = data_dir / KILLSWITCH_FILE
    # Pre-condition: pastikan tidak ada file
    if ks.exists():
        backup = ks.read_text()
        ks.unlink()
    else:
        backup = None
    try:
        res = _run(["reset"])
        assert res.returncode == 0
        assert "nothing to reset" in res.stdout.lower()
    finally:
        if backup is not None:
            ks.write_text(backup)


def test_reset_aborts_on_wrong_confirm():
    data_dir = ROOT / "data"
    ks = data_dir / KILLSWITCH_FILE
    backup = ks.read_text() if ks.exists() else None
    try:
        ks.write_text(json.dumps({"engaged_at": "now", "reason": "test"}))
        res = _run(["reset"], input_text="nope\n")
        assert res.returncode == 1
        assert "aborted" in res.stdout.lower()
        # File still there
        assert ks.exists()
    finally:
        if backup is None:
            ks.unlink(missing_ok=True)
        else:
            ks.write_text(backup)


def test_reset_removes_file_and_logs_event_with_yes_flag():
    """--yes bypass prompt — pakai untuk test deterministic."""
    data_dir = ROOT / "data"
    ks = data_dir / KILLSWITCH_FILE
    backup = ks.read_text() if ks.exists() else None
    try:
        ks.write_text(json.dumps({
            "engaged_at": datetime.now(timezone.utc).isoformat(),
            "reason": "daily_loss",
            "daily_pnl_pct": -2.5,
        }))
        res = _run(["reset", "--yes"])
        assert res.returncode == 0
        assert "reset OK" in res.stdout
        assert not ks.exists()
        # risk.log appended
        assert (data_dir / "risk.log").exists()
        log = (data_dir / "risk.log").read_text()
        assert "RESET" in log
    finally:
        if backup is not None:
            ks.write_text(backup)


def test_reset_with_exact_RESET_string():
    data_dir = ROOT / "data"
    ks = data_dir / KILLSWITCH_FILE
    backup = ks.read_text() if ks.exists() else None
    try:
        ks.write_text(json.dumps({"engaged_at": "now", "reason": "test"}))
        res = _run(["reset"], input_text="RESET\n")
        assert res.returncode == 0
        assert "reset OK" in res.stdout
        assert not ks.exists()
    finally:
        if backup is not None:
            ks.write_text(backup)


# ─── history ─────────────────────────────────────────────────────────────

def test_history_runs_without_error():
    res = _run(["history", "--days", "30"])
    assert res.returncode == 0, res.stderr
    # Either shows table OR "no risk events"
    out = res.stdout.lower()
    assert "risk events" in out or "no risk events" in out


def test_history_default_days():
    res = _run(["history"])
    assert res.returncode == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
