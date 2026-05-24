#!/usr/bin/env python3
"""Generate daily summary markdown report.

Writes to data/daily_reports/YYYY-MM-DD.md with:
- Header (date, wallet snapshot, equity, daily PnL)
- Open positions (entry, current, unrealized)
- Closed today (realized PnL)
- Top scan candidates (5)
- News headlines today (top 10)
- Risk state snapshot
- Recent log highlights (live_executor + alerts + risk)

Manual trigger:
    python scripts/daily_report.py                  # today UTC
    python scripts/daily_report.py --date 2026-05-23

Cron-able. Uses read_only DuckDB so safe alongside daemon writer.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import duckdb
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.brokers import BinanceSpotBroker  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "market.duckdb"
CFG_PATH = REPO_ROOT / "config" / "pairs.yaml"
REPORT_DIR = REPO_ROOT / "data" / "daily_reports"
LIVE_LOG = REPO_ROOT / "data" / "live_executor.log"
ALERT_LOG = REPO_ROOT / "data" / "alerts.log"


def _ro_conn():
    return duckdb.connect(str(DB_PATH), read_only=True)


def section(title: str) -> str:
    return f"\n## {title}\n"


def fetch_wallet(broker: BinanceSpotBroker) -> dict:
    try:
        bal = broker.fetch_spot_balance()
        total_usd = 0.0
        rows = []
        for asset, amount in sorted(bal.items(), key=lambda x: -x[1]):
            if asset == "USDT":
                usd = amount
            else:
                try:
                    tk = broker.get_ticker(f"{asset}/USDT")
                    usd = amount * float(tk.get("last") or 0)
                except Exception:
                    usd = 0
            total_usd += usd
            rows.append((asset, amount, usd))
        return {"rows": rows, "total_usd": total_usd}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "rows": [], "total_usd": 0.0}


def fetch_open_positions(conn, broker) -> list[dict]:
    rows = conn.execute(
        "SELECT id, ts, pair, direction, entry_price, size, thesis "
        "FROM trade_journal WHERE status='OPEN' ORDER BY ts"
    ).fetchall()
    out = []
    for r in rows:
        pair = str(r[2])
        entry = float(r[4]) if r[4] is not None else 0
        size = float(r[5]) if r[5] is not None else 0
        try:
            tk = broker.get_ticker(pair)
            current = float(tk.get("last") or 0)
        except Exception:
            current = 0
        unrealized = (current - entry) * size if (current and entry) else 0
        pct = ((current - entry) / entry * 100) if entry else 0
        out.append({
            "id": int(r[0]),
            "ts": r[1],
            "pair": pair,
            "direction": str(r[3]),
            "entry": entry,
            "size": size,
            "current": current,
            "unrealized": unrealized,
            "pct": pct,
            "thesis": (str(r[6]) or "")[:80],
        })
    return out


def fetch_closed_in_range(conn, day_start, day_end) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, ts, pair, direction, entry_price, size, exit_ts, exit_price, pnl, notes
        FROM trade_journal
        WHERE status='CLOSED' AND exit_ts >= ? AND exit_ts < ?
        ORDER BY exit_ts
        """,
        [day_start, day_end],
    ).fetchall()
    return [
        {
            "id": int(r[0]),
            "ts_open": r[1],
            "pair": str(r[2]),
            "direction": str(r[3]),
            "entry": float(r[4]) if r[4] is not None else None,
            "size": float(r[5]) if r[5] is not None else None,
            "ts_close": r[6],
            "exit": float(r[7]) if r[7] is not None else None,
            "pnl": float(r[8]) if r[8] is not None else None,
            "notes": (str(r[9]) or "")[:80],
        }
        for r in rows
    ]


def fetch_news(conn, day_start, day_end, limit=10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT published_at, source, title, url, symbols
        FROM news
        WHERE published_at >= ? AND published_at < ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [day_start, day_end, limit],
    ).fetchall()
    return [
        {
            "published_at": r[0],
            "source": str(r[1]),
            "title": str(r[2]),
            "url": str(r[3]),
            "symbols": str(r[4] or ""),
        }
        for r in rows
    ]


