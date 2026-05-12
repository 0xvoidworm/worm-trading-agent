"""DuckDB storage layer.

DuckDB dipilih karena: columnar (bagus untuk OHLCV scan), zero-ops (file-based),
Parquet-native (history dump gampang), dan handle 100M+ rows di laptop tanpa
issue. Tidak ada server process = tidak ada failure mode tambahan.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import duckdb
import pandas as pd


class StorageLocked(RuntimeError):
    """Raised setelah retry budget habis — writer sedang aktif."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv (
    exchange   VARCHAR  NOT NULL,
    symbol     VARCHAR  NOT NULL,
    timeframe  VARCHAR  NOT NULL,
    ts         TIMESTAMP NOT NULL,   -- UTC, bar open time
    open       DOUBLE,
    high       DOUBLE,
    low        DOUBLE,
    close      DOUBLE,
    volume     DOUBLE,
    PRIMARY KEY (exchange, symbol, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS funding_rate (
    exchange    VARCHAR  NOT NULL,
    symbol      VARCHAR  NOT NULL,
    ts          TIMESTAMP NOT NULL,
    funding     DOUBLE   NOT NULL,   -- 8h rate, decimal (0.0005 = 0.05%)
    mark_price  DOUBLE,
    PRIMARY KEY (exchange, symbol, ts)
);

CREATE TABLE IF NOT EXISTS news (
    id            VARCHAR  NOT NULL,   -- hash(url) untuk dedup
    source        VARCHAR  NOT NULL,   -- coindesk, cointelegraph, dsb
    title         VARCHAR  NOT NULL,
    url           VARCHAR  NOT NULL,
    published_at  TIMESTAMP NOT NULL,  -- UTC
    fetched_at    TIMESTAMP NOT NULL,
    symbols       VARCHAR,             -- comma-separated: "BTC,ETH"
    PRIMARY KEY (id)
);

CREATE SEQUENCE IF NOT EXISTS seq_signal_num START 1;

CREATE TABLE IF NOT EXISTS signal_events (
    id          VARCHAR  NOT NULL,   -- hash(type|symbol|ts|payload) untuk dedup
    sig_num     INTEGER  DEFAULT nextval('seq_signal_num'),  -- user-friendly sequential id
    ts          TIMESTAMP NOT NULL,
    event_type  VARCHAR  NOT NULL,   -- REGIME_CHANGE, FUNDING_EXTREME, Z_SPIKE, FUNDING_FLIP
    symbol      VARCHAR,             -- atau pair "A|B"
    severity    VARCHAR  NOT NULL,   -- info, warn, critical
    payload     VARCHAR,             -- freeform text / JSON-ish
    PRIMARY KEY (id)
);


CREATE SEQUENCE IF NOT EXISTS seq_trade_journal_id START 1;

CREATE TABLE IF NOT EXISTS trade_journal (
    id           INTEGER  NOT NULL DEFAULT nextval('seq_trade_journal_id'),
    ts           TIMESTAMP NOT NULL,  -- trade open time (UTC)
    pair         VARCHAR   NOT NULL,  -- "SOL/USDT" utk single, "SOL/USDT|ETH/USDT" utk pair trade
    direction    VARCHAR   NOT NULL,  -- LONG, SHORT, PAIR
    entry_price  DOUBLE,              -- harga masuk (nullable untuk PAIR, bisa pake spread z)
    size         DOUBLE,              -- notional / unit, freeform
    thesis       VARCHAR,             -- alasan trade, referensi sinyal
    status       VARCHAR   NOT NULL DEFAULT 'OPEN',  -- OPEN, CLOSED
    exit_ts      TIMESTAMP,           -- nullable, diisi saat close
    exit_price   DOUBLE,              -- nullable
    pnl          DOUBLE,              -- nullable, dihitung saat close
    notes        VARCHAR,             -- catatan akhir / postmortem
    signal_id    INTEGER,             -- nullable, FK ke signal_events.sig_num
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ts ON ohlcv(ts);
CREATE INDEX IF NOT EXISTS idx_funding_ts ON funding_rate(ts);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news(published_at);
CREATE INDEX IF NOT EXISTS idx_sig_ts ON signal_events(ts);
CREATE INDEX IF NOT EXISTS idx_trade_status ON trade_journal(status);
CREATE INDEX IF NOT EXISTS idx_trade_ts ON trade_journal(ts);
"""


class Storage:
    """Thin wrapper around DuckDB connection with idempotent upserts.

    Upsert pattern: INSERT OR REPLACE pakai composite PK. Aman untuk re-run
    backfill tanpa duplikasi.
    """

    def __init__(
        self,
        db_path: str | os.PathLike = None,
        read_only: bool = False,
        max_retries: int = 10,
        retry_delay: float = 0.5,
    ):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "data" / "market.duckdb"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = read_only
        # Retry on lock contention: daemon + dashboard run as separate processes,
        # both expected to hold connection briefly. Collision window small,
        # exponential backoff cukup.
        last_err = None
        for attempt in range(max_retries):
            try:
                if read_only and self.db_path.exists():
                    self._conn = duckdb.connect(str(self.db_path), read_only=True)
                else:
                    self._conn = duckdb.connect(str(self.db_path))
                    self._conn.execute(_SCHEMA)
                    self._migrate()
                return
            except duckdb.IOException as e:
                last_err = e
                if "lock" not in str(e).lower():
                    raise
                # sleep lebih lama tiap retry, maks ~5 detik total di default
                time.sleep(retry_delay * (1.5 ** attempt))
        raise StorageLocked(f"could not acquire DB lock after {max_retries} retries: {last_err}")

    def _migrate(self):
        """Imperative migration untuk DB lama. Hanya jalan kalau kolom belum ada.

        Tidak boleh di-embed di _SCHEMA sebagai `ALTER ... IF NOT EXISTS ... DEFAULT
        nextval(...)`: DuckDB tetap evaluate DEFAULT-nya tiap parse meskipun ALTER
        di-skip, sehingga sequence seq_signal_num ter-burn tiap connection open.
        """
        cols = [r[1] for r in self._conn.execute(
            "PRAGMA table_info(signal_events)"
        ).fetchall()]
        if "sig_num" not in cols:
            self._conn.execute(
                "ALTER TABLE signal_events ADD COLUMN sig_num INTEGER "
                "DEFAULT nextval('seq_signal_num')"
            )

        cols_tr = [r[1] for r in self._conn.execute(
            "PRAGMA table_info(trade_journal)"
        ).fetchall()]
        if "signal_id" not in cols_tr:
            self._conn.execute(
                "ALTER TABLE trade_journal ADD COLUMN signal_id INTEGER"
            )

    # --- core ops ---
    def upsert_ohlcv(self, rows: Sequence[dict]) -> int:
        """rows: list of dict{exchange,symbol,timeframe,ts,open,high,low,close,volume}"""
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        # Register DataFrame for DuckDB and run INSERT OR REPLACE
        self._conn.register("df_tmp", df)
        self._conn.execute(
            "INSERT OR REPLACE INTO ohlcv "
            "SELECT exchange, symbol, timeframe, ts, open, high, low, close, volume FROM df_tmp"
        )
        self._conn.unregister("df_tmp")
        return len(rows)

    def upsert_news(self, rows: Sequence[dict]) -> int:
        """rows: list of dict{id,source,title,url,published_at,fetched_at,symbols}"""
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        self._conn.register("df_tmp", df)
        self._conn.execute(
            "INSERT OR REPLACE INTO news "
            "SELECT id, source, title, url, published_at, fetched_at, symbols FROM df_tmp"
        )
        self._conn.unregister("df_tmp")
        return len(rows)

    def get_news(self, limit: int = 20) -> pd.DataFrame:
        sql = (
            "SELECT published_at, source, title, url, symbols FROM news "
            "ORDER BY published_at DESC LIMIT ?"
        )
        return self._conn.execute(sql, [limit]).fetchdf()

    def upsert_signals(self, rows: Sequence[dict]) -> int:
        """rows: list of dict{id, ts, event_type, symbol, severity, payload}.

        ON CONFLICT DO NOTHING: existing row (same hash id) tidak disentuh —
        penting supaya `sig_num` surrogate stabil sepanjang hidup DB. Detector
        rerun → same hash → skipped, sig_num tetap.
        """
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        self._conn.register("df_tmp", df)
        self._conn.execute(
            "INSERT INTO signal_events (id, ts, event_type, symbol, severity, payload) "
            "SELECT id, ts, event_type, symbol, severity, payload FROM df_tmp "
            "ON CONFLICT (id) DO NOTHING"
        )
        self._conn.unregister("df_tmp")
        return len(rows)

    def get_signals(
        self,
        limit: int = 50,
        event_type: str | None = None,
        symbol: str | None = None,
        since_hours: int | None = None,
    ) -> pd.DataFrame:
        sql = "SELECT sig_num, ts, event_type, symbol, severity, payload FROM signal_events WHERE 1=1"
        params: list = []
        if event_type:
            sql += " AND event_type=?"
            params.append(event_type)
        if symbol:
            sql += " AND symbol=?"
            params.append(symbol)
        if since_hours is not None:
            # Hitung cutoff di Python (naive UTC) supaya comparison konsisten
            # dengan ts yang disimpan naive UTC — CURRENT_TIMESTAMP DuckDB
            # bawa timezone session dan bisa salah shift.
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=int(since_hours))
            sql += " AND ts >= ?"
            params.append(cutoff)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return self._conn.execute(sql, params).fetchdf()

    def upsert_funding(self, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        self._conn.register("df_tmp", df)
        self._conn.execute(
            "INSERT OR REPLACE INTO funding_rate "
            "SELECT exchange, symbol, ts, funding, mark_price FROM df_tmp"
        )
        self._conn.unregister("df_tmp")
        return len(rows)

    # --- reads ---
    def get_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str = "1h",
        limit: int | None = None,
    ) -> pd.DataFrame:
        sql = (
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? ORDER BY ts"
        )
        params = [exchange, symbol, timeframe]
        if limit is not None:
            sql += " DESC LIMIT ?"
            params.append(limit)
        df = self._conn.execute(sql, params).fetchdf()
        if limit is not None:
            df = df.iloc[::-1].reset_index(drop=True)  # re-ascend setelah LIMIT
        return df

    def get_funding(
        self,
        exchange: str,
        symbol: str,
        limit: int | None = None,
    ) -> pd.DataFrame:
        sql = (
            "SELECT ts, funding, mark_price FROM funding_rate "
            "WHERE exchange=? AND symbol=? ORDER BY ts"
        )
        params = [exchange, symbol]
        if limit is not None:
            sql += " DESC LIMIT ?"
            params.append(limit)
        df = self._conn.execute(sql, params).fetchdf()
        if limit is not None:
            df = df.iloc[::-1].reset_index(drop=True)
        return df

    def get_ohlcv_around(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        center_ts: datetime,
        before: int = 12,
        after: int = 12,
    ) -> dict:
        """Ambil OHLCV window di sekitar satu timestamp.

        Returns dict dengan key:
          - before : DataFrame (ts asc) — `before` bar di sebelum signal bar
          - signal : dict (open,high,low,close,volume,ts) atau None —
                     bar yang ts-nya <= center_ts terakhir (bar yang "memuat" signal)
          - after  : DataFrame (ts asc) — `after` bar setelah signal bar
        """
        # Signal bar = bar dengan ts <= center_ts yang paling dekat
        sig_row = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            [exchange, symbol, timeframe, center_ts],
        ).fetchone()
        if sig_row is None:
            return {"before": pd.DataFrame(), "signal": None, "after": pd.DataFrame()}
        sig_ts = sig_row[0]
        signal = {
            "ts": sig_ts, "open": sig_row[1], "high": sig_row[2],
            "low": sig_row[3], "close": sig_row[4], "volume": sig_row[5],
        }

        before_df = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND ts < ? "
            "ORDER BY ts DESC LIMIT ?",
            [exchange, symbol, timeframe, sig_ts, before],
        ).fetchdf().iloc[::-1].reset_index(drop=True)

        after_df = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM ohlcv "
            "WHERE exchange=? AND symbol=? AND timeframe=? AND ts > ? "
            "ORDER BY ts LIMIT ?",
            [exchange, symbol, timeframe, sig_ts, after],
        ).fetchdf()

        return {"before": before_df, "signal": signal, "after": after_df}

    def get_news_in_window(
        self,
        center_ts: datetime,
        before_hours: int = 3,
        after_hours: int = 3,
    ) -> pd.DataFrame:
        """News yang published_at di window [center-before, center+after] jam."""
        return self._conn.execute(
            "SELECT published_at, source, title, url, symbols FROM news "
            "WHERE published_at >= ? - INTERVAL (?) HOUR "
            "  AND published_at <= ? + INTERVAL (?) HOUR "
            "ORDER BY published_at",
            [center_ts, before_hours, center_ts, after_hours],
        ).fetchdf()

    def get_trades_by_signal(self, sig_num: int) -> pd.DataFrame:
        """Semua trade yang di-link ke sig_num ini."""
        return self._conn.execute(
            "SELECT id, ts, pair, direction, entry_price, size, thesis, status, "
            "exit_ts, exit_price, pnl, notes FROM trade_journal "
            "WHERE signal_id=? ORDER BY ts",
            [sig_num],
        ).fetchdf()

    def latest_ohlcv_ts(self, exchange: str, symbol: str, timeframe: str) -> pd.Timestamp | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=?",
            [exchange, symbol, timeframe],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def latest_funding_ts(self, exchange: str, symbol: str) -> pd.Timestamp | None:
        row = self._conn.execute(
            "SELECT MAX(ts) FROM funding_rate WHERE exchange=? AND symbol=?",
            [exchange, symbol],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    # --- trade journal ---
    def get_signal_by_num(self, sig_num: int) -> dict | None:
        """Lookup signal via surrogate sig_num. Return dict atau None."""
        row = self._conn.execute(
            "SELECT sig_num, ts, event_type, symbol, severity, payload "
            "FROM signal_events WHERE sig_num=?",
            [sig_num],
        ).fetchone()
        if row is None:
            return None
        return {
            "sig_num": row[0],
            "ts": row[1],
            "event_type": row[2],
            "symbol": row[3],
            "severity": row[4],
            "payload": row[5],
        }

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
        """Open a new trade. Returns new trade id.

        signal_id (kalau diberikan) divalidasi — raise ValueError kalau
        sig_num tidak ada di signal_events. Mencegah dangling reference.
        """
        if direction not in ("LONG", "SHORT", "PAIR"):
            raise ValueError(f"direction must be LONG|SHORT|PAIR, got {direction!r}")
        if signal_id is not None and self.get_signal_by_num(signal_id) is None:
            raise ValueError(f"signal #{signal_id} not found in signal_events")
        ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
        row = self._conn.execute(
            "INSERT INTO trade_journal "
            "(ts, pair, direction, entry_price, size, thesis, status, signal_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?) RETURNING id",
            [ts, pair, direction, entry_price, size, thesis, signal_id],
        ).fetchone()
        return int(row[0])

    def close_trade(
        self,
        trade_id: int,
        exit_price: float | None = None,
        notes: str | None = None,
        exit_ts: datetime | None = None,
    ) -> dict:
        """Close trade. Auto-compute PnL untuk LONG/SHORT jika entry+size+exit tersedia.
        PAIR skip auto-calc (spread-based, user fill via notes). Raises ValueError
        kalau id tidak ada atau sudah CLOSED.
        """
        cur = self._conn.execute(
            "SELECT id, direction, entry_price, size, status FROM trade_journal WHERE id=?",
            [trade_id],
        ).fetchone()
        if cur is None:
            raise ValueError(f"trade id {trade_id} not found")
        _id, direction, entry, size, status = cur
        if status == "CLOSED":
            raise ValueError(f"trade #{_id} already CLOSED")

        pnl = None
        if exit_price is not None and entry is not None and size is not None:
            if direction == "LONG":
                pnl = (exit_price - entry) * size
            elif direction == "SHORT":
                pnl = (entry - exit_price) * size
            # PAIR: skip auto-calc

        exit_ts = exit_ts or datetime.now(timezone.utc).replace(tzinfo=None)
        self._conn.execute(
            "UPDATE trade_journal "
            "SET status='CLOSED', exit_ts=?, exit_price=?, pnl=?, notes=? "
            "WHERE id=?",
            [exit_ts, exit_price, pnl, notes, trade_id],
        )
        return {
            "id": trade_id,
            "direction": direction,
            "entry_price": entry,
            "exit_ts": exit_ts,
            "exit_price": exit_price,
            "pnl": pnl,
        }

    def get_trades(
        self,
        status: str | None = "OPEN",
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Ambil trade. status=None artinya semua (OPEN + CLOSED)."""
        sql = (
            "SELECT id, ts, pair, direction, entry_price, size, thesis, status, "
            "exit_ts, exit_price, pnl, notes, signal_id "
            "FROM trade_journal WHERE 1=1"
        )
        params: list = []
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self._conn.execute(sql, params).fetchdf()

    def journal_stats(self) -> dict:
        """Kalkulasi performance metric dari trade_journal yang CLOSED.

        Win rate, total PnL net, best trade, worst trade, avg PnL per trade,
        jumlah open/closed, count yang PnL-nya None (PAIR trade atau missing data).
        Semua hitung berdasarkan kolom `pnl` — jadi PAIR trade tanpa manual PnL
        fill akan di-exclude dari win rate (dihitung di `closed_no_pnl`).
        """
        open_n = self._conn.execute(
            "SELECT COUNT(*) FROM trade_journal WHERE status='OPEN'"
        ).fetchone()[0]
        closed_n = self._conn.execute(
            "SELECT COUNT(*) FROM trade_journal WHERE status='CLOSED'"
        ).fetchone()[0]

        # Metric hanya dari CLOSED dengan pnl not null
        agg = self._conn.execute(
            "SELECT "
            "  COUNT(*) AS n, "
            "  SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins, "
            "  SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses, "
            "  COALESCE(SUM(pnl), 0) AS total_pnl, "
            "  COALESCE(AVG(pnl), 0) AS avg_pnl "
            "FROM trade_journal WHERE status='CLOSED' AND pnl IS NOT NULL"
        ).fetchone()
        n, wins, losses, total_pnl, avg_pnl = agg

        closed_no_pnl = self._conn.execute(
            "SELECT COUNT(*) FROM trade_journal WHERE status='CLOSED' AND pnl IS NULL"
        ).fetchone()[0]

        best = self._conn.execute(
            "SELECT id, pair, direction, pnl FROM trade_journal "
            "WHERE status='CLOSED' AND pnl IS NOT NULL "
            "ORDER BY pnl DESC LIMIT 1"
        ).fetchone()
        worst = self._conn.execute(
            "SELECT id, pair, direction, pnl FROM trade_journal "
            "WHERE status='CLOSED' AND pnl IS NOT NULL "
            "ORDER BY pnl ASC LIMIT 1"
        ).fetchone()

        win_rate = (wins / n) if n else 0.0

        def _fmt_trade(row):
            if row is None:
                return None
            return {"id": int(row[0]), "pair": row[1], "direction": row[2], "pnl": float(row[3])}

        return {
            "open": open_n,
            "closed": closed_n,
            "closed_no_pnl": closed_no_pnl,
            "scored": n or 0,
            "wins": wins or 0,
            "losses": losses or 0,
            "win_rate": win_rate,
            "total_pnl": float(total_pnl),
            "avg_pnl": float(avg_pnl),
            "best": _fmt_trade(best),
            "worst": _fmt_trade(worst),
        }

    def stats(self) -> dict:
        ohlcv_n = self._conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        funding_n = self._conn.execute("SELECT COUNT(*) FROM funding_rate").fetchone()[0]
        news_n = self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        signals_n = self._conn.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
        trades_open = self._conn.execute(
            "SELECT COUNT(*) FROM trade_journal WHERE status='OPEN'"
        ).fetchone()[0]
        symbols = self._conn.execute(
            "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"
        ).fetchdf()["symbol"].tolist()
        return {
            "ohlcv_rows": ohlcv_n,
            "funding_rows": funding_n,
            "news_rows": news_n,
            "signal_rows": signals_n,
            "trades_open": trades_open,
            "symbols": symbols,
            "db_path": str(self.db_path),
        }

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
