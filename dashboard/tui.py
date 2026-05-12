"""Bloomberg-style terminal dashboard — amber on black, dense, numbered panels.

Panels:
  [1] FUNDING RATES — 8h perp funding + APR + signal
  [2] REGIME CLASSIFIER — ATR + trend t-stat + BB width
  [3] PAIRS COINTEGRATION — ADF p-value + hedge + z-score
  [4] NEWS FEED — RSS headlines dengan symbol tag
  STATUS BAR — DB stats + daemon heartbeat + live UTC clock
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import yaml
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from data_pipe.advisor import read_latest_verdict
from data_pipe.executor import KILL_SWITCH, read_latest_execution
from data_pipe.health import run_health_check
from data_pipe.storage import Storage
from dashboard.regime import classify
from dashboard.cointegration import engle_granger
from dashboard.sparkline import sparkline


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pairs.yaml"

# --- Bloomberg palette ---
# Amber accent untuk brand/header, putih untuk data, hijau/merah untuk tanda.
AMBER = "orange1"             # #ff8700 ~ Bloomberg classic amber
AMBER_BG = "black on orange1" # reverse video untuk header bar
DIM_AMBER = "dark_orange3"
GREEN = "green1"
RED = "bright_red"
CYAN_D = "dark_cyan"
WHITE = "grey93"
MUTED = "grey50"
BG_PANEL = "on grey3"         # sangat gelap, hampir hitam

# Box: Bloomberg pakai garis berat tebal. HEAVY cocok untuk outer.
PANEL_BOX = box.HEAVY
TABLE_BOX = box.SIMPLE_HEAVY  # header bar only


def _fmt_pct(x: float, decimals: int = 4) -> str:
    return f"{x * 100:.{decimals}f}%"


def _panel_title(num: int, label: str) -> str:
    """Format: '[N] LABEL' — numbered window convention a la Bloomberg."""
    return f"[{AMBER}][{num}][/{AMBER}] [bold {AMBER}]{label.upper()}[/bold {AMBER}]"


def _mkpanel(content, num: int, label: str) -> Panel:
    return Panel(
        content,
        title=_panel_title(num, label),
        title_align="left",
        border_style=AMBER,
        box=PANEL_BOX,
        padding=(0, 1),
    )


def _mktable() -> Table:
    """Bloomberg-style table: amber header bar, minimal borders, dense rows."""
    return Table(
        expand=True,
        box=TABLE_BOX,
        show_edge=False,
        header_style=AMBER_BG,
        border_style=DIM_AMBER,
        pad_edge=False,
        padding=(0, 1),
    )


def _color_funding(rate: float, cfg: dict) -> str:
    extreme = cfg["funding"]["extreme"]
    elevated = cfg["funding"]["elevated"]
    if abs(rate) >= extreme:
        return f"bold {RED}" if rate > 0 else f"bold magenta"
    if abs(rate) >= elevated:
        return "yellow"
    if cfg["funding"]["neutral_band"][0] <= rate <= cfg["funding"]["neutral_band"][1]:
        return MUTED
    return CYAN_D


def _regime_color(regime: str) -> str:
    return {
        "trending_up": GREEN,
        "trending_down": RED,
        "ranging": CYAN_D,
        "chaos": "yellow",
        "insufficient_data": MUTED,
    }.get(regime, WHITE)


# ─── HEADER BAR (top ribbon) ────────────────────────────────────────────────

def build_header_bar(cfg: dict) -> Panel:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    exch = cfg.get("exchange", "?").upper()
    tf = cfg.get("timeframe", "?")
    txt = Text.from_markup(
        f" [bold {AMBER}]TRADING TERMINAL[/bold {AMBER}]  "
        f"[{MUTED}]│[/{MUTED}]  "
        f"[bold {WHITE}]{now} UTC[/bold {WHITE}]  "
        f"[{MUTED}]│[/{MUTED}]  "
        f"[{AMBER}]MKT[/{AMBER}] [bold]{exch}[/bold]  "
        f"[{AMBER}]TF[/{AMBER}] [bold]{tf}[/bold]  "
        f"[{MUTED}]│[/{MUTED}]  "
        f"[{DIM_AMBER}]<HELP> <F1-F4:PANELS> <Q:QUIT>[/{DIM_AMBER}]  "
        f"[{MUTED}]│[/{MUTED}]  "
        f"[{DIM_AMBER}]READ-ONLY · NO ORDERS[/{DIM_AMBER}] "
    )
    return Panel(txt, box=box.HEAVY, border_style=AMBER, padding=(0, 0))


# ─── [1] FUNDING ────────────────────────────────────────────────────────────

def build_funding_panel(storage: Storage, cfg: dict) -> Panel:
    table = _mktable()
    table.add_column("SYMBOL", width=10, style=WHITE)
    table.add_column("FUND 8H", width=9, justify="right")
    table.add_column("APR", width=7, justify="right")
    table.add_column("AGE", width=4, justify="right", style=MUTED)
    table.add_column("7D TREND", width=14, justify="left", style=DIM_AMBER)
    table.add_column("SIGNAL", justify="left")

    symbols = cfg["tier1"] + cfg["tier2"]
    exchange = cfg["exchange"]

    for sym in symbols:
        df = storage.get_funding(exchange, sym, limit=21)
        if df.empty:
            table.add_row(sym, "—", "—", "—", "—", f"[{MUTED}]no data[/{MUTED}]")
            continue
        last = df.iloc[-1]
        rate = float(last["funding"])
        ts = pd.Timestamp(last["ts"])
        age = datetime.utcnow() - ts.to_pydatetime()
        age_str = f"{int(age.total_seconds() / 3600)}H"
        apr = rate * 3 * 365
        # sparkline 7d funding history (21 bars)
        spark = sparkline(df["funding"].tolist(), width=14)
        color = _color_funding(rate, cfg)
        signal = ""
        if abs(rate) >= cfg["funding"]["extreme"]:
            signal = f"[bold {RED} on grey11] ARB WINDOW [/bold {RED} on grey11]"
        elif abs(rate) >= cfg["funding"]["elevated"]:
            signal = "[yellow]▲ ELEVATED[/yellow]"

        table.add_row(
            sym,
            f"[{color}]{_fmt_pct(rate, 4)}[/{color}]",
            f"[{color}]{apr*100:+.1f}%[/{color}]",
            age_str,
            spark,
            signal,
        )

    return _mkpanel(table, 1, "FUNDING RATES (8H PERP)")


# ─── [2] REGIME ─────────────────────────────────────────────────────────────

def build_regime_panel(storage: Storage, cfg: dict) -> Panel:
    # Singkat label regime → hemat kolom. TRENDING_UP→UP, TRENDING_DOWN→DOWN, dll.
    regime_short = {
        "trending_up": "UPTREND",
        "trending_down": "DOWNTRND",
        "ranging": "RANGING",
        "chaos": "CHAOS",
        "insufficient_data": "NO DATA",
    }
    table = _mktable()
    table.add_column("SYMBOL", width=10, style=WHITE)
    table.add_column("REGIME", width=9)
    table.add_column("ATR%", width=5, justify="right")
    table.add_column("T-STAT", width=9, justify="right")
    table.add_column("7D PX", width=14, justify="left", style=DIM_AMBER)
    table.add_column("CONF", width=4, justify="right", style=MUTED)

    symbols = cfg["tier1"] + cfg["tier2"]
    exchange = cfg["exchange"]
    tf = cfg["timeframe"]

    for sym in symbols:
        df = storage.get_ohlcv(exchange, sym, tf, limit=500)
        snap = classify(df)
        color = _regime_color(snap.regime)
        t = snap.trend_slope_t
        t_color = GREEN if t > 2 else RED if t < -2 else MUTED
        arrow = "▲" if t > 2 else "▼" if t < -2 else "·"
        # 7d price sparkline — ambil close last 7*24=168 bars, downsample ke 14
        tail = df["close"].tail(168).tolist() if not df.empty else []
        spark = sparkline(tail, width=14)
        table.add_row(
            sym,
            f"[{color}]{regime_short.get(snap.regime, snap.regime[:8].upper())}[/{color}]",
            f"{snap.atr_pct*100:.2f}",
            f"[{t_color}]{arrow}{t:+6.2f}[/{t_color}]",
            spark,
            f"{snap.confidence:.2f}",
        )

    return _mkpanel(table, 2, "REGIME CLASSIFIER")


# ─── [3] COINTEGRATION ──────────────────────────────────────────────────────

def build_cointegration_panel(storage: Storage, cfg: dict) -> Panel:
    table = _mktable()
    table.add_column("PAIR", width=10, style=WHITE)
    table.add_column("β", width=6, justify="right")
    table.add_column("ADF p", width=6, justify="right")
    table.add_column("HL", width=5, justify="right", style=MUTED)
    table.add_column("Z", width=6, justify="right")
    table.add_column("STATUS", justify="left")

    exchange = cfg["exchange"]
    tf = cfg["timeframe"]

    for pair_cfg in cfg.get("cointegration_candidates", []):
        a, b = pair_cfg
        df_a = storage.get_ohlcv(exchange, a, tf, limit=500)
        df_b = storage.get_ohlcv(exchange, b, tf, limit=500)
        if df_a.empty or df_b.empty:
            table.add_row(f"{a} / {b}", "—", "—", "—", "—", f"[{MUTED}]no data[/{MUTED}]")
            continue
        df_a = df_a.set_index("ts")["close"]
        df_b = df_b.set_index("ts")["close"]
        y = np.log(df_a)
        x = np.log(df_b)
        try:
            res = engle_granger(y, x, pair_names=(a, b))
        except Exception as e:
            table.add_row(f"{a} / {b}", "err", str(e)[:10], "—", "—", f"[{RED}]CALC FAIL[/{RED}]")
            continue

        p_color = GREEN if res.is_cointegrated else MUTED
        z = res.current_zscore
        z_color = "yellow" if abs(z) > 2 else WHITE
        hl_str = f"{res.half_life_bars:.1f}" if res.half_life_bars < 1e5 else "∞"
        if res.is_cointegrated:
            if z > 2:
                status = f"[bold {CYAN_D}]▼ SHORT SPR[/bold {CYAN_D}]"
            elif z < -2:
                status = f"[bold {CYAN_D}]▲ LONG SPR[/bold {CYAN_D}]"
            else:
                status = f"[{GREEN}]COINT NEUT[/{GREEN}]"
        else:
            status = f"[{MUTED}]NOT COINT[/{MUTED}]"

        table.add_row(
            f"{a.split('/')[0]}/{b.split('/')[0]}",
            f"{res.hedge_ratio:.3f}",
            f"[{p_color}]{res.adf_pvalue:.3f}[/{p_color}]",
            hl_str,
            f"[{z_color}]{z:+.2f}[/{z_color}]",
            status,
        )

    return _mkpanel(table, 3, "PAIRS COINTEGRATION")


# ─── [4] NEWS ───────────────────────────────────────────────────────────────

def build_signal_panel(storage: Storage, limit: int = 10) -> Panel:
    """[5] SIGNAL EVENTS — log dari detector."""
    table = _mktable()
    table.add_column("SIG#", width=5, justify="right", style=AMBER)
    table.add_column("TIME", width=8, style=MUTED)
    table.add_column("TYPE", width=14)
    table.add_column("SYM", width=12)
    table.add_column("SEV", width=5)
    table.add_column("DETAIL")

    df = storage.get_signals(limit=limit)
    if df.empty:
        return _mkpanel(
            Text.from_markup(f"[{MUTED}]no signals yet — menunggu detector cycle[/{MUTED}]"),
            5, "SIGNAL EVENTS",
        )

    sev_color = {"critical": RED, "warn": "yellow", "info": CYAN_D}
    type_color = {
        "REGIME_CHANGE": CYAN_D,
        "FUNDING_EXTREME": f"bold {RED}",
        "FUNDING_FLIP": "yellow",
        "Z_SPIKE": f"bold magenta",
    }
    for _, row in df.iterrows():
        t = pd.Timestamp(row["ts"]).to_pydatetime().strftime("%H:%M:%S")
        et = str(row["event_type"])
        sym = str(row["symbol"] or "-")[:12]
        sev = str(row["severity"])[:5].upper()
        payload = str(row["payload"] or "")[:60]
        tc = type_color.get(et, WHITE)
        sc = sev_color.get(row["severity"], WHITE)
        sig_n = row["sig_num"] if pd.notna(row["sig_num"]) else "-"
        table.add_row(
            str(int(sig_n)) if sig_n != "-" else "-",
            t,
            f"[{tc}]{et}[/{tc}]",
            sym,
            f"[{sc}]{sev}[/{sc}]",
            f"[{WHITE}]{payload}[/{WHITE}]",
        )
    return _mkpanel(table, 5, "SIGNAL EVENTS")


# ─── [6] TRADE JOURNAL ──────────────────────────────────────────────────────

def build_journal_panel(storage: Storage, limit: int = 8) -> Panel:
    """[6] TRADE JOURNAL — OPEN trades dari manual journal."""
    df = storage.get_trades(status="OPEN", limit=limit)
    if df.empty:
        return _mkpanel(
            Text.from_markup(
                f"[{MUTED}]no open trades — `python scripts/journal.py add ...`[/{MUTED}]"
            ),
            6, "TRADE JOURNAL (OPEN)",
        )

    table = _mktable()
    table.add_column("ID", width=4, justify="right", style=WHITE)
    table.add_column("OPEN", width=11, style=MUTED)
    table.add_column("PAIR", width=34)
    table.add_column("DIR", width=5, justify="center")
    table.add_column("ENTRY", width=10, justify="right")
    table.add_column("SIZE", width=8, justify="right", style=MUTED)
    table.add_column("THESIS", justify="left")

    dir_color = {"LONG": GREEN, "SHORT": RED, "PAIR": CYAN_D}
    for _, row in df.iterrows():
        ts = pd.Timestamp(row["ts"]).strftime("%m-%d %H:%M")
        direction = str(row["direction"])
        dcol = dir_color.get(direction, WHITE)
        entry = f"{row['entry_price']:.4f}" if pd.notna(row["entry_price"]) else "—"
        size = f"{row['size']:.2f}" if pd.notna(row["size"]) else "—"
        thesis = str(row["thesis"] or "")
        if len(thesis) > 60:
            thesis = thesis[:59] + "…"
        # Pair cell: "SYM/USDT [SIG#12]" kalau signal_id terisi
        pair_cell = str(row["pair"])[:22]
        if pd.notna(row.get("signal_id")):
            pair_cell = f"{pair_cell} [{DIM_AMBER}][SIG#{int(row['signal_id'])}][/{DIM_AMBER}]"
        table.add_row(
            str(int(row["id"])),
            ts,
            pair_cell,
            f"[{dcol}]{direction}[/{dcol}]",
            entry,
            size,
            f"[{WHITE}]{thesis}[/{WHITE}]",
        )
    return _mkpanel(table, 6, "TRADE JOURNAL (OPEN)")


def build_ticker_bar(storage: Storage, offset: int = 0) -> Panel:
    """Scrolling news ticker — Bloomberg classic. Offset by cycle tick."""
    df = storage.get_news(limit=30)
    if df.empty:
        msg = f"[{MUTED}]· no news ·[/{MUTED}]"
    else:
        items = []
        for _, row in df.iterrows():
            sym = row["symbols"] or ""
            tag = f" [bold yellow]{sym}[/bold yellow] " if sym else " "
            title = str(row["title"])[:80]
            items.append(f"[{DIM_AMBER}]{row['source']}[/{DIM_AMBER}]{tag}[{WHITE}]{title}[/{WHITE}]")
        # rotate berdasarkan offset supaya terasa scrolling
        if offset and items:
            offset = offset % len(items)
            items = items[offset:] + items[:offset]
        sep = f"  [{AMBER}]▶[/{AMBER}]  "
        msg = sep.join(items)
    txt = Text.from_markup(msg)
    txt.no_wrap = True
    txt.overflow = "ellipsis"
    return Panel(
        txt,
        box=box.HEAVY,
        border_style=DIM_AMBER,
        padding=(0, 1),
    )


def build_news_panel(storage: Storage, limit: int = 14) -> Panel:
    table = _mktable()
    table.add_column("AGE", width=5, justify="right")
    table.add_column("SRC", width=9, style=MUTED)
    table.add_column("SYM", width=10)
    table.add_column("HEADLINE")

    df = storage.get_news(limit=limit)
    if df.empty:
        return _mkpanel(
            Text.from_markup(f"[{MUTED}]no news yet — daemon belum pull[/{MUTED}]"),
            4, "NEWS FEED",
        )

    now = datetime.utcnow()
    for _, row in df.iterrows():
        pub = pd.Timestamp(row["published_at"]).to_pydatetime()
        delta = now - pub
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            age = f"{mins}M"
            age_color = GREEN if mins < 15 else CYAN_D
        elif mins < 1440:
            age = f"{mins // 60}H"
            age_color = WHITE
        else:
            age = f"{mins // 1440}D"
            age_color = MUTED

        syms = row["symbols"] or ""
        sym_colored = f"[bold yellow]{syms}[/bold yellow]" if syms else f"[{MUTED}]—[/{MUTED}]"
        title_color = WHITE if syms else MUTED
        title = str(row["title"])[:85]
        src = str(row["source"])[:9]

        table.add_row(
            f"[{age_color}]{age}[/{age_color}]",
            src,
            sym_colored,
            f"[{title_color}]{title}[/{title_color}]",
        )

    return _mkpanel(table, 4, "NEWS FEED (RSS)")


# ─── STATUS BAR ─────────────────────────────────────────────────────────────

_HEALTH_COLOR = {"OK": GREEN, "GAP": "yellow", "STALE": RED}
_VERDICT_COLOR = {"WAIT": MUTED, "ENTRY": GREEN, "EXIT": "yellow"}
_EXEC_FRESH_MIN = 15  # di bawah threshold ini → ACTIVE; di atas → IDLE


def _executor_badge() -> str:
    """Badge EXEC · ACTIVE/IDLE/OFF dari data/executor.latest.json + kill switch."""
    data_dir = Path(__file__).resolve().parent.parent / "data"
    if (data_dir / KILL_SWITCH).exists():
        return f"[bold {RED}]EXEC · OFF[/bold {RED}]"
    result = read_latest_execution(data_dir)
    if result is None:
        return f"[{MUTED}]EXEC · IDLE[/{MUTED}]"
    try:
        ts = datetime.fromisoformat(result["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        action = result.get("action", "?")
        if age_min > _EXEC_FRESH_MIN:
            return f"[{MUTED}]EXEC · IDLE {int(age_min)}m[/{MUTED}]"
        color = GREEN if action == "OPEN" else "yellow"
        suffix = f" · {action}"
        if action == "OPEN" and result.get("trade_id") is not None:
            suffix += f" #{result['trade_id']}"
        return f"[bold {color}]EXEC · ACTIVE[/bold {color}][{MUTED}]{suffix}[/{MUTED}]"
    except Exception:
        return f"[{MUTED}]EXEC · ?[/{MUTED}]"


def _advisor_badge() -> str:
    """Badge 'ADV · WAIT/ENTRY/EXIT · Xm' dari data/advisor.latest.json."""
    data_dir = Path(__file__).resolve().parent.parent / "data"
    payload = read_latest_verdict(data_dir)
    if payload is None:
        return f"[{MUTED}]ADV · —[/{MUTED}]"
    try:
        v = payload["verdict"]["verdict"]
        gen = datetime.fromisoformat(payload["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        age_min = int((datetime.now(timezone.utc) - gen).total_seconds() / 60)
        # >30 menit: color fade ke MUTED (stale verdict) supaya user tahu basi
        stale = age_min > 30
        color = MUTED if stale else _VERDICT_COLOR.get(v, WHITE)
        return f"[bold {color}]ADV · {v}[/bold {color}] [{MUTED}]{age_min}m[/{MUTED}]"
    except Exception:
        return f"[{MUTED}]ADV · ?[/{MUTED}]"


_RISK_COLOR = {"OK": GREEN, "WARN": "yellow", "TRIGGER": RED}


def _risk_badge(storage: Storage, cfg: dict) -> str:
    """Badge 'RISK · OK / WARN -X.X% / KILL -X.X%' dari risk_guard.

    Show 'KILL' kalau status TRIGGER ATAU file killswitch ada (engaged & belum
    reset). File-existence dominan supaya operator yang baru reset tidak tertipu
    state cycle lama.
    """
    try:
        from data_pipe.risk_guard import (
            KILLSWITCH_FILE, get_daily_pnl_state, is_killswitch_active,
        )
        data_dir = Path(__file__).resolve().parent.parent / "data"
        st = get_daily_pnl_state(storage, cfg)
        engaged = is_killswitch_active(data_dir)
        status = "TRIGGER" if engaged else st["status"]
        label = "KILL" if status == "TRIGGER" else status
        color = _RISK_COLOR.get(status, MUTED)
        pct = st["daily_pnl_pct"]
        return (
            f"[{AMBER}]RISK[/{AMBER}] "
            f"[bold {color}]{label}[/bold {color}] "
            f"[{MUTED}]{pct:+.2f}%[/{MUTED}]"
        )
    except Exception:
        return f"[{MUTED}]RISK · ?[/{MUTED}]"


def _portfolio_badge(storage: Storage, cfg: dict) -> str:
    """Badge 'EQ · 10000 · OPEN 25%/100% · 1/3' dari account_state.

    Warna gross_pct: hijau <50, kuning 50-80, merah >80. Concurrent: hijau bila
    < max, merah bila == max (cap reached).
    """
    try:
        from data_pipe.portfolio import get_account_state
        st = get_account_state(storage, cfg)
        gp = float(st["gross_pct"])
        gross_color = GREEN if gp < 50 else "yellow" if gp < 80 else RED
        n_open = int(st["n_open"])
        max_c = int(st["max_concurrent"])
        conc_color = RED if n_open >= max_c else GREEN if n_open > 0 else MUTED
        return (
            f"[{AMBER}]EQ[/{AMBER}] [bold {WHITE}]{st['equity']:,.0f}[/bold {WHITE}] "
            f"[{MUTED}]·[/{MUTED}] "
            f"[{AMBER}]GROSS[/{AMBER}] [bold {gross_color}]{gp:.0f}%[/bold {gross_color}]"
            f"[{MUTED}]/{int(st['max_gross_pct'])}%[/{MUTED}] "
            f"[{MUTED}]·[/{MUTED}] "
            f"[{AMBER}]POS[/{AMBER}] [bold {conc_color}]{n_open}[/bold {conc_color}]"
            f"[{MUTED}]/{max_c}[/{MUTED}]"
        )
    except Exception:
        return f"[{MUTED}]EQ · ?[/{MUTED}]"


def _health_badge(storage: Storage, cfg: dict) -> str:
    """Rich markup 1-token status indikator: 'DATA: OK/GAP/STALE'."""
    try:
        rep = run_health_check(storage, cfg)
        color = _HEALTH_COLOR.get(rep["status"], MUTED)
        return f"[bold {color}]DATA · {rep['status']}[/bold {color}]"
    except Exception:
        return f"[{MUTED}]DATA · ?[/{MUTED}]"


def build_status_bar(
    storage: Storage,
    last_refresh: datetime,
    refresh_sec: int,
    cfg: dict | None = None,
) -> Panel:
    stats = storage.stats()
    now = datetime.now(timezone.utc)
    age = (now - last_refresh.replace(tzinfo=timezone.utc) if last_refresh.tzinfo is None else now - last_refresh).total_seconds()
    next_in = max(0, refresh_sec - int(age))

    hb_path = Path(__file__).resolve().parent.parent / "data" / "daemon.heartbeat"
    daemon_info = f"[{MUTED}]DAEMON · OFFLINE[/{MUTED}]"
    if hb_path.exists():
        try:
            hb = yaml.safe_load(hb_path.read_text())
            last_daemon = datetime.fromisoformat(hb["last_cycle_ts"])
            age_sec = (datetime.now(timezone.utc) - last_daemon).total_seconds()
            c = GREEN if age_sec < 900 else "yellow" if age_sec < 1800 else RED
            daemon_info = (
                f"[{c}]● DAEMON[/{c}] "
                f"[{WHITE}]C#{hb['cycle_number']} {hb['cycle_duration_sec']}s · "
                f"last {int(age_sec)}s ago[/{WHITE}]"
            )
        except Exception:
            daemon_info = f"[{RED}]● DAEMON · UNREADABLE[/{RED}]"

    health_badge = _health_badge(storage, cfg) if cfg else f"[{MUTED}]DATA · ?[/{MUTED}]"
    adv_badge = _advisor_badge()
    exec_badge = _executor_badge()
    port_badge = _portfolio_badge(storage, cfg) if cfg else f"[{MUTED}]EQ · ?[/{MUTED}]"
    risk_badge = _risk_badge(storage, cfg) if cfg else f"[{MUTED}]RISK · ?[/{MUTED}]"

    txt = Text.from_markup(
        f" [{AMBER}]DB[/{AMBER}] "
        f"[bold]{stats['ohlcv_rows']:,}[/bold][{MUTED}]ohlcv[/{MUTED}] · "
        f"[bold]{stats['funding_rows']:,}[/bold][{MUTED}]fnd[/{MUTED}] · "
        f"[bold]{stats.get('news_rows', 0):,}[/bold][{MUTED}]news[/{MUTED}] · "
        f"[bold]{stats.get('signal_rows', 0):,}[/bold][{MUTED}]sig[/{MUTED}] · "
        f"[bold]{stats.get('trades_open', 0)}[/bold][{MUTED}]trd[/{MUTED}] · "
        f"[bold]{len(stats['symbols'])}[/bold][{MUTED}]sym[/{MUTED}]  "
        f"[{MUTED}]│[/{MUTED}]  {daemon_info}  "
        f"[{MUTED}]│[/{MUTED}]  {health_badge}  "
        f"[{MUTED}]│[/{MUTED}]  {adv_badge}  "
        f"[{MUTED}]│[/{MUTED}]  {exec_badge}  "
        f"[{MUTED}]│[/{MUTED}]  {port_badge}  "
        f"[{MUTED}]│[/{MUTED}]  {risk_badge}  "
        f"[{MUTED}]│[/{MUTED}]  "
        f"[{AMBER}]RNDR[/{AMBER}] [bold]{last_refresh.strftime('%H:%M:%S')}[/bold]  "
        f"[{AMBER}]NEXT[/{AMBER}] [bold]{next_in}s[/bold] "
    )
    return Panel(txt, box=box.HEAVY, border_style=AMBER, padding=(0, 0))


# ─── LAYOUT ─────────────────────────────────────────────────────────────────

def build_layout_from_cfg(cfg: dict, last_refresh: datetime, refresh_sec: int, tick: int = 0) -> Layout:
    with Storage(read_only=True) as storage:
        return build_layout(storage, cfg, last_refresh, refresh_sec, tick)


def build_layout(
    storage: Storage,
    cfg: dict,
    last_refresh: datetime,
    refresh_sec: int,
    tick: int = 0,
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="signals", size=14),
        Layout(name="journal", size=10),
        Layout(name="ticker", size=3),
        Layout(name="status", size=3),
    )
    layout["header"].update(build_header_bar(cfg))

    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(build_funding_panel(storage, cfg), name="funding"),
        Layout(build_cointegration_panel(storage, cfg), name="coint"),
    )
    layout["right"].split_column(
        Layout(build_regime_panel(storage, cfg), name="regime"),
        Layout(build_news_panel(storage, limit=10), name="news"),
    )
    layout["signals"].update(build_signal_panel(storage))
    layout["journal"].update(build_journal_panel(storage))
    layout["ticker"].update(build_ticker_bar(storage, offset=tick))
    layout["status"].update(build_status_bar(storage, last_refresh, refresh_sec, cfg))
    return layout


def run_dashboard(refresh_sec: int = 60):
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    console = Console()
    last_refresh = datetime.now(timezone.utc)
    tick = 0  # scroll offset untuk ticker bar — advance tiap render

    try:
        with Live(
            build_layout_from_cfg(cfg, last_refresh, refresh_sec, tick),
            console=console,
            refresh_per_second=1,
            screen=False,
        ) as live:
            while True:
                time.sleep(1)
                tick += 1
                now = datetime.now(timezone.utc)
                if (now - last_refresh).total_seconds() >= refresh_sec:
                    last_refresh = now
                try:
                    layout = build_layout_from_cfg(cfg, last_refresh, refresh_sec, tick)
                    live.update(layout)
                except Exception as e:
                    console.log(f"[yellow]refresh skipped: {e}[/yellow]")
    except KeyboardInterrupt:
        console.print(f"\n[{MUTED}]terminal stopped[/{MUTED}]")


if __name__ == "__main__":
    run_dashboard()