def fetch_risk_events(conn, day_start, day_end) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ts, event_type, symbol, severity, payload
        FROM signal_events
        WHERE ts >= ? AND ts < ? AND event_type LIKE 'RISK_%'
        ORDER BY ts DESC
        """,
        [day_start, day_end],
    ).fetchall()
    return [
        {
            "ts": r[0],
            "event_type": str(r[1]),
            "symbol": r[2],
            "severity": str(r[3]),
            "payload": str(r[4] or "")[:120],
        }
        for r in rows
    ]


def tail_log(path: Path, n: int = 10) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open() as f:
            return f.readlines()[-n:]
    except Exception:
        return []


def fetch_candidates(storage, broker):
    """Run scan_all_candidates with read-only storage wrapper."""
    from data_pipe.event_scanner import scan_all_candidates

    class ROWrapper:
        def __init__(self, conn):
            self._conn = conn

    try:
        return scan_all_candidates(ROWrapper(storage), broker)
    except Exception as e:
        return [f"scan err: {e}"]


def build_report(target_date: date) -> str:
    cfg = yaml.safe_load(CFG_PATH.read_text()) or {}
    pcfg = cfg.get("portfolio") or {}
    starting_capital = float(pcfg.get("starting_capital", 50.0))

    day_start = datetime.combine(target_date, dtime.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    now_utc = datetime.now(timezone.utc)

    broker = BinanceSpotBroker()
    conn = _ro_conn()
    try:
        wallet = fetch_wallet(broker)
        open_pos = fetch_open_positions(conn, broker)
        closed = fetch_closed_in_range(conn, day_start, day_end)
        news = fetch_news(conn, day_start, day_end, limit=15)
        risk_events = fetch_risk_events(conn, day_start, day_end)
        candidates = fetch_candidates(conn, broker)
    finally:
        conn.close()

    realized_today = sum(c["pnl"] for c in closed if c["pnl"] is not None)
    unrealized_total = sum(p["unrealized"] for p in open_pos)
    daily_pnl_pct = (realized_today / starting_capital * 100) if starting_capital > 0 else 0

    md: list[str] = []
    md.append(f"# Daily Report — {target_date.isoformat()}")
    md.append(f"\n_Generated: {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}_\n")

    # Wallet snapshot
    md.append(section("Wallet Snapshot (Binance Spot LIVE)"))
    if wallet.get("error"):
        md.append(f"`{wallet['error']}`\n")
    else:
        md.append("| Asset | Amount | USD value |")
        md.append("|---|---:|---:|")
        for asset, amt, usd in wallet["rows"]:
            md.append(f"| {asset} | {amt:.8g} | ${usd:.4f} |")
        md.append(f"| **TOTAL** | — | **${wallet['total_usd']:.4f}** |\n")

    # Header equity
    md.append(section("Equity"))
    md.append(f"- Starting capital (config): **${starting_capital:.2f}**")
    md.append(f"- Realized PnL today: **${realized_today:+.4f}** ({daily_pnl_pct:+.2f}%)")
    md.append(f"- Unrealized PnL (open): **${unrealized_total:+.4f}**")
    md.append(f"- Wallet total (mtm): **${wallet.get('total_usd', 0):.4f}**\n")

    # Open positions
    md.append(section(f"Open Positions ({len(open_pos)})"))
    if not open_pos:
        md.append("_No open positions._\n")
    else:
        md.append("| # | Pair | Dir | Entry | Size | Current | Unrealized | % | Thesis |")
        md.append("|---:|---|---|---:|---:|---:|---:|---:|---|")
        for p in open_pos:
            md.append(
                f"| {p['id']} | {p['pair']} | {p['direction']} | {p['entry']:.6g} | "
                f"{p['size']:.6g} | {p['current']:.6g} | {p['unrealized']:+.4f} | "
                f"{p['pct']:+.2f}% | {p['thesis']} |"
            )
        md.append("")

    # Closed today
    md.append(section(f"Closed Today ({len(closed)})"))
    if not closed:
        md.append("_No closed trades today._\n")
    else:
        md.append("| # | Pair | Entry | Exit | Size | PnL | Notes |")
        md.append("|---:|---|---:|---:|---:|---:|---|")
        for c in closed:
            pnl_s = f"{c['pnl']:+.4f}" if c["pnl"] is not None else "n/a"
            md.append(
                f"| {c['id']} | {c['pair']} | {c['entry']} | {c['exit']} | "
                f"{c['size']} | {pnl_s} | {c['notes']} |"
            )
        md.append("")

    # Risk events
    md.append(section(f"Risk Events Today ({len(risk_events)})"))
    if not risk_events:
        md.append("_No risk events._\n")
    else:
        for r in risk_events:
            md.append(f"- `{r['ts']}` **{r['event_type']}** [{r['severity']}] {r['payload']}")
        md.append("")

    # Top candidates
    md.append(section("Top Scan Candidates"))
    if not candidates:
        md.append("_No candidates surfaced._\n")
    elif isinstance(candidates[0], str):
        md.append(f"`{candidates[0]}`\n")
    else:
        md.append("| Score | Kind | Symbol | Sector | Event | Rationale |")
        md.append("|---:|---|---|---|---|---|")
        for c in candidates[:8]:
            md.append(
                f"| {c.score:.1f} | {c.kind} | {c.symbol} | {c.sector} | "
                f"{c.event_type} | {c.rationale[:90]} |"
            )
        md.append("")

    # News headlines
    md.append(section(f"News Headlines Today ({len(news)})"))
    if not news:
        md.append("_No news (daemon idle?)._\n")
    else:
        for n in news[:10]:
            ts = n["published_at"]
            ts_s = ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)
            sym = f" `{n['symbols']}`" if n["symbols"] else ""
            md.append(f"- {ts_s} {n['source']}: [{n['title']}]({n['url']}){sym}")
        md.append("")

    # Log tails
    md.append(section("Recent Live Executor Log (last 10)"))
    lines = tail_log(LIVE_LOG, 10)
    md.append("```\n" + ("".join(lines) if lines else "(empty)\n") + "```\n")

    md.append(section("Recent Alerts Log (last 10)"))
    lines = tail_log(ALERT_LOG, 10)
    md.append("```\n" + ("".join(lines) if lines else "(empty)\n") + "```\n")

    return "\n".join(md)


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily summary markdown writer.")
    parser.add_argument("--date", help="YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--stdout", action="store_true", help="print to stdout instead of file")
    args = parser.parse_args()

    if args.date:
        target = date.fromisoformat(args.date)
    else:
        target = datetime.now(timezone.utc).date()

    report = build_report(target)

    if args.stdout:
        print(report)
        return 0

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"{target.isoformat()}.md"
    out.write_text(report)
    print(f"[daily_report] written {out} ({len(report)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
