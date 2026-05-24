#!/usr/bin/env python3
"""Binance LIVE smoke test (path alpha step 1) - REAL ACCOUNT.

5-layer diagnostic:
  1. Market load (endpoint reachable)
  2. Spot balance (per-asset non-zero list)
  3. Futures USDT-M balance + total
  4. Open futures positions
  5. API key restriction introspection (Binance-specific endpoint)

Reads creds from ~/.secrets/binance.env. Never prints key/secret values.

Usage:
    python scripts/binance_smoke.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import ccxt


SECRETS = Path.home() / ".secrets" / "binance.env"


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f"[FAIL] secrets file not found: {path}")
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def main() -> int:
    env = load_env(SECRETS)
    api_key = env.get("BINANCE_API_KEY", "")
    secret = env.get("BINANCE_API_SECRET", "")

    if not api_key or not secret:
        sys.exit("[FAIL] BINANCE_API_KEY or BINANCE_API_SECRET empty in env file")

    print(f"[smoke] LIVE Binance, key_prefix={api_key[:4]}..., secret_len={len(secret)}")
    print("[smoke] *** REAL ACCOUNT, NO SANDBOX *** verify key permission scope before trade")
    print()

    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "timeout": 15_000,
        "options": {"defaultType": "spot"},
    })

    # --- 1. Market load ---
    try:
        ex.load_markets()
        n_mk = len(ex.markets or {})
        sample = next(iter(ex.markets or {}), "?")
        print(f"[1/5] load_markets OK -- {n_mk} markets (sample: {sample})")
    except Exception as e:
        print(f"[1/5] load_markets FAIL -- {type(e).__name__}: {e}")
        return 1

    # --- 2. Spot balance ---
    try:
        bal = ex.fetch_balance({"type": "spot"})
        totals = bal.get("total") or {}
        nonzero = {k: v for k, v in totals.items() if v and float(v) > 0}
        print(f"[2/5] spot balance OK -- {len(nonzero)} non-zero assets")
        for asset, amt in sorted(nonzero.items(), key=lambda kv: -float(kv[1])):
            print(f"       {asset:>8s}: {float(amt):.8g}")
        if not nonzero:
            print("       (empty spot wallet)")
    except Exception as e:
        print(f"[2/5] spot balance FAIL -- {type(e).__name__}: {e}")
        traceback.print_exc()
        return 2

    # --- 3. Futures balance (USDT-M) ---
    try:
        bal_fut = ex.fetch_balance({"type": "future"})
        usdt = bal_fut.get("USDT", {}) or {}
        total = float(usdt.get("total") or 0)
        free = float(usdt.get("free") or 0)
        used = float(usdt.get("used") or 0)
        print(f"[3/5] futures USDT-M OK -- total={total:,.4f} free={free:,.4f} used={used:,.4f}")
    except Exception as e:
        print(f"[3/5] futures balance SKIP/FAIL -- {type(e).__name__}: {e}")

    # --- 4. Open futures positions ---
    try:
        positions = ex.fetch_positions()
        open_pos = [p for p in positions if float(p.get("contracts") or 0) != 0]
        print(f"[4/5] futures positions OK -- {len(open_pos)} open / {len(positions)} tracked")
        for p in open_pos[:5]:
            print(f"       . {p.get('symbol')} {p.get('side')} contracts={p.get('contracts')} pnl={p.get('unrealizedPnl')}")
    except Exception as e:
        print(f"[4/5] positions FAIL -- {type(e).__name__}: {e}")

    # --- 5. API key restriction introspection (Binance-specific) ---
    try:
        # ccxt method: sapi_get_account_apirestrictions  (Binance private SAPI endpoint)
        info = ex.sapi_get_account_apirestrictions()  # type: ignore[attr-defined]
        print(f"[5/5] api restrictions OK")
        important_keys = (
            "ipRestrict", "enableReading", "enableSpotAndMarginTrading",
            "enableFutures", "enableMargin", "enableWithdrawals",
            "enableInternalTransfer", "permitsUniversalTransfer",
            "tradingAuthorityExpirationTime",
        )
        for k in important_keys:
            if k in info:
                v = info[k]
                marker = ""
                if k == "enableWithdrawals" and v:
                    marker = "  <-- DANGER: withdraw allowed"
                if k == "ipRestrict" and not v:
                    marker = "  <-- WARNING: no IP whitelist"
                print(f"       {k}: {v}{marker}")
    except Exception as e:
        print(f"[5/5] api restrictions SKIP -- {type(e).__name__}: {e}")

    print()
    print("[SMOKE PASS] Binance live auth + market + account access verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
