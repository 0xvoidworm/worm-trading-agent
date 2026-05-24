"""Streamlit web dashboard untuk Trading Agent.

Read-only view atas market.duckdb + Bybit testnet probe. Aman dijalankan
paralel dengan daemon (DuckDB read_only mode).

Launch:
    streamlit run dashboard/streamlit_app.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "market.duckdb"
HEARTBEAT_PATH = REPO_ROOT / "data" / "daemon.heartbeat"
CONFIG_PATH = REPO_ROOT / "config" / "pairs.yaml"
SECRETS_PATH = Path.home() / ".secrets" / "binance.env"

sys.path.insert(0, str(REPO_ROOT))

WINDOW_HOURS = {
    "1d": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
    "90d": 24 * 90,
    "1y": 24 * 365,
    "3y": 24 * 365 * 3,
}


# ---------- DB helpers ----------

@st.cache_resource
def get_conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_data(ttl=30)
def fetch_symbols() -> list[str]:
    return [r[0] for r in get_conn().execute(
        "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol"
    ).fetchall()]


@st.cache_data(ttl=30)
def fetch_db_stats() -> dict:
    conn = get_conn()
    stats: dict = {}
    for tbl in ("ohlcv", "funding_rate", "news", "signal_events", "trade_journal"):
        stats[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    stats["last_ohlcv_ts"] = conn.execute("SELECT MAX(ts) FROM ohlcv").fetchone()[0]
    stats["last_funding_ts"] = conn.execute("SELECT MAX(ts) FROM funding_rate").fetchone()[0]
    stats["last_signal_ts"] = conn.execute("SELECT MAX(ts) FROM signal_events").fetchone()[0]
    stats["db_size_mb"] = DB_PATH.stat().st_size / (1024 * 1024)
    return stats


@st.cache_data(ttl=30)
def fetch_ohlcv(symbol: str, lookback_h: int) -> pd.DataFrame:
    return get_conn().execute(
        f"""
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = ?
          AND ts >= (SELECT MAX(ts) FROM ohlcv WHERE symbol = ?) - INTERVAL {lookback_h} HOUR
        ORDER BY ts
        """,
        [symbol, symbol],
    ).fetchdf()


@st.cache_data(ttl=30)
def fetch_funding(symbols: tuple[str, ...], lookback_h: int) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    placeholders = ",".join(["?"] * len(symbols))
    return get_conn().execute(
        f"""
        SELECT ts, symbol, exchange, funding
        FROM funding_rate
        WHERE symbol IN ({placeholders})
          AND ts >= (SELECT MAX(ts) FROM funding_rate) - INTERVAL {lookback_h} HOUR
        ORDER BY ts
        """,
        list(symbols),
    ).fetchdf()


@st.cache_data(ttl=30)
def fetch_signal_events(limit: int = 200) -> pd.DataFrame:
    return get_conn().execute(
        f"""
        SELECT sig_num, ts, event_type, symbol, severity, payload
        FROM signal_events
        ORDER BY ts DESC
        LIMIT {limit}
        """
    ).fetchdf()


@st.cache_data(ttl=60)
def fetch_news(limit: int = 50) -> pd.DataFrame:
    return get_conn().execute(
        f"""
        SELECT published_at, source, title, url, symbols
        FROM news
        ORDER BY published_at DESC
        LIMIT {limit}
        """
    ).fetchdf()


@st.cache_data(ttl=60)
def fetch_trade_journal() -> pd.DataFrame:
    return get_conn().execute(
        """
        SELECT *
        FROM trade_journal
        ORDER BY ts DESC
        LIMIT 200
        """
    ).fetchdf()


@st.cache_data(ttl=60)
def compute_cointegration(pair: tuple[str, str], lookback_h: int) -> dict | None:
    from dashboard.cointegration import engle_granger

    a, b = pair
    df_a = fetch_ohlcv(a, lookback_h)
    df_b = fetch_ohlcv(b, lookback_h)
    if df_a.empty or df_b.empty:
        return None
    merged = pd.merge(
        df_a[["ts", "close"]].rename(columns={"close": "a"}),
        df_b[["ts", "close"]].rename(columns={"close": "b"}),
        on="ts",
        how="inner",
    )
    if len(merged) < 50:
        return None
    try:
        res = engle_granger(merged["a"], merged["b"], pair=(a, b))
        return {
            "pair": f"{a} / {b}",
            "hedge_ratio": round(res.hedge_ratio, 4),
            "adf_pvalue": round(res.adf_pvalue, 4),
            "half_life_bars": round(res.half_life_bars, 1) if res.half_life_bars != float("inf") else None,
            "zscore": round(res.current_zscore, 2),
            "cointegrated": res.is_cointegrated,
            "n_bars": res.n_bars,
        }
    except Exception as e:
        return {"pair": f"{a} / {b}", "error": str(e)}


# ---------- Daemon status ----------

def daemon_status() -> tuple[str, str]:
    if not HEARTBEAT_PATH.exists():
        return ("unknown", "heartbeat file not found")
    try:
        hb = yaml.safe_load(HEARTBEAT_PATH.read_text())
        last_str = hb.get("last_cycle_ts")
        if not last_str:
            return ("unknown", "no last_cycle_ts")
        last = datetime.fromisoformat(last_str)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - last).total_seconds()
        if age_sec < 600:
            return ("alive", f"last cycle {age_sec/60:.1f}m ago, cycle #{hb.get('cycle_number')}")
        if age_sec < 3600:
            return ("stale", f"last cycle {age_sec/60:.1f}m ago")
        return ("dead", f"last cycle {age_sec/3600:.1f}h ago, cycle #{hb.get('cycle_number')}")
    except Exception as e:
        return ("error", f"heartbeat unreadable: {e}")


def load_pairs_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:
        return {}


# ---------- Binance live ----------

def load_binance_env() -> dict[str, str] | None:
    if not SECRETS_PATH.exists():
        return None
    env: dict[str, str] = {}
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


@st.cache_data(ttl=60)
def fetch_binance_live() -> dict:
    import ccxt

    env = load_binance_env()
    if env is None:
        return {"error": f"secrets file not found: {SECRETS_PATH}"}
    api_key = env.get("BINANCE_API_KEY", "")
    secret = env.get("BINANCE_API_SECRET", "")
    if not api_key or not secret:
        return {"error": "BINANCE_API_KEY or BINANCE_API_SECRET empty"}

    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {"defaultType": "spot"},
    })

    out: dict = {"key_prefix": api_key[:4], "live": True}

    try:
        ex.load_markets()
        out["n_markets"] = len(ex.markets or {})
    except Exception as e:
        out["markets_error"] = f"{type(e).__name__}: {e}"
        return out

    # Spot balance + USD valuation via tickers
    try:
        bal_spot = ex.fetch_balance({"type": "spot"})
        totals = bal_spot.get("total") or {}
        nonzero = {k: float(v) for k, v in totals.items() if v and float(v) > 0}
        tickers = ex.fetch_tickers() if nonzero else {}
        rows = []
        total_usdt = 0.0
        for asset, amount in nonzero.items():
            usd = 0.0
            if asset == "USDT":
                usd = amount
            else:
                sym = f"{asset}/USDT"
                tk = tickers.get(sym)
                if tk and tk.get("last"):
                    usd = amount * float(tk["last"])
            total_usdt += usd
            rows.append({
                "asset": asset,
                "amount": amount,
                "usd_value": round(usd, 4),
            })
        rows.sort(key=lambda r: -r["usd_value"])
        for r in rows:
            r["pct"] = round(r["usd_value"] / total_usdt * 100, 2) if total_usdt > 0 else 0
        out["spot_total_usd"] = round(total_usdt, 4)
        out["spot_assets"] = rows
    except Exception as e:
        out["spot_error"] = f"{type(e).__name__}: {e}"

    # Futures balance (USDT-M)
    try:
        bal_fut = ex.fetch_balance({"type": "future"})
        usdt = bal_fut.get("USDT", {}) or {}
        out["futures_total"] = float(usdt.get("total") or 0)
        out["futures_free"] = float(usdt.get("free") or 0)
        out["futures_used"] = float(usdt.get("used") or 0)
    except Exception as e:
        out["futures_error"] = f"{type(e).__name__}: {e}"

    # Open futures positions
    try:
        positions = ex.fetch_positions()
        open_pos = [p for p in positions if float(p.get("contracts") or 0) != 0]
        out["positions_open"] = len(open_pos)
        out["positions_tracked"] = len(positions)
        out["open_positions_df"] = pd.DataFrame(
            [
                {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "contracts": p.get("contracts"),
                    "entry": p.get("entryPrice"),
                    "mark": p.get("markPrice"),
                    "unrealizedPnl": p.get("unrealizedPnl"),
                }
                for p in open_pos
            ]
        )
    except Exception as e:
        out["positions_error"] = f"{type(e).__name__}: {e}"

    # API key restrictions (Binance-specific)
    try:
        info = ex.sapi_get_account_apirestrictions()
        keep = {
            "ipRestrict": info.get("ipRestrict"),
            "enableReading": info.get("enableReading"),
            "enableSpotAndMarginTrading": info.get("enableSpotAndMarginTrading"),
            "enableFutures": info.get("enableFutures"),
            "enableMargin": info.get("enableMargin"),
            "enableWithdrawals": info.get("enableWithdrawals"),
            "enableInternalTransfer": info.get("enableInternalTransfer"),
        }
        out["api_restrictions"] = keep
    except Exception as e:
        out["api_restrictions_error"] = f"{type(e).__name__}: {e}"

    return out


# ---------- UI ----------

st.set_page_config(
    page_title="Trading Agent Monitor",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Auto-refresh hook
from streamlit_autorefresh import st_autorefresh

st.sidebar.header("Settings")
refresh_sec = st.sidebar.slider("Auto-refresh (detik, 0 = off)", 0, 120, 30, 10)
if refresh_sec > 0:
    st_autorefresh(interval=refresh_sec * 1000, key="datarefresh")

symbols_all = fetch_symbols()
sel_symbols = st.sidebar.multiselect(
    "Simbol", symbols_all, default=symbols_all[:3] if len(symbols_all) >= 3 else symbols_all
)
window_label = st.sidebar.selectbox("Window", list(WINDOW_HOURS.keys()), index=1)
window_h = WINDOW_HOURS[window_label]

state, state_msg = daemon_status()
state_color = {"alive": "success", "stale": "warning", "dead": "error", "unknown": "info", "error": "error"}[state]
getattr(st.sidebar, state_color)(f"Daemon **{state.upper()}** — {state_msg}")

if st.sidebar.button("Clear cache + reload"):
    st.cache_data.clear()
    st.rerun()

st.title("Trading Agent Monitor")
st.caption(f"DB: `{DB_PATH}` (read-only) — Streamlit web dashboard")

# Top stats row
stats = fetch_db_stats()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("OHLCV rows", f"{stats['ohlcv']:,}")
c2.metric("Funding rows", f"{stats['funding_rate']:,}")
c3.metric("Signal events", f"{stats['signal_events']:,}")
c4.metric("News rows", f"{stats['news']:,}")
c5.metric("DB size", f"{stats['db_size_mb']:.1f} MB")

last_ohlcv = stats["last_ohlcv_ts"]
last_funding = stats["last_funding_ts"]
now_utc = datetime.now(timezone.utc)
if last_ohlcv:
    last_ohlcv = last_ohlcv if last_ohlcv.tzinfo else last_ohlcv.replace(tzinfo=timezone.utc)
    age_h = (now_utc - last_ohlcv).total_seconds() / 3600
    st.caption(f"OHLCV freshest: **{last_ohlcv.strftime('%Y-%m-%d %H:%M UTC')}** ({age_h:.1f} jam lalu) · Funding: **{last_funding.strftime('%Y-%m-%d %H:%M UTC') if last_funding else '-'}** · Last signal: **{stats['last_signal_ts'].strftime('%Y-%m-%d %H:%M UTC') if stats['last_signal_ts'] else '(none)'}**")

tab_overview, tab_funding, tab_price, tab_coint, tab_signals, tab_news, tab_binance = st.tabs(
    [
        "Overview",
        "Funding Rates",
        "Price + Regime",
        "Cointegration",
        "Signal Events",
        "News",
        "Binance LIVE",
    ]
)

# ----- Overview -----
with tab_overview:
    st.subheader("Pipeline snapshot")
    st.markdown(
        f"""
