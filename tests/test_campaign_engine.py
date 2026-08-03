from __future__ import annotations

import sys
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_pipe import campaign_engine as ce

_SPEC = importlib.util.spec_from_file_location(
    "brutalctl_test", ROOT / "scripts" / "brutalctl.py"
)
assert _SPEC and _SPEC.loader
brutalctl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(brutalctl)


def _cfg(**kw):
    base = dict(campaign_id="t", cooldown_sec=0.0, freshness_sec=600.0)
    base.update(kw)
    return ce.CampaignConfig(**base)


# ── compute_r ────────────────────────────────────────────────────────────────

def test_compute_r_long_and_short():
    # entry 100, stop 90 => 1R distance = 10
    assert ce.compute_r("LONG", 100, 90, 110) == pytest.approx(1.0)
    assert ce.compute_r("LONG", 100, 90, 80) == pytest.approx(-2.0)
    # short: profit when price falls
    assert ce.compute_r("SHORT", 100, 110, 90) == pytest.approx(1.0)
    assert ce.compute_r("SHORT", 100, 110, 120) == pytest.approx(-2.0)


def test_compute_r_zero_distance_raises():
    with pytest.raises(ValueError):
        ce.compute_r("LONG", 100, 100, 105)


def test_compute_r_bad_side():
    with pytest.raises(ValueError):
        ce.compute_r("SIDEWAYS", 100, 90, 100)


# ── size_qty ─────────────────────────────────────────────────────────────────

def test_size_qty_snaps_down_to_step():
    # risk 0.25% of 1000 = 2.5 USD; dist 0.10 => raw 25; step 1 => 25
    q = ce.size_qty(1000, 0.25, 1.00, 0.90, step_size=1.0, min_qty=1.0)
    assert q == pytest.approx(25.0)


def test_size_qty_floors_not_rounds_up():
    # raw would be 24.9 -> must floor to 24, never 25 (never exceed risk target)
    q = ce.size_qty(996, 0.25, 1.00, 0.90, step_size=1.0, min_qty=1.0)
    assert q == pytest.approx(24.0)


def test_size_qty_never_rounds_up_past_risk():
    # risk 2.5 USD; dist 0.10 => raw 25; step 20 => floor(25/20)*20 = 20
    q = ce.size_qty(1000, 0.25, 1.00, 0.90, step_size=20.0, min_qty=1.0)
    assert q == pytest.approx(20.0)


def test_size_qty_min_qty_floor():
    q = ce.size_qty(10, 0.25, 1.00, 0.99, step_size=1.0, min_qty=5.0)
    assert q == 0.0  # min_qty would exceed the declared risk budget


def test_size_qty_min_qty_override_is_explicit():
    q = ce.size_qty(
        10, 0.25, 1.00, 0.99,
        step_size=1.0, min_qty=5.0,
        allow_min_qty_risk_overrun=True,
    )
    assert q == pytest.approx(5.0)


def test_size_qty_degenerate_returns_zero():
    assert ce.size_qty(1000, 0.25, 1.0, 1.0, 1.0, 1.0) == 0.0
    assert ce.size_qty(0, 0.25, 1.0, 0.9, 1.0, 1.0) == 0.0


# ── targets_from_entry ───────────────────────────────────────────────────────

def test_targets_long_short():
    stop, tp = ce.targets_from_entry("LONG", 100, 90)
    assert stop == 90 and tp == pytest.approx(120)   # +2R
    stop, tp = ce.targets_from_entry("SHORT", 100, 110)
    assert stop == 110 and tp == pytest.approx(80)


def test_protective_stop_price_long_and_short():
    # Long: original stop -> BE -> trailing peak - giveback.
    assert ce.protective_stop_price("LONG", 100, 90, 0.5) == pytest.approx(90)
    assert ce.protective_stop_price("LONG", 100, 90, 1.0) == pytest.approx(100)
    assert ce.protective_stop_price("LONG", 100, 90, 1.8) == pytest.approx(110.5)
    # Short mirrors the geometry.
    assert ce.protective_stop_price("SHORT", 100, 110, 1.0) == pytest.approx(100)
    assert ce.protective_stop_price("SHORT", 100, 110, 1.8) == pytest.approx(89.5)


# ── exit_decision parity with paper policy ──────────────────────────────────

