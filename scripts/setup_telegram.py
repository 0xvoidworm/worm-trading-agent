#!/usr/bin/env python3
"""One-time setup helper for Telegram alerts.

Workflow:
1. User opens Telegram, search for @userinfobot, send /start, read user id
2. Run: python scripts/setup_telegram.py <USER_ID>
3. Script writes TELEGRAM_CHAT_ID to ~/.secrets/telegram.env + sends test message

Atau:
1. User send /start ke bot trading (yang sama dengan tiktok-bot token)
2. **Stop tiktok-bot.service sementara** supaya queue tidak ke-consume:
       sudo systemctl stop tiktok-bot
3. Run: python scripts/setup_telegram.py --discover
4. Restart tiktok-bot:
       sudo systemctl start tiktok-bot
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from data_pipe.telegram import discover_chat_id, send_message, SECRETS_PATH


def write_chat_id(cid: str) -> None:
    existing = ""
    if SECRETS_PATH.exists():
        existing = SECRETS_PATH.read_text()
    # Remove old TELEGRAM_CHAT_ID lines
    lines = [
        ln for ln in existing.splitlines()
        if not ln.strip().lstrip("export ").startswith("TELEGRAM_CHAT_ID=")
    ]
    lines.append(f"TELEGRAM_CHAT_ID={cid}")
    SECRETS_PATH.write_text("\n".join(lines) + "\n")
    SECRETS_PATH.chmod(0o600)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("chat_id", nargs="?", help="Telegram user/chat ID (from @userinfobot)")
    p.add_argument("--discover", action="store_true", help="Auto-discover via getUpdates (stop tiktok-bot first)")
    args = p.parse_args()

    cid: str | None = args.chat_id
    if args.discover:
        cid = discover_chat_id()
        if not cid:
            print("[setup_telegram] discover failed — no recent messages in getUpdates queue.")
            print("                  Either:")
            print("                  (a) Send /start ke bot, then re-run --discover")
            print("                  (b) Stop tiktok-bot.service dulu agar queue tidak consumed")
            print("                  (c) Pass chat_id manually: python scripts/setup_telegram.py 123456789")
            return 1

    if not cid:
        print("Usage: python scripts/setup_telegram.py <CHAT_ID>")
        print("       python scripts/setup_telegram.py --discover")
        print()
        print("Cara dapat CHAT_ID:")
        print("  1. Buka Telegram, search @userinfobot, send /start")
        print("  2. Bot reply dengan 'Id: 123456789' — copy angka itu")
        print("  3. Run: python scripts/setup_telegram.py 123456789")
        return 1

    write_chat_id(cid)
    print(f"[setup_telegram] wrote TELEGRAM_CHAT_ID={cid} ke {SECRETS_PATH}")
    ok = send_message(
        f"*[TRADING AGENT TELEGRAM SETUP OK]*\n"
        f"Chat ID: `{cid}`\n"
        f"Trade alerts + risk events akan dikirim ke sini.",
        chat_id=cid,
    )
    if ok:
        print("[setup_telegram] test message sent — cek Telegram lo")
        return 0
    else:
        print("[setup_telegram] test message FAILED — bot token mungkin invalid atau chat_id salah")
        return 2


if __name__ == "__main__":
    sys.exit(main())
