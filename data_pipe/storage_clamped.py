"""StorageClamped — Storage subclass dengan look-ahead bias prevention via
view-based time clamping. Backbone Step 5 backtest harness.

Architecture:
  - In-memory DuckDB. Tidak buka file production DB (read-only snapshot
    di-load di __init__).
  - Raw historical tables: `_ohlcv_raw`, `_funding_rate_raw`, `_news_raw`.
  - VIEWs `ohlcv` / `funding_rate` / `news` JOIN dengan `_as_of` table →
    semua read otomatis ter-clamp ke `as_of` cutoff. Production code yang
    pakai `storage._conn.execute("SELECT ... FROM ohlcv WHERE ...")` tidak
    perlu modifikasi.
  - `signal_events` & `trade_journal` tetap real TABLEs (writable). Sinyal
    historis di-load saat init; cycle-time emisi (RISK_KILLSWITCH dari
    risk_guard) tetap berhasil. `get_signals` di-override untuk clamp manual
    + cutoff `since_hours` relatif ke `as_of` (bukan wall clock).
  - `add_trade` / `close_trade` di-override → delegate ke PaperBroker untuk
    fee math (production Storage tidak charge fee). Connection di-share
    via `broker.attach_conn(self._conn)` sehingga semua read direct via
    `_conn` tetap visible.

Catatan look-ahead:
  - News VIEW pakai `published_at <= as_of` (bukan `fetched_at`) — yang
    relevan untuk feed liveness window.
  - View dipilih di atas trigger/RLS karena DuckDB belum support either,
    dan view-rewrite cukup transparent untuk semua read pattern.
  - `set_as_of(ts)` advance backtest clock; harus dipanggil tiap cycle
    sebelum `run_advisor` / `run_executor`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import duckdb
import pandas as pd

from .paper_broker import PaperBroker
from .storage import Storage


class StorageClamped(Storage):
    """Storage adapter for backtest. Bypass parent __init__ (no file DB)."""

    # Sentinel as-of saat belum di-set: jauh di masa depan supaya semua
    # historical rows visible (defensive — caller seharusnya selalu set_as_of
    # sebelum cycle pertama).
    _SENTINEL_AS_OF = datetime(9999, 12, 31)

    def __init__(self, source: Storage, broker: PaperBroker):
        # Jangan call super().__init__ — itu buka file DB.
        self.db_path = Path(":memory:")
        self.read_only = False
        self._conn = duckdb.connect(":memory:")
        self._broker = broker
        self._as_of: Optional[datetime] = None

        self._build_schema()
        self._load_from(source)
        broker.attach_conn(self._conn)

    # ─── schema build ──────────────────────────────────────────────────

    def _build_schema(self) -> None:
        c = self._conn

        # Raw historical tables (mirror production schema, renamed prefix).
        c.execute(
            "CREATE TABLE _ohlcv_raw ("
            "  exchange VARCHAR NOT NULL, symbol VARCHAR NOT NULL,"
            "  timeframe VARCHAR NOT NULL, ts TIMESTAMP NOT NULL,"
            "  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,"
            "  PRIMARY KEY (exchange, symbol, timeframe, ts))"
        )
        c.execute(
            "CREATE TABLE _funding_rate_raw ("
            "  exchange VARCHAR NOT NULL, symbol VARCHAR NOT NULL,"
            "  ts TIMESTAMP NOT NULL, funding DOUBLE NOT NULL, mark_price DOUBLE,"
            "  PRIMARY KEY (exchange, symbol, ts))"
        )
        c.execute(
            "CREATE TABLE _news_raw ("
            "  id VARCHAR NOT NULL, source VARCHAR NOT NULL,"
            "  title VARCHAR NOT NULL, url VARCHAR NOT NULL,"
            "  published_at TIMESTAMP NOT NULL, fetched_at TIMESTAMP NOT NULL,"
            "  symbols VARCHAR, PRIMARY KEY (id))"
        )

        # As-of pin: single-row table yang VIEW join. Default = sentinel
        # (semua row visible) supaya CREATE VIEW tidak crash kalau ada
        # accidental query sebelum set_as_of.
        c.execute("CREATE TABLE _as_of (ts TIMESTAMP NOT NULL)")
        c.execute("INSERT INTO _as_of VALUES (?)", [self._SENTINEL_AS_OF])

        # Clamped VIEWs — namanya match production tables jadi production
        # SQL `SELECT ... FROM ohlcv WHERE ...` rewrite ke clamped read
        # tanpa modifikasi.
        c.execute(
            "CREATE VIEW ohlcv AS "
            "SELECT r.* FROM _ohlcv_raw r, _as_of a WHERE r.ts <= a.ts"
        )
        c.execute(
            "CREATE VIEW funding_rate AS "
            "SELECT r.* FROM _funding_rate_raw r, _as_of a WHERE r.ts <= a.ts"
        )
        c.execute(
            "CREATE VIEW news AS "
            "SELECT r.* FROM _news_raw r, _as_of a WHERE r.published_at <= a.ts"
        )

        # signal_events: real table (writable — risk_guard upserts at runtime).
        # Clamping handled in get_signals override; direct lookups via
        # get_signal_by_num tidak butuh clamp (sig_num references sudah
        # gated upstream oleh get_signals yang advisor pakai).
        c.execute("CREATE SEQUENCE seq_signal_num START 1")
        c.execute(
            "CREATE TABLE signal_events ("
            "  id VARCHAR NOT NULL,"
            "  sig_num INTEGER DEFAULT nextval('seq_signal_num'),"
            "  ts TIMESTAMP NOT NULL, event_type VARCHAR NOT NULL,"
            "  symbol VARCHAR, severity VARCHAR NOT NULL, payload VARCHAR,"
            "  PRIMARY KEY (id))"
        )

        # trade_journal: real table, owned by PaperBroker. Schema mirror
        # production; PaperBroker writes via shared _conn.
        c.execute("CREATE SEQUENCE seq_trade_journal_id START 1")
        c.execute(
            "CREATE TABLE trade_journal ("
            "  id INTEGER NOT NULL DEFAULT nextval('seq_trade_journal_id'),"
            "  ts TIMESTAMP NOT NULL, pair VARCHAR NOT NULL,"
            "  direction VARCHAR NOT NULL, entry_price DOUBLE, size DOUBLE,"
            "  thesis VARCHAR, status VARCHAR NOT NULL DEFAULT 'OPEN',"
            "  exit_ts TIMESTAMP, exit_price DOUBLE, pnl DOUBLE, notes VARCHAR,"
            "  signal_id INTEGER, PRIMARY KEY (id))"
        )

    # ─── data load ─────────────────────────────────────────────────────

    def _load_from(self, source: Storage) -> None:
        """Copy historical rows from production Storage into raw/in-memory tables."""
        c = self._conn

        df = source._conn.execute(
            "SELECT exchange, symbol, timeframe, ts, open, high, low, close, volume "
            "FROM ohlcv"
        ).fetchdf()
        if not df.empty:
            c.register("df_tmp", df)
            c.execute("INSERT INTO _ohlcv_raw SELECT * FROM df_tmp")
            c.unregister("df_tmp")

        df = source._conn.execute(
            "SELECT exchange, symbol, ts, funding, mark_price FROM funding_rate"
        ).fetchdf()
        if not df.empty:
            c.register("df_tmp", df)
            c.execute("INSERT INTO _funding_rate_raw SELECT * FROM df_tmp")
            c.unregister("df_tmp")

        df = source._conn.execute(
            "SELECT id, source, title, url, published_at, fetched_at, symbols FROM news"
        ).fetchdf()
        if not df.empty:
            c.register("df_tmp", df)
            c.execute("INSERT INTO _news_raw SELECT * FROM df_tmp")
            c.unregister("df_tmp")

        df = source._conn.execute(
            "SELECT id, ts, event_type, symbol, severity, payload "
            "FROM signal_events ORDER BY ts"
        ).fetchdf()
        if not df.empty:
            c.register("df_tmp", df)
            c.execute(
                "INSERT INTO signal_events (id, ts, event_type, symbol, severity, payload) "
                "SELECT id, ts, event_type, symbol, severity, payload FROM df_tmp"
            )
            c.unregister("df_tmp")
            # Bump sequence so cycle-time emissions don't collide with loaded sig_num.
            max_sig = int(c.execute(
                "SELECT COALESCE(MAX(sig_num), 0) FROM signal_events"
            ).fetchone()[0])
            for _ in range(max_sig):
                c.execute("SELECT nextval('seq_signal_num')")

    # ─── as-of control ─────────────────────────────────────────────────

    def set_as_of(self, ts: datetime) -> None:
        """Advance backtest clock. Stored naive UTC. All views re-clamp."""
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        self._as_of = ts
        self._conn.execute("UPDATE _as_of SET ts = ?", [ts])

    def get_as_of(self) -> Optional[datetime]:
        return self._as_of

    # ─── overrides: clamping & broker delegation ───────────────────────

    def get_signals(
        self,
        limit: int = 50,
        event_type: str | None = None,
        symbol: str | None = None,
        since_hours: int | None = None,
    ) -> pd.DataFrame:
        """Override: cap ts <= as_of, dan since_hours window relative ke as_of
        (bukan wall clock) supaya backtest deterministic.
        """
        sql = (
            "SELECT sig_num, ts, event_type, symbol, severity, payload "
            "FROM signal_events WHERE 1=1"
        )
        params: list = []
        if self._as_of is not None:
            sql += " AND ts <= ?"
            params.append(self._as_of)
        if event_type:
            sql += " AND event_type=?"
            params.append(event_type)
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if since_hours is not None:
            anchor = (
                self._as_of if self._as_of is not None
                else datetime.now(timezone.utc).replace(tzinfo=None)
            )
            cutoff = anchor - timedelta(hours=int(since_hours))
            sql += " AND ts >= ?"
            params.append(cutoff)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchdf()

    def add_trade(
        self,
        pair: str,
        direction: str,
        entry_price: float | None = None,
        size: float | None = None,
        thesis: str | None = None,
        signal_id: int | None = None,
        ts: datetime | None = None,
    ) -> int:
        """Override: delegate to PaperBroker (fee accumulated at close).

        Validate signal_id parity dengan parent: signal harus exist.
        """
        if signal_id is not None and self.get_signal_by_num(signal_id) is None:
            raise ValueError(f"signal #{signal_id} not found in signal_events")
        ts_eff = (
            ts or self._as_of
            or datetime.now(timezone.utc).replace(tzinfo=None)
        )
        return self._broker.open_position(
            pair=pair,
            direction=direction,
            size=float(size) if size is not None else 0.0,
            entry_price=float(entry_price) if entry_price is not None else 0.0,
            signal_id=signal_id,
            thesis=thesis,
            ts=ts_eff,
        )

    def close_trade(
        self,
        trade_id: int,
        exit_price: float | None = None,
        notes: str | None = None,
        exit_ts: datetime | None = None,
    ) -> dict:
        """Override: delegate to PaperBroker. Fee + PnL computed there."""
        ts_eff = (
            exit_ts or self._as_of
            or datetime.now(timezone.utc).replace(tzinfo=None)
        )
        return self._broker.close_position(
            trade_id=trade_id,
            exit_price=float(exit_price) if exit_price is not None else 0.0,
            exit_reason=notes or "",
            ts=ts_eff,
        )

    # ─── upsert overrides: historical tables frozen di backtest ────────

    def upsert_ohlcv(self, rows: Sequence[dict]) -> int:
        return 0

    def upsert_funding(self, rows: Sequence[dict]) -> int:
        return 0

    def upsert_news(self, rows: Sequence[dict]) -> int:
        return 0

    # upsert_signals: inherit. risk_guard.check_and_engage emits RISK_KILLSWITCH
    # via Storage.upsert_signals → INSERT INTO signal_events (real table).
    # Itu visible immediately untuk get_signals subsequent calls.

    def close(self) -> None:
        self._conn.close()