def _pos(**kw):
    base = dict(signal_id="s", instrument="X", side="LONG", entry=100.0, stop=90.0,
                tp=120.0, qty=1.0, opened_at="2026-01-01T00:00:00+00:00", peak_r=0.0)
    base.update(kw)
    return base


def test_exit_take_profit():
    d = ce.exit_decision(_pos(), 120)  # +2R
    assert d["status"] == "TP2" and d["exit_r"] == pytest.approx(2.0)


def test_exit_hard_stop():
    d = ce.exit_decision(_pos(), 90)  # -1R
    assert d["status"] == "STOPPED" and d["exit_r"] == pytest.approx(-1.0)


def test_exit_breakeven_after_arm():
    d = ce.exit_decision(_pos(peak_r=1.0), 100)  # back to entry after +1R
    assert d["status"] == "BREAKEVEN" and d["exit_r"] == pytest.approx(0.0)


def test_exit_trailing_after_arm():
    # peak +1.5R, giveback 0.75 => trail at +0.75R; price at +0.75R triggers
    d = ce.exit_decision(_pos(peak_r=1.5), 107.5)
    assert d["status"] == "TRAIL" and d["exit_r"] == pytest.approx(0.75)


def test_no_exit_when_running():
    assert ce.exit_decision(_pos(), 105) is None  # +0.5R, nothing armed


# ── select_entry gates ───────────────────────────────────────────────────────

def _cand(**kw):
    base = dict(signal_id="CEX_PERP:AAA:LONG:t", detected_at="2026-01-01T00:00:00+00:00",
                lane="CEX_PERP", side="LONG", score=100.0, entry_ref=1.0, stop_ref=0.9)
    base.update(kw)
    return base


def test_select_capacity_block():
    now = ce.parse_iso("2026-01-01T00:00:30+00:00")
    r = ce.select_entry([_cand()], now=now, started_at=ce.parse_iso("2026-01-01T00:00:00+00:00"),
                        executed_ids=[], open_count=1, last_exit_at=None, cfg=_cfg())
    assert r["reason"] == "CAPACITY"


def test_select_cooldown_block():
    now = ce.parse_iso("2026-01-01T00:10:00+00:00")
    r = ce.select_entry([_cand()], now=now, started_at=ce.parse_iso("2026-01-01T00:00:00+00:00"),
                        executed_ids=[], open_count=0,
                        last_exit_at=ce.parse_iso("2026-01-01T00:09:59+00:00"),
                        cfg=_cfg(cooldown_sec=1800.0))
    assert r["reason"] == "COOLDOWN"


def test_select_dedup_block():
    now = ce.parse_iso("2026-01-01T00:00:30+00:00")
    r = ce.select_entry([_cand()], now=now, started_at=ce.parse_iso("2026-01-01T00:00:00+00:00"),
                        executed_ids=["CEX_PERP:AAA:LONG:t"], open_count=0, last_exit_at=None, cfg=_cfg())
    assert r["reason"] == "NO_FRESH_CANDIDATE"


def test_select_freshness_and_before_start():
    started = ce.parse_iso("2026-01-01T00:05:00+00:00")
    now = ce.parse_iso("2026-01-01T00:06:00+00:00")
    # candidate detected before campaign start must be ignored
    stale = _cand(detected_at="2026-01-01T00:00:00+00:00")
    r = ce.select_entry([stale], now=now, started_at=started, executed_ids=[],
                        open_count=0, last_exit_at=None, cfg=_cfg())
    assert r["reason"] == "NO_FRESH_CANDIDATE"


def test_select_lane_filter():
    now = ce.parse_iso("2026-01-01T00:00:30+00:00")
    r = ce.select_entry([_cand(lane="DEX_MEME")], now=now,
                        started_at=ce.parse_iso("2026-01-01T00:00:00+00:00"),
                        executed_ids=[], open_count=0, last_exit_at=None, cfg=_cfg())
    assert r["reason"] == "NO_FRESH_CANDIDATE"


