#!/usr/bin/env python3
"""CLI untuk Phase 1 live spot ops di Binance.

Defaults --dry untuk safety. Pakai --live untuk eksekusi real order.

Commands:
    trade_live.py status                     show balances + allocation + open positions
    trade_live.py risk                       show risk_guard state
    trade_live.py scan                       show news + suggestions (Phase 2 wires LLM)
    trade_live.py open SYMBOL USDT [--thesis] [--live]
    trade_live.py close TRADE_ID [--live]
    trade_live.py panic [--live]             close ALL open spot positions
    trade_live.py reset-daily                remove data/risk.killswitch
    trade_live.py reset-permanent            remove data/permanent.killswitch

Examples:
    python scripts/trade_live.py status
    python scripts/trade_live.py open BNB/USDT 5 --thesis "test plumbing"
    python scripts/trade_live.py open BNB/USDT 5 --thesis "real entry" --live
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.brokers import make_broker  # noqa: E402
from data_pipe.live_executor import (  # noqa: E402
    execute_live_close,
    execute_live_open,
    execute_panic_close_all,
)
from data_pipe.portfolio import get_account_state  # noqa: E402
from data_pipe.risk_guard import (  # noqa: E402
    check_max_trades_per_day,
    get_daily_pnl_state,
    get_equity_drawdown,
    is_killswitch_active,
    is_permanent_killswitch_active,
    remove_killswitch,
    remove_permanent_killswitch,
)
from data_pipe.storage import Storage  # noqa: E402
from data_pipe.strategy import (  # noqa: E402
    allocate_capital,
    get_recent_news_context,
)


DB_PATH = REPO_ROOT / "data" / "market.duckdb"
CFG_PATH = REPO_ROOT / "config" / "pairs.yaml"
DATA_DIR = REPO_ROOT / "data"


def _load_cfg() -> dict:
    return yaml.safe_load(CFG_PATH.read_text()) or {}


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


# ─── command handlers ──────────────────────────────────────────────────────

def cmd_status(args, storage: Storage, cfg: dict) -> int:
    broker = make_broker(live=args.live)
    _print_section(f"Binance Spot (mode={'LIVE' if broker.is_live else 'DRY'})")
    try:
        bal = broker.fetch_spot_balance()
        total_usdt_est = 0.0
        for asset, amount in sorted(bal.items(), key=lambda x: -x[1]):
            if asset == "USDT":
                usd = amount
            else:
                try:
                    tk = broker.get_ticker(f"{asset}/USDT")
                    usd = amount * float(tk.get("last") or 0)
                except Exception:
                    usd = 0
            total_usdt_est += usd
            print(f"  {asset:>8s}: {amount:>14.8g}  ~${usd:>10.4f}")
        if not bal:
            print("  (empty)")
        print(f"  TOTAL ~${total_usdt_est:.4f}")
    except Exception as e:
        print(f"  fetch_balance err: {e}")
        total_usdt_est = 0.0

    _print_section("Allocation (hybrid 70/30)")
    alloc = allocate_capital(total_usdt_est, cfg)
    for k, v in alloc.as_dict().items():
        print(f"  {k:>22s}: {v}")

    _print_section("Open positions (trade_journal)")
    df = storage.get_trades(status="OPEN")
    if df.empty:
        print("  (no open trades in journal)")
    else:
        cols = ["id", "ts", "pair", "direction", "entry_price", "size", "thesis"]
        print(df[cols].to_string(index=False))

    _print_section("Account state (caps)")
    state = get_account_state(storage, cfg)
    for k in ("equity", "gross_notional", "gross_pct", "n_open",
              "max_concurrent", "headroom", "max_position_pct", "max_gross_pct"):
        print(f"  {k:>22s}: {state.get(k)}")
    return 0


def cmd_risk(args, storage: Storage, cfg: dict) -> int:
    _print_section("Daily PnL state")
    daily = get_daily_pnl_state(storage, cfg)
    for k in ("equity_start", "realized_today", "unrealized_now",
              "daily_pnl", "daily_pnl_pct", "status",
              "threshold_pct", "warn_pct"):
        v = daily.get(k)
        v = round(v, 4) if isinstance(v, float) else v
        print(f"  {k:>22s}: {v}")

    _print_section("Permanent floor state")
    dd = get_equity_drawdown(storage, cfg)
    for k in ("starting_capital", "equity_realized", "unrealized",
              "equity_mtm", "drawdown_abs", "drawdown_pct",
              "floor_pct", "tripped"):
        v = dd.get(k)
        v = round(v, 4) if isinstance(v, float) else v
        print(f"  {k:>22s}: {v}")

    _print_section("Trade cap today")
    tc = check_max_trades_per_day(storage, cfg)
    for k, v in tc.items():
        print(f"  {k:>22s}: {v}")

    _print_section("Killswitch files")
    print(f"  daily risk.killswitch    : {is_killswitch_active(DATA_DIR)}")
    print(f"  permanent.killswitch     : {is_permanent_killswitch_active(DATA_DIR)}")
    print(f"  manual executor.disabled : {(DATA_DIR / 'executor.disabled').exists()}")
    return 0


def cmd_scan(args, storage: Storage, cfg: dict) -> int:
    from data_pipe.event_scanner import scan_all_candidates
    from data_pipe.sectors import diversification_status

    _print_section("Recent news (last 24h)")
    news = get_recent_news_context(storage, hours=24, limit=10)
    if news.empty:
        print("  (no news — daemon mati / DB news table kosong)")
    else:
        for _, row in news.iterrows():
            ts = row["published_at"]
            ts_s = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            sym = f"[{row['symbols']}]" if row.get("symbols") else ""
            print(f"  {ts_s}  {row['source']:>14s}  {sym}  {row['title'][:100]}")

    _print_section("Phase 2 candidates (event + narrative scan)")
    broker = make_broker(live=False)
    try:
        cands = scan_all_candidates(storage, broker)
    except Exception as e:
        print(f"  scan err: {type(e).__name__}: {e}")
        return 1
    if not cands:
        print("  (no candidates surfaced)")
        return 0

    # Open positions for diversification check
    open_df = storage.get_trades(status="OPEN")
    open_symbols = open_df["pair"].astype(str).tolist() if not open_df.empty else []

    for c in cands[:15]:
        div = diversification_status(c.symbol, open_symbols)
        warn = ""
        if div["concentration_risk"]:
            warn = f" CONCENTRATED w/ {','.join(div['matching_open'])}"
        print(f"  score={c.score:>4.1f} {c.kind:<9s} {c.symbol:<14s} [{c.event_type}] sector={c.sector}{warn}")
        print(f"     {c.rationale[:140]}")
        if c.source_url:
            print(f"     src: {c.source_url[:80]}")
    print()
    if open_symbols:
        open_sectors = [c for s in open_symbols for c in [diversification_status(s, [])["candidate_sector"]] if c != "UNKNOWN"]
        print(f"[scan] open positions sectors: {', '.join(set(open_sectors)) if open_sectors else 'UNKNOWN'}")
    print(f"[scan] {len(cands)} total candidates. Pick high score + non-concentrated for #N+1 position.")
    return 0


def cmd_open(args, storage: Storage, cfg: dict) -> int:
    broker = make_broker(live=args.live)
    print(f"[open] mode={'LIVE' if broker.is_live else 'DRY'} symbol={args.symbol} usdt={args.usdt}")
    if broker.is_live:
        print("[open] *** REAL MONEY — order will be placed ***")
    result = execute_live_open(
        storage=storage,
        cfg=cfg,
        data_dir=DATA_DIR,
        broker=broker,
        symbol=args.symbol,
        usdt_amount=float(args.usdt),
        thesis=args.thesis or "manual cli open",
    )
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.action in ("OPEN",) else 1


def cmd_close(args, storage: Storage, cfg: dict) -> int:
    broker = make_broker(live=args.live)
    print(f"[close] mode={'LIVE' if broker.is_live else 'DRY'} trade_id={args.trade_id}")
    result = execute_live_close(
        storage=storage,
        cfg=cfg,
        data_dir=DATA_DIR,
        broker=broker,
        trade_id=int(args.trade_id),
    )
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.action in ("CLOSE",) else 1


def cmd_panic(args, storage: Storage, cfg: dict) -> int:
    broker = make_broker(live=args.live)
    print(f"[panic] mode={'LIVE' if broker.is_live else 'DRY'}")
    if broker.is_live:
        confirm = input("Type 'PANIC' to confirm closing ALL open positions: ")
        if confirm.strip() != "PANIC":
            print("[panic] aborted")
            return 1
    results = execute_panic_close_all(
        storage=storage, cfg=cfg, data_dir=DATA_DIR, broker=broker,
    )
    if not results:
        print("[panic] no open LONG positions to close")
        return 0
    for r in results:
        print(json.dumps(r.to_dict(), indent=2, default=str))
    return 0


def cmd_reset_daily(args, storage: Storage, cfg: dict) -> int:
    prev = remove_killswitch(DATA_DIR)
    if prev is None:
        print("[reset-daily] file tidak ada — no-op")
    else:
        print(f"[reset-daily] removed. previous: {json.dumps(prev, indent=2, default=str)}")
    return 0


def cmd_protect(args, storage: Storage, cfg: dict) -> int:
    """Place protective OCO (TP + SL) on exchange for a trade."""
    from data_pipe.protection import place_protective_oco

    broker = make_broker(live=args.live)
    if not broker.is_live:
        print("[protect] dry-run mode — OCO cannot be simulated meaningfully. Use --live to place real OCO.")
        return 1

    row = storage._conn.execute(
        "SELECT id, pair, entry_price, size, direction, status FROM trade_journal WHERE id=?",
        [int(args.trade_id)],
    ).fetchone()
    if row is None:
        print(f"[protect] trade #{args.trade_id} not found")
        return 1
    tid, pair, entry_price, size, direction, status = row
    if status != "OPEN":
        print(f"[protect] trade #{tid} status={status} (must be OPEN)")
        return 1
    if direction != "LONG":
        print(f"[protect] trade #{tid} direction={direction} (only LONG supported)")
        return 1

    print(f"[protect] trade #{tid} {pair} entry=${entry_price:.6g} size={size:.6g}")
    print(f"[protect] target TP +{args.tp:.1f}% (${entry_price*(1+args.tp/100):.6g})")
    print(f"[protect] target SL -{args.sl:.1f}% (${entry_price*(1-args.sl/100):.6g})")
    print("[protect] *** REAL OCO will be placed, asset locked until fill or cancel ***")

    result = place_protective_oco(
        broker=broker, trade_id=int(tid), symbol=str(pair),
        quantity=float(size), entry_price=float(entry_price),
        tp_pct=float(args.tp), sl_pct=float(args.sl),
    )
    if not result.success:
        print(f"[protect] FAILED: {result.error}")
        return 1
    print(f"[protect] OK order_list_id={result.order_list_id} client_id={result.list_client_order_id}")
    print(f"          TP=${result.tp_price:.6g}  SL_stop=${result.sl_stop:.6g}  SL_limit=${result.sl_limit:.6g}")
    return 0


def cmd_protect_cancel(args, storage: Storage, cfg: dict) -> int:
    """Cancel protective OCO for a trade."""
    from data_pipe.protection import cancel_protection
    broker = make_broker(live=args.live)
    if not broker.is_live:
        print("[protect-cancel] dry-run can't cancel real OCO. Use --live.")
        return 1
    result = cancel_protection(broker, int(args.trade_id))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("success") else 1


def cmd_protect_status(args, storage: Storage, cfg: dict) -> int:
    """Show protection state for all trades or one trade_id."""
    from data_pipe.protection import get_protection_status
    broker = make_broker(live=args.live)
    tid = int(args.trade_id) if args.trade_id else None
    result = get_protection_status(broker, tid)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_reset_permanent(args, storage: Storage, cfg: dict) -> int:
    print("[reset-permanent] WARNING: this lifts the cumulative drawdown stop.")
    print("                  ensure you've done post-mortem + top-up before reset.")
    confirm = input("Type 'UNFREEZE' to confirm: ")
    if confirm.strip() != "UNFREEZE":
        print("[reset-permanent] aborted")
        return 1
    prev = remove_permanent_killswitch(DATA_DIR)
    if prev is None:
        print("[reset-permanent] file tidak ada — no-op")
    else:
        print(f"[reset-permanent] removed. previous: {json.dumps(prev, indent=2, default=str)}")
    return 0


# ─── argparse ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trade_live",
        description="Phase 1 live spot trading CLI for Binance (Bahrain-compatible spot).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="show balance + allocation + open positions")
    s.add_argument("--live", action="store_true", help="use live broker (default: dry-run)")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("risk", help="show daily PnL + drawdown + killswitch state")
    s.set_defaults(func=cmd_risk)

    s = sub.add_parser("scan", help="show news + strategy candidates (Phase 1 = stubs)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("open", help="open spot LONG position via market BUY")
    s.add_argument("symbol", help="e.g. BNB/USDT")
    s.add_argument("usdt", type=float, help="USDT amount to spend")
    s.add_argument("--thesis", default="", help="rationale (logged to trade_journal)")
    s.add_argument("--live", action="store_true", help="place real order (default: dry-run)")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("close", help="close spot LONG by trade_id via market SELL")
    s.add_argument("trade_id", type=int)
    s.add_argument("--live", action="store_true", help="place real order (default: dry-run)")
    s.set_defaults(func=cmd_close)

    s = sub.add_parser("panic", help="close ALL open LONG positions immediately")
    s.add_argument("--live", action="store_true", help="place real orders (default: dry-run)")
    s.set_defaults(func=cmd_panic)

    s = sub.add_parser("protect", help="place protective OCO (TP+SL) on exchange for open trade")
    s.add_argument("trade_id", type=int)
    s.add_argument("--tp", type=float, default=5.0, help="take-profit pct above entry (default 5)")
    s.add_argument("--sl", type=float, default=5.0, help="stop-loss pct below entry (default 5)")
    s.add_argument("--live", action="store_true", help="place real OCO (required)")
    s.set_defaults(func=cmd_protect)

    s = sub.add_parser("protect-cancel", help="cancel protective OCO for trade")
    s.add_argument("trade_id", type=int)
    s.add_argument("--live", action="store_true", help="real cancel (required)")
    s.set_defaults(func=cmd_protect_cancel)

    s = sub.add_parser("protect-status", help="show protection state for trades")
    s.add_argument("trade_id", type=int, nargs="?", help="optional, default all")
    s.add_argument("--live", action="store_true", help="query exchange for fresh state")
    s.set_defaults(func=cmd_protect_status)

    s = sub.add_parser("reset-daily", help="remove data/risk.killswitch (manual reset)")
    s.set_defaults(func=cmd_reset_daily)

    s = sub.add_parser("reset-permanent", help="remove data/permanent.killswitch")
    s.set_defaults(func=cmd_reset_permanent)

    return p


def main() -> int:
    args = build_parser().parse_args()
    cfg = _load_cfg()
    storage = Storage(str(DB_PATH))
    try:
        return args.func(args, storage, cfg)
    finally:
        try:
            storage.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