- **Daemon:** `{state.upper()}` — {state_msg}
- **OHLCV:** {stats['ohlcv']:,} rows × {len(symbols_all)} simbol, freshest **{last_ohlcv.strftime('%Y-%m-%d %H:%M UTC') if last_ohlcv else '-'}**
- **Funding:** {stats['funding_rate']:,} rows (Bybit + Binance gabungan)
- **Signal events:** {stats['signal_events']:,} record
- **Trade journal:** {stats['trade_journal']:,} record
"""
    )

    st.subheader("Quick price (close) — multi-symbol")
    if sel_symbols:
        frames = []
        for sym in sel_symbols:
            df = fetch_ohlcv(sym, window_h)
            if not df.empty:
                df = df[["ts", "close"]].copy()
                df["symbol"] = sym
                # Normalize to first close = 100 for cross-symbol comparison
                first = df["close"].iloc[0]
                df["normalized"] = df["close"] / first * 100
                frames.append(df)
        if frames:
            allp = pd.concat(frames, ignore_index=True)
            fig = px.line(
                allp, x="ts", y="normalized", color="symbol",
                title=f"Normalized close (base=100) — window {window_label}",
            )
            fig.update_layout(height=400, hovermode="x unified", legend_title_text="")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Tidak ada data OHLCV untuk simbol yang dipilih.")
    else:
        st.info("Pilih simbol di sidebar.")

# ----- Funding Rates -----
with tab_funding:
    st.subheader(f"Funding rates — window {window_label}")
    fdf = fetch_funding(tuple(sel_symbols), window_h)
    if fdf.empty:
        st.warning("Tidak ada data funding untuk simbol yang dipilih.")
    else:
        fdf["funding_pct"] = fdf["funding"] * 100
        fig = px.line(
            fdf, x="ts", y="funding_pct", color="symbol", line_dash="exchange",
            title="Funding 8h rate (%)",
        )
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=480, hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Latest funding per symbol/exchange")
        latest = (
            fdf.sort_values("ts").groupby(["symbol", "exchange"]).tail(1)
            .sort_values(["symbol", "exchange"])
            .reset_index(drop=True)
        )
        latest["funding_pct_8h"] = (latest["funding"] * 100).round(4)
        latest["apr_pct"] = (latest["funding"] * 3 * 365 * 100).round(2)
        st.dataframe(
            latest[["symbol", "exchange", "ts", "funding_pct_8h", "apr_pct"]],
            use_container_width=True, hide_index=True,
        )

# ----- Price + Regime -----
with tab_price:
    st.subheader(f"Price candle + regime — window {window_label}")
    if not sel_symbols:
        st.info("Pilih simbol di sidebar.")
    else:
        from dashboard.regime import classify

        for sym in sel_symbols:
            df = fetch_ohlcv(sym, window_h)
            if df.empty:
                st.warning(f"{sym}: no data")
                continue
            try:
                snap = classify(df)
                regime_label = f"**{snap.regime}** · ATR {snap.atr_pct:.2%} · t={snap.trend_slope_t:.2f} · BB-width {snap.bb_width:.3f} · conf {snap.confidence:.2f}"
            except Exception as e:
                regime_label = f"regime error: {e}"

            fig = go.Figure(
                data=[
                    go.Candlestick(
                        x=df["ts"],
                        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                        name=sym,
                    )
                ]
            )
            fig.update_layout(
                title=f"{sym} — {regime_label}",
                height=350, xaxis_rangeslider_visible=False,
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

# ----- Cointegration -----
with tab_coint:
    st.subheader(f"Pair cointegration — window {window_label}")
    cfg = load_pairs_config()
    pairs = cfg.get("cointegration_candidates", [])
    if not pairs:
        st.info("Tidak ada `cointegration_candidates` di config/pairs.yaml")
    else:
        rows = []
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            res = compute_cointegration(tuple(pair), window_h)
            if res is not None:
                rows.append(res)
        if rows:
            cdf = pd.DataFrame(rows)
            st.dataframe(cdf, use_container_width=True, hide_index=True)
            st.caption("`cointegrated=True` jika ADF p-value < 0.05 (95% confidence)")
        else:
            st.warning("Tidak ada hasil cointegration (mungkin window terlalu kecil)")

# ----- Signal Events -----
with tab_signals:
    st.subheader("Recent signal events")
    sdf = fetch_signal_events(500)
    if sdf.empty:
        st.warning("Belum ada signal events. Daemon perlu jalan + detector replay untuk populate.")
    else:
        unique_types = sdf["event_type"].dropna().unique().tolist()
        type_filter = st.multiselect("Filter event type", unique_types, default=unique_types)
        view = sdf[sdf["event_type"].isin(type_filter)] if type_filter else sdf
        st.caption(f"{len(view)} / {len(sdf)} events shown")
        st.dataframe(view, use_container_width=True, hide_index=True, height=600)

        # Histogram per type
        st.subheader("Event type distribution")
        counts = sdf["event_type"].value_counts().reset_index()
        counts.columns = ["event_type", "count"]
        fig = px.bar(counts, x="event_type", y="count")
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig, use_container_width=True)

# ----- News -----
with tab_news:
    st.subheader("Latest news (RSS)")
    ndf = fetch_news(100)
    if ndf.empty:
        st.warning("Belum ada news. Daemon harus jalan untuk fetch RSS feeds.")
    else:
        for _, row in ndf.iterrows():
            published = row["published_at"]
            ts_str = published.strftime("%Y-%m-%d %H:%M") if hasattr(published, "strftime") else str(published)
            sym = f" · `{row['symbols']}`" if row.get("symbols") else ""
            st.markdown(f"- [{row['title']}]({row['url']}) — *{row['source']}* · {ts_str}{sym}")

# ----- Binance LIVE -----
with tab_binance:
    st.subheader("Binance LIVE — Path α (real account)")
    st.error(
        "**REAL MONEY** — semua action di sini ke akun produksi. Dashboard ini read-only, "
        "tapi credentials dengan trade scope bisa dipakai modul lain. Verifikasi scope sebelum lanjut."
    )
    if not SECRETS_PATH.exists():
        st.warning(f"Secrets file belum ada: `{SECRETS_PATH}`")
        st.markdown(
            """