def test_select_deterministic_highest_score_then_id():
    now = ce.parse_iso("2026-01-01T00:00:30+00:00")
    started = ce.parse_iso("2026-01-01T00:00:00+00:00")
    lo = _cand(signal_id="CEX_PERP:AAA:LONG:t", score=100.0)
    hi = _cand(signal_id="CEX_PERP:BBB:LONG:t", score=200.0)
    r = ce.select_entry([lo, hi], now=now, started_at=started, executed_ids=[],
                        open_count=0, last_exit_at=None, cfg=_cfg())
    assert r["action"] == "ENTER" and r["candidate"]["signal_id"] == "CEX_PERP:BBB:LONG:t"
    # tie on score => lexicographically smallest signal_id wins (determinism)
    a = _cand(signal_id="CEX_PERP:ZZZ:LONG:t", score=150.0)
    b = _cand(signal_id="CEX_PERP:AAA:LONG:t", score=150.0)
    r2 = ce.select_entry([a, b], now=now, started_at=started, executed_ids=[],
                         open_count=0, last_exit_at=None, cfg=_cfg())
    assert r2["candidate"]["signal_id"] == "CEX_PERP:AAA:LONG:t"


def test_select_skips_degenerate_geometry():
    now = ce.parse_iso("2026-01-01T00:00:30+00:00")
    r = ce.select_entry([_cand(entry_ref=1.0, stop_ref=1.0)], now=now,
                        started_at=ce.parse_iso("2026-01-01T00:00:00+00:00"),
                        executed_ids=[], open_count=0, last_exit_at=None, cfg=_cfg())
    assert r["reason"] == "NO_FRESH_CANDIDATE"


# ── stop conditions ──────────────────────────────────────────────────────────

def test_stop_max_trades():
    st = ce.CampaignState(started_at="2026-01-01T00:00:00+00:00", closed_trades=50)
    assert ce.stop_reason(st, _cfg(max_closed_trades=50), ce.parse_iso("2026-01-02T00:00:00+00:00")) == "MAX_TRADES"


def test_stop_loss_budget():
    st = ce.CampaignState(started_at="2026-01-01T00:00:00+00:00", realized_r=-20.0)
    assert ce.stop_reason(st, _cfg(loss_budget_r=20.0), ce.parse_iso("2026-01-02T00:00:00+00:00")) == "LOSS_BUDGET"


def test_stop_max_days():
    st = ce.CampaignState(started_at="2026-01-01T00:00:00+00:00")
    later = ce.parse_iso("2026-01-01T00:00:00+00:00") + timedelta(days=21)
    assert ce.stop_reason(st, _cfg(max_days=21.0), later) == "MAX_DAYS"


def test_no_stop_on_ordinary_loss():
    # a losing streak that is NOT past the budget must not stop the campaign
    st = ce.CampaignState(started_at="2026-01-01T00:00:00+00:00", realized_r=-3.0, closed_trades=5)
    assert ce.stop_reason(st, _cfg(loss_budget_r=20.0, max_closed_trades=50), ce.parse_iso("2026-01-02T00:00:00+00:00")) is None


# ── structural faults ────────────────────────────────────────────────────────

def test_fault_naked_position():
    st = ce.CampaignState(open_positions=[{"instrument": "AKEUSDT"}])
    f = ce.structural_faults(st, [{"instrument": "AKEUSDT", "contracts": 1}], {"AKEUSDT": []})
    assert any(x.startswith("NAKED_POSITION") for x in f)


def test_exchange_flat_campaign_position_is_resolved_by_trigger_history():
    st = ce.CampaignState(open_positions=[{"instrument": "AKEUSDT"}])
    assert ce.structural_faults(st, [], {"AKEUSDT": []}) == []


def test_fault_ghost_position():
    st = ce.CampaignState(open_positions=[])
    f = ce.structural_faults(st, [{"instrument": "BTCUSDT", "contracts": 1}], {})
    assert any(x.startswith("GHOST_POSITION") for x in f)


def test_fault_orphan_triggers():
    st = ce.CampaignState(open_positions=[])
    f = ce.structural_faults(st, [], {"XRPUSDT": [{"id": "1"}]})
    assert any(x.startswith("ORPHAN_TRIGGERS") for x in f)


def test_no_fault_when_consistent():
    st = ce.CampaignState(open_positions=[{
        "instrument": "AKEUSDT",
        "sl_order_id": "sl",
        "tp_order_id": "tp",
    }])
    f = ce.structural_faults(
        st,
        [{"symbol": "AKE/USDT:USDT", "contracts": 1}],
        {
            "AKEUSDT": [
                {"id": "sl", "type": "STOP_MARKET"},
                {"id": "tp", "type": "TAKE_PROFIT_MARKET"},
            ]
        },
    )
    assert f == []


