"""Paper broker — isolated trade-mutation layer untuk backtest harness (Step 5).

Owns trade_journal state pada in-memory DuckDB. StorageClamped attach
connection-nya supaya production code (executor.add_trade, get_trades, dll)
"talk to" paper broker tanpa modifikasi.

Fee model: per-side commission, default 0.11% (sesuai backtest.py existing
asumsi). Dipotong dari realized PnL saat close — gross_pnl − (open_notional +
close_notional) × fee_pct.

Killswitch state in-memory: backtest harness tetap pakai filesystem-based
killswitch via tmp_dir (risk_guard.check_and_engage write file di sana),
tapi PaperBroker juga menyediakan accessor optional kalau caller mau cache
state tanpa hit FS tiap cycle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd


DEFAULT_FEE_PCT = 0.11  # per-side commission, percent
DEFAULT_SLIPPAGE_BPS = 0.0  # Step 17.A3: default off untuk backward compat (existing tests/runs identik)


class PaperBroker:
    """Trade state holder untuk backtest. Bukan substitute Storage —
    StorageClamped wrapper-nya yang quack-types ke Storage interface.

    Connection di-attach via `attach_conn` (StorageClamped membangunkan
    in-memory DB lalu memanggil ini). Kalau tidak di-attach, semua
    trade-method raise.

    Slippage (Step 17.A3): cost per-side dalam bps notional. Default 0 untuk
    backward compat. New backtest runs (post-17) pakai --slippage-bps 10 (≈ 20
    bps round-trip = realistic Bybit perp high-liquidity). Step 15a sensitivity
    showed thesis FUNDING_FLIP melemah significantly @ 20bps round-trip.
    """

    def __init__(
        self,
        starting_capital: float,
        fee_pct: float = DEFAULT_FEE_PCT,
        slippage_bps_per_side: float = DEFAULT_SLIPPAGE_BPS,
    ):
        self.starting_capital = float(starting_capital)
        self.fee_pct = float(fee_pct)
        self.slippage_bps_per_side = float(slippage_bps_per_side)
        self._conn = None  # set via attach_conn
        self._killswitch_state: Optional[dict] = None

    # ─── connection wiring ─────────────────────────────────────────────

    def attach_conn(self, conn) -> None:
        """Wired by StorageClamped saat membangun in-memory DB."""
        self._conn = conn

    def _require_conn(self):
        if self._conn is None:
            raise RuntimeError(
                "PaperBroker.attach_conn() belum dipanggil — wire ke "
                "StorageClamped dulu sebelum buka/close posisi."
            )
        return self._conn

    # ─── position lifecycle ────────────────────────────────────────────

    def open_position(
        self,
        pair: str,
        direction: str,
        size: float,
        entry_price: float,
        signal_id: Optional[int] = None,
        thesis: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> int:
        """Open posisi. Return new trade_id. Fee diakumulasi saat close.

        Validasi minimal — direction ∈ LONG/SHORT/PAIR. PAIR di-allow tapi
        production fee/PnL math tidak diapply (sama dengan Storage.add_trade
        & close_trade behavior).
        """
        if direction not in ("LONG", "SHORT", "PAIR"):
            raise ValueError(
                f"direction must be LONG|SHORT|PAIR, got {direction!r}"
            )
        if size is None or float(size) <= 0:
            raise ValueError(f"size must be > 0, got {size!r}")
        if entry_price is None or float(entry_price) <= 0:
            raise ValueError(f"entry_price must be > 0, got {entry_price!r}")

        ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)

        conn = self._require_conn()
        row = conn.execute(
            "INSERT INTO trade_journal "
            "(ts, pair, direction, entry_price, size, thesis, status, signal_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?) RETURNING id",
            [ts, pair, direction, float(entry_price), float(size),
             thesis, signal_id],
        ).fetchone()
        return int(row[0])

    def close_position(
        self,
        trade_id: int,
        exit_price: float,
        exit_reason: str,
        ts: Optional[datetime] = None,
    ) -> dict:
        """Close posisi. Return dict {id, direction, entry_price, exit_ts,
        exit_price, pnl}. PnL = gross − fee_total. Raise kalau trade tidak
        ada / sudah CLOSED / direction PAIR (no auto-PnL math).
        """
        conn = self._require_conn()
        cur = conn.execute(
            "SELECT id, direction, entry_price, size, status "
            "FROM trade_journal WHERE id=?",
            [trade_id],
        ).fetchone()
        if cur is None:
            raise ValueError(f"trade #{trade_id} not found")
        _id, direction, entry, size, status = cur
        if status == "CLOSED":
            raise ValueError(f"trade #{_id} already CLOSED")

        pnl: Optional[float] = None
        if direction in ("LONG", "SHORT") and entry is not None and size is not None:
            sign = 1 if direction == "LONG" else -1
            gross = (float(exit_price) - float(entry)) * float(size) * sign
            open_notional = abs(float(entry) * float(size))
            close_notional = abs(float(exit_price) * float(size))
            total_fee = (open_notional + close_notional) * (self.fee_pct / 100.0)
            total_slippage = (
                (open_notional + close_notional) * (self.slippage_bps_per_side / 10000.0)
            )
            pnl = gross - total_fee - total_slippage
        # PAIR: skip auto-calc

        ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)
        notes = exit_reason
        conn.execute(
            "UPDATE trade_journal "
            "SET status='CLOSED', exit_ts=?, exit_price=?, pnl=?, notes=? "
            "WHERE id=?",
            [ts, float(exit_price), pnl, notes, trade_id],
        )
        return {
            "id": int(trade_id),
            "direction": direction,
            "entry_price": float(entry) if entry is not None else None,
            "exit_ts": ts,
            "exit_price": float(exit_price),
            "pnl": float(pnl) if pnl is not None else None,
        }

    # ─── reads ─────────────────────────────────────────────────────────

    def get_open_positions(self) -> pd.DataFrame:
        return self._require_conn().execute(
            "SELECT id, ts, pair, direction, entry_price, size, thesis, "
            "status, signal_id FROM trade_journal WHERE status='OPEN' "
            "ORDER BY ts"
        ).fetchdf()

    def get_closed_positions(self) -> pd.DataFrame:
        return self._require_conn().execute(
            "SELECT id, ts, pair, direction, entry_price, size, thesis, "
            "status, exit_ts, exit_price, pnl, notes, signal_id "
            "FROM trade_journal WHERE status='CLOSED' ORDER BY exit_ts"
        ).fetchdf()

    def equity_now(self) -> float:
        """starting_capital + sum realized PnL pada CLOSED trades.

        TIDAK exclude `[TEST_ARTIFACT` — di backtest broker semua trade
        berasal dari sinyal backtest (no test contamination).
        """
        row = self._require_conn().execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trade_journal "
            "WHERE status='CLOSED' AND pnl IS NOT NULL"
        ).fetchone()
        return self.starting_capital + float(row[0] or 0.0)

    def reset_state(self) -> None:
        """Drop semua trades + reset killswitch cache. Untuk run baru."""
        if self._conn is not None:
            self._conn.execute("DELETE FROM trade_journal")
        self._killswitch_state = None

    # ─── killswitch passthrough (optional cache) ───────────────────────

    def get_killswitch_state(self) -> Optional[dict]:
        return self._killswitch_state

    def set_killswitch_state(self, state: Optional[dict]) -> None:
        self._killswitch_state = state

    # ─── fee helper (static) ───────────────────────────────────────────

    @staticmethod
    def apply_fee(notional: float, fee_pct: float = DEFAULT_FEE_PCT) -> float:
        """Compute fee absolute dari notional × fee_pct. Helper untuk caller
        yang mau preview fee tanpa membuka posisi.
        """
        return abs(float(notional)) * (float(fee_pct) / 100.0)

    @staticmethod
    def apply_slippage(notional: float, bps_per_side: float = DEFAULT_SLIPPAGE_BPS) -> float:
        """Compute slippage cost untuk notional × bps_per_side. Step 17.A3."""
        return abs(float(notional)) * (float(bps_per_side) / 10000.0)
