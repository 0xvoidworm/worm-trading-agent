"""Telegram notification module.

Sends alerts to user via Telegram Bot API. Reuses TIKTOK_BOT_TOKEN credential
(yang sudah ada di ~/.secrets/telegram.env untuk TikTok downloader bot). Bot
sama bisa multi-purpose: TikTok DL + trading alerts.

Untuk bisa kirim DM ke user, butuh chat_id user (one-time setup).

Discovery flow:
1. User chat ke bot (cukup /start atau message apapun)
2. Call discover_chat_id() — auto-detect latest chat
3. Save ke ~/.secrets/telegram.env sebagai TELEGRAM_CHAT_ID

Atau user manually set TELEGRAM_CHAT_ID via:
    echo "TELEGRAM_CHAT_ID=123456789" >> ~/.secrets/telegram.env

Graceful no-op kalau token/chat_id tidak available — caller dapat False
return tanpa exception.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import requests


SECRETS_PATH = Path.home() / ".secrets" / "telegram.env"
HTTP_TIMEOUT = 10


def _load_env() -> dict[str, str]:
    if not SECRETS_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_bot_token() -> Optional[str]:
    env = _load_env()
    # Reuse TIKTOK_BOT_TOKEN as the bot. Could be renamed but compatible.
    return env.get("TIKTOK_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")


def get_chat_id() -> Optional[str]:
    env = _load_env()
    return env.get("TELEGRAM_CHAT_ID")


def discover_chat_id() -> Optional[str]:
    """Query getUpdates to find most recent chat that messaged the bot.

    User must have sent at least one message to the bot. Returns chat_id
    as string, or None if no recent messages.
    """
    token = get_bot_token()
    if not token:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        updates = data.get("result", [])
        if not updates:
            return None
        # Most recent message's chat id
        last = updates[-1]
        msg = last.get("message") or last.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        return str(cid) if cid is not None else None
    except Exception:
        return None


def send_message(
    text: str,
    chat_id: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    silent: bool = False,
) -> bool:
    """Send Telegram message. Returns True on success, False on no-op or failure.

    No-op (False) reasons:
    - Bot token not configured
    - Chat ID not configured + can't discover
    - HTTP error / timeout

    Doesn't raise — caller can ignore return for fire-and-forget pattern.
    """
    token = get_bot_token()
    if not token:
        return False
    cid = chat_id or get_chat_id()
    if not cid:
        cid = discover_chat_id()
        if not cid:
            return False

    payload = {
        "chat_id": cid,
        "text": text,
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception:
        return False


def notify_trade(action: str, symbol: str, details: dict, urgent: bool = False) -> bool:
    """Higher-level helper: format trade event as Telegram message.

    Uses plain text (no Markdown) to avoid parse errors with underscores
    in keys like trade_id, asset symbols with slashes, etc.
    """
    icon = {"OPEN": "BUY", "CLOSE": "SELL", "SKIP": "SKIP", "ERROR": "ERR",
            "TAKE_PROFIT": "TP-HIT", "STOP_LOSS": "SL-HIT",
            "TIME_STOP": "TIME-STOP", "PROTECT": "PROTECT",
            "ALERT": "ALERT"}.get(action, action)
    lines = [f"[{icon}] {symbol}"]
    for k, v in details.items():
        lines.append(f"  {k}: {v}")
    text = "\n".join(lines)
    return send_message(text, parse_mode=None, silent=not urgent)