def test_fault_missing_expected_tp():
    st = ce.CampaignState(open_positions=[{
        "instrument": "AKEUSDT",
        "sl_order_id": "sl",
        "tp_order_id": "tp",
    }])
    f = ce.structural_faults(
        st,
        [{"symbol": "AKE/USDT:USDT", "contracts": 1}],
        {"AKEUSDT": [{"id": "sl", "type": "STOP_MARKET"}]},
    )
    assert any(x.startswith("MISSING_TP") for x in f)


# ── controller lifecycle ─────────────────────────────────────────────────────

def test_controller_full_lifecycle(tmp_path):
    cfg = _cfg(max_closed_trades=2, loss_budget_r=20.0)
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    pos = ce.Position(signal_id="s1", instrument="AKEUSDT", side="LONG", entry=1.0,
                      stop=0.9, tp=1.2, qty=10, opened_at=ctrl.state.started_at)
    ctrl.record_entry(pos)
    assert len(ctrl.state.open_positions) == 1
    assert "s1" in ctrl.state.executed_signal_ids
    ctrl.record_exit("s1", 2.0, "TP2", exit_price=1.2, exit_fee_usd=0.01)
    assert ctrl.state.closed_trades == 1 and ctrl.state.realized_r == pytest.approx(2.0)
    assert ctrl.state.wins == 1 and not ctrl.state.open_positions
    assert ctrl.state.closed_records[0]["status"] == "TP2"
    assert ctrl.state.realized_pnl_usd == pytest.approx(-0.01)
    # dedup persists after exit: same signal cannot re-enter
    assert "s1" in ctrl.state.executed_signal_ids
    # second closed trade hits MAX_TRADES and completes when flat
    pos2 = ce.Position(signal_id="s2", instrument="BUSDT", side="SHORT", entry=1.0,
                       stop=1.1, tp=0.8, qty=5, opened_at=ctrl.state.started_at)
    ctrl.record_entry(pos2)
    ctrl.record_exit("s2", -1.0, "STOPPED")
    ctrl.maybe_complete()
    assert ctrl.state.status == "COMPLETED" and ctrl.state.stop_reason == "MAX_TRADES"


def test_controller_persist_reload(tmp_path):
    cfg = _cfg()
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    pos = ce.Position(signal_id="s9", instrument="AKEUSDT", side="LONG", entry=1.0,
                      stop=0.9, tp=1.2, qty=10, opened_at=ctrl.state.started_at)
    ctrl.record_entry(pos)
    ctrl.save()
    ctrl2 = ce.CampaignController(cfg, state_dir=tmp_path)
    assert len(ctrl2.state.open_positions) == 1
    assert ctrl2.state.open_positions[0]["signal_id"] == "s9"


def test_entries_blocked_when_budget_hit(tmp_path):
    cfg = _cfg(loss_budget_r=1.0)
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    ctrl.state.realized_r = -1.0
    assert ctrl.entries_blocked() == "LOSS_BUDGET"


def test_config_validation_rejects_bad_risk():
    with pytest.raises(ValueError):
        _cfg(risk_pct=5.0).validate()
    with pytest.raises(ValueError):
        _cfg(leverage=10).validate()