**Cara setup:**

1. Login [binance.com](https://www.binance.com/) → User profile → API Management → Create API
2. Centang permission yang dibutuhkan (saran: minimal scope — uncheck Withdrawals)
3. Optional: IP whitelist (`curl ifconfig.me`)
4. Save key + secret ke `~/.secrets/binance.env`:

```
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
```

5. `chmod 600 ~/.secrets/binance.env`
6. Refresh halaman ini
"""
        )
    else:
        cols = st.columns([1, 1, 4])
        if cols[0].button("Probe Binance LIVE"):
            st.cache_data.clear()
            st.rerun()
        cols[1].caption(f"`{SECRETS_PATH}`")

        with st.spinner("Probing Binance live..."):
            out = fetch_binance_live()
        if "error" in out:
            st.error(out["error"])
        else:
            # API restrictions banner (most important info)
            restr = out.get("api_restrictions") or {}
            if restr:
                st.subheader("API key scope")
                cscope1, cscope2 = st.columns(2)
                with cscope1:
                    for k in ("ipRestrict", "enableReading", "enableSpotAndMarginTrading", "enableFutures"):
                        v = restr.get(k)
                        icon = "OK" if v else "OFF"
                        st.markdown(f"- **{k}**: `{v}` ({icon})")
                with cscope2:
                    for k in ("enableMargin", "enableInternalTransfer", "enableWithdrawals"):
                        v = restr.get(k)
                        icon = "DANGER" if (k == "enableWithdrawals" and v) else ("OK" if v else "OFF")
                        st.markdown(f"- **{k}**: `{v}` ({icon})")
                if restr.get("enableWithdrawals"):
                    st.error("**enableWithdrawals = TRUE** — key punya akses withdraw. Sangat berisiko. Pertimbangkan revoke + rebuat tanpa permission ini.")
                if not restr.get("ipRestrict"):
                    st.warning("ipRestrict = FALSE — key bisa dipakai dari IP manapun. Pertimbangkan whitelist IP.")
            elif "api_restrictions_error" in out:
                st.warning(f"API restriction probe gagal: {out['api_restrictions_error']}")

            # Top metrics
            st.subheader("Wallet snapshot")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Spot total USD", f"${out.get('spot_total_usd', 0):,.2f}")
            m2.metric("Futures USDT", f"{out.get('futures_total', 0):,.2f}")
            m3.metric("Open futures pos", out.get("positions_open", 0))
            m4.metric("Markets loaded", out.get("n_markets", "?"))
            st.caption(f"key_prefix=`{out.get('key_prefix')}***` · positions tracked {out.get('positions_tracked', 0)}")

            for err_key in ("markets_error", "spot_error", "futures_error", "positions_error"):
                if err_key in out:
                    st.warning(f"{err_key}: {out[err_key]}")

            # Spot wallet table
            spot_rows = out.get("spot_assets") or []
            if spot_rows:
                st.subheader("Spot wallet (per asset)")
                sdf = pd.DataFrame(spot_rows)
                st.dataframe(sdf, use_container_width=True, hide_index=True)
            elif "spot_error" not in out:
                st.info("Spot wallet kosong.")

            # Futures positions
            opf = out.get("open_positions_df")
            if isinstance(opf, pd.DataFrame) and not opf.empty:
                st.subheader("Open futures positions")
                st.dataframe(opf, use_container_width=True, hide_index=True)

st.caption(f"Last render: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