class FakeBroker:
    is_live = False

    def __init__(self, marks=None):
        self.marks = list(marks or [1.0])
        self.mark = self.marks[0]
        self.orders = []
        self.triggers = {}
        self.positions = {}
        self.stop_success = True
        self.tp_success = True
        self.orders_by_client = {}

    def _next_mark(self):
        if self.marks:
            self.mark = self.marks.pop(0)
        return self.mark

    def fetch_positions(self, symbol=None):
        if symbol is not None:
            p = self.positions.get(symbol)
            return [p] if p else []
        return list(self.positions.values())

    def fetch_trigger_orders(self, symbol=None):
        return list(self.triggers.get(symbol, [])) if symbol else []

    def fetch_mark_price(self, symbol):
        return self._next_mark()

    def get_market_filters(self, symbol):
        return {"step_size": 1.0, "min_qty": 1.0, "min_notional": 5.0}

    def check_order_feasible(self, symbol, amount, price):
        return None

    def set_margin_mode(self, symbol, mode="ISOLATED"):
        return True

    def set_leverage(self, symbol, leverage):
        return True

    def assert_live_entry_ready(self, risk):
        return {"risk": risk}

    def place_market_order(
        self, symbol, side, amount, reduce_only=False, client_order_id=None,
    ):
        self.orders.append((side, amount, reduce_only, client_order_id))
        if reduce_only:
            self.positions.pop(symbol, None)
        else:
            ps = "LONG" if side == "buy" else "SHORT"
            self.positions[symbol] = {
                "instrument": ce.normalize_instrument(symbol),
                "contracts": amount,
                "side": ps,
            }
        result = SimpleNamespace(
            success=True,
            avg_fill_price=self.mark,
            filled_amount=amount,
            fee_usdt=0.01,
            error=None,
            order_id=client_order_id or "market",
        )
        if client_order_id:
            self.orders_by_client[client_order_id] = {
                "id": result.order_id,
                "status": "closed",
                "average": self.mark,
                "fee": {"cost": 0.01},
            }
        return result

    def place_reduce_trigger(
        self, symbol, side, amount, price, kind, client_order_id=None,
    ):
        success = self.stop_success if kind == "STOP" else self.tp_success
        oid = client_order_id or kind
        if success:
            self.triggers.setdefault(symbol, []).append({
                "id": oid,
                "type": "STOP_MARKET" if kind == "STOP" else "TAKE_PROFIT_MARKET",
            })
        return SimpleNamespace(success=success, order_id=oid)

    def cancel_order(self, symbol, order_id, conditional=True):
        self.triggers[symbol] = [
            t for t in self.triggers.get(symbol, [])
            if str(t.get("id")) != str(order_id)
        ]
        return True

    def cancel_all_orders(self, symbol):
        self.triggers[symbol] = []
        return True

    def fetch_order_by_client_id(self, symbol, client_order_id, conditional=False):
        return self.orders_by_client.get(client_order_id)

    def fetch_trigger_execution(self, symbol, client_order_id):
        return None


def _fresh_candidate():
    return [{
        "signal_id": "CEX_PERP:AKEUSDT:LONG:fresh",
        "detected_at": ce.now_utc().replace(microsecond=0).isoformat(),
        "lane": "CEX_PERP",
        "instrument": "AKEUSDT",
        "side": "LONG",
        "score": 300.0,
        "entry_ref": 1.0,
        "stop_ref": 0.9,
        "tp2_ref": 1.2,
    }]


def test_tick_once_dry_run_does_not_halt_on_real_exchange_flat(tmp_path, monkeypatch):
    broker = FakeBroker([1.0])
    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: broker)
    monkeypatch.setattr(brutalctl, "live_bankroll", lambda: 1000.0)
    monkeypatch.setattr(brutalctl, "candidates_from_action_plan", _fresh_candidate)
    cfg = _cfg(max_closed_trades=2, live=False)
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    out = brutalctl._tick_once(ctrl)
    assert out["actions"][0]["enter"] == "AKEUSDT"
    assert ctrl.state.status == "RUNNING"
    assert len(ctrl.state.open_positions) == 1


def test_tick_once_dry_run_restart_exits_and_completes(tmp_path, monkeypatch):
    broker = FakeBroker([1.0, 1.2])
    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: broker)
    monkeypatch.setattr(brutalctl, "live_bankroll", lambda: 1000.0)
    monkeypatch.setattr(brutalctl, "candidates_from_action_plan", _fresh_candidate)
    cfg = _cfg(max_closed_trades=1, live=False)
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    brutalctl._tick_once(ctrl)
    reloaded = ce.CampaignController(cfg, state_dir=tmp_path)
    out = brutalctl._tick_once(reloaded)
    assert out["actions"][0]["status"] == "TP2"
    assert reloaded.state.closed_trades == 1
    assert reloaded.state.status == "COMPLETED"


def test_tick_rejects_price_chase(tmp_path, monkeypatch):
    broker = FakeBroker([1.04])  # +0.4R, beyond entry_max_r=0.25
    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: broker)
    monkeypatch.setattr(brutalctl, "live_bankroll", lambda: 1000.0)
    monkeypatch.setattr(brutalctl, "candidates_from_action_plan", _fresh_candidate)
    ctrl = ce.CampaignController(_cfg(), state_dir=tmp_path)
    out = brutalctl._tick_once(ctrl)
    assert out["entry_skip"].startswith("PRICE_DRIFT_R")
    assert not ctrl.state.open_positions


def test_live_sl_failure_halts_if_emergency_close_fails(tmp_path, monkeypatch):
    broker = FakeBroker([1.0])
    broker.is_live = True
    broker.stop_success = False
    original = broker.place_market_order

    def market(*args, **kwargs):
        if kwargs.get("reduce_only"):
            return SimpleNamespace(
                success=False, avg_fill_price=0.0, fee_usdt=0.0,
                error="close failed", order_id="",
            )
        return original(*args, **kwargs)

    broker.place_market_order = market
    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: broker)
    monkeypatch.setattr(brutalctl, "live_bankroll", lambda: 1000.0)
    monkeypatch.setattr(brutalctl, "candidates_from_action_plan", _fresh_candidate)
    ctrl = ce.CampaignController(_cfg(live=True), state_dir=tmp_path)
    out = brutalctl._tick_once(ctrl)
    assert out["halted"][0].startswith("SL_FAILED_NAKED_POSITION")
    assert ctrl.state.status == "HALTED"


def test_missing_config_cli_is_clean_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ce, "DEFAULT_STATE_DIR", tmp_path)
    rc = brutalctl.main(["tick", "--id", "missing"])
    assert rc == 3
    assert "not armed" in capsys.readouterr().err


def test_campaign_runtime_health_exposes_reconcile_failure(tmp_path):
    ctrl = ce.CampaignController(_cfg(), state_dir=tmp_path)
    ctrl.state.last_cycle_at = ce.now_utc().isoformat(timespec="seconds")
    ctrl.state.last_cycle_result = {
        "actions": [],
        "reconcile_error": "AuthenticationError: invalid credentials",
    }
    health = brutalctl._campaign_runtime_health(ctrl)
    assert health["status"] == "degraded"
    assert health["healthy"] is False
    assert health["exchange_reconciled"] is False
    assert health["last_error"].startswith("AuthenticationError")


def test_campaign_runtime_health_requires_recent_clean_cycle(tmp_path):
    ctrl = ce.CampaignController(_cfg(), state_dir=tmp_path)
    ctrl.state.last_cycle_at = ce.now_utc().isoformat(timespec="seconds")
    ctrl.state.last_cycle_result = {"actions": []}
    health = brutalctl._campaign_runtime_health(ctrl)
    assert health["status"] == "healthy"
    assert health["healthy"] is True
    assert health["exchange_reconciled"] is True


def test_live_reconcile_failures_halt_after_bounded_retries(tmp_path, monkeypatch):
    class AuthFailBroker(FakeBroker):
        is_live = True

        def fetch_positions(self, symbol=None):
            raise RuntimeError("auth unavailable")

    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: AuthFailBroker())
    ctrl = ce.CampaignController(_cfg(live=True), state_dir=tmp_path)
    for expected in range(1, brutalctl.MAX_CONSECUTIVE_RECONCILE_FAILURES):
        out = brutalctl._tick_once(ctrl)
        assert out["consecutive_reconcile_failures"] == expected
        assert ctrl.state.status == "RUNNING"
        ctrl = ce.CampaignController(_cfg(live=True), state_dir=tmp_path)
    out = brutalctl._tick_once(ctrl)
    assert ctrl.state.status == "HALTED"
    assert ctrl.state.halt_reason == "RECONCILE_UNAVAILABLE:3"
    assert out["halted"] == ["RECONCILE_UNAVAILABLE:3"]


def test_successful_reconcile_resets_failure_counter(tmp_path, monkeypatch):
    broker = FakeBroker()
    broker.is_live = True
    monkeypatch.setattr(brutalctl, "_make_broker", lambda cfg: broker)
    monkeypatch.setattr(brutalctl, "candidates_from_action_plan", lambda: [])
    ctrl = ce.CampaignController(_cfg(live=True), state_dir=tmp_path)
    ctrl.state.last_cycle_result = {
        "actions": [],
        "reconcile_error": "temporary",
        "consecutive_reconcile_failures": 2,
    }
    ctrl.save()
    ctrl = ce.CampaignController(_cfg(live=True), state_dir=tmp_path)
    out = brutalctl._tick_once(ctrl)
    assert out["entry_skip"] == "NO_FRESH_CANDIDATE"
    assert "consecutive_reconcile_failures" not in out
    assert ctrl.state.status == "RUNNING"


def test_reload_controller_state_adopts_external_halt(tmp_path):
    cfg = _cfg()
    runner = ce.CampaignController(cfg, state_dir=tmp_path)
    external = ce.CampaignController(cfg, state_dir=tmp_path)
    external.halt("EXTERNAL_HALT")
    external.save()
    assert runner.state.status == "RUNNING"
    brutalctl._reload_controller_state(runner)
    assert runner.state.status == "HALTED"
    assert runner.state.halt_reason == "EXTERNAL_HALT"


def test_runtime_cannot_override_armed_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DEFAULT_STATE_DIR", tmp_path)
    cfg = _cfg(campaign_id="mode", live=True)
    (tmp_path / "mode.config.json").write_text(json.dumps(cfg.to_dict()))
    args = SimpleNamespace(id="mode", live=False)
    with pytest.raises(RuntimeError, match="mode mismatch"):
        brutalctl._controller(args)


def test_consume_terminal_entry_failure_blocks_repeat(tmp_path):
    ctrl = ce.CampaignController(_cfg(), state_dir=tmp_path)
    brutalctl._consume_terminal_entry_failure(
        ctrl,
        "sig-terminal",
        "BRACKET_VERIFY_FAILED_CLOSED_FLAT",
    )
    assert ctrl.state.executed_signal_ids == ["sig-terminal"]
    reloaded = ce.CampaignController(_cfg(), state_dir=tmp_path)
    assert reloaded.state.executed_signal_ids == ["sig-terminal"]
    brutalctl._consume_terminal_entry_failure(
        reloaded,
        "sig-terminal",
        "BRACKET_VERIFY_FAILED_CLOSED_FLAT",
    )
    assert reloaded.state.executed_signal_ids == ["sig-terminal"]


def test_consume_nonterminal_entry_failure_allows_retry(tmp_path):
    ctrl = ce.CampaignController(_cfg(), state_dir=tmp_path)
    brutalctl._consume_terminal_entry_failure(
        ctrl,
        "sig-retry",
        "TRANSIENT_PRE_ORDER_FAILURE",
    )
    assert ctrl.state.executed_signal_ids == []


def test_pending_filled_entry_is_adopted_with_brackets(tmp_path):
    broker = FakeBroker([1.0])
    broker.is_live = True
    cfg = _cfg(campaign_id="adopt", live=True)
    ctrl = ce.CampaignController(cfg, state_dir=tmp_path)
    entry_client = "entry-client"
    sl_client = "sl-client"
    tp_client = "tp-client"
    ctrl.set_pending_entry({
        "signal_id": "sig-adopt",
        "instrument": "AKEUSDT",
        "symbol": "AKE/USDT:USDT",
        "side": "LONG",
        "qty": 10.0,
        "client_order_id": entry_client,
        "sl_client_id": sl_client,
        "tp_client_id": tp_client,
        "entry_ref": 1.0,
        "stop_ref": 0.9,
        "submitted_at": ce.now_utc().isoformat(timespec="seconds"),
    })
    broker.orders_by_client[entry_client] = {
        "id": "exchange-entry",
        "status": "closed",
        "average": 1.0,
        "fee": {"cost": 0.01},
    }
    broker.positions["AKE/USDT:USDT"] = {
        "instrument": "AKEUSDT",
        "contracts": 10.0,
        "entry_price": 1.0,
    }
    out = brutalctl._resolve_pending_entry(ctrl, broker)
    assert out["pending_entry"] == "ADOPTED"
    assert ctrl.state.pending_entry is None
    assert len(ctrl.state.open_positions) == 1
    assert brutalctl._has_stop_and_tp(
        broker.fetch_trigger_orders("AKE/USDT:USDT")
    )


def test_pending_not_found_halts_instead_of_blind_retry(tmp_path):
    broker = FakeBroker([1.0])
    broker.is_live = True
    ctrl = ce.CampaignController(
        _cfg(campaign_id="pending-missing", live=True),
        state_dir=tmp_path,
    )
    ctrl.set_pending_entry({
        "signal_id": "sig-missing",
        "instrument": "AKEUSDT",
        "symbol": "AKE/USDT:USDT",
        "side": "LONG",
        "qty": 10.0,
        "client_order_id": "missing-client",
        "sl_client_id": "missing-sl",
        "tp_client_id": "missing-tp",
        "entry_ref": 1.0,
        "stop_ref": 0.9,
    })
    out = brutalctl._resolve_pending_entry(ctrl, broker)
    assert out["halted"].startswith("PENDING_ENTRY_NOT_FOUND")
    assert ctrl.state.status == "HALTED"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
