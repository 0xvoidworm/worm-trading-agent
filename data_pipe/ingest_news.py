"""RSS news ingest — 5 sumber crypto utama.

Design notes:
- RSS dipilih karena free, no-auth, public endpoint. CryptoPanic API unreliable.
- Dedup via SHA1(url) → primary key. Rerun aman, no dupe.
- Auto-tag symbol: regex word-boundary match ticker terhadap title.
- Timeout per-feed 10s. Kalau satu feed down, lanjut sisanya.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import feedparser
import requests

from .retry import retry_network, is_retryable
from .storage import Storage


# Sumber RSS. Prioritas: sumber dengan signal-to-noise terbaik.
FEEDS = [
    ("coindesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock",      "https://www.theblock.co/rss.xml"),
    ("decrypt",       "https://decrypt.co/feed"),
    ("bitcoinmag",    "https://bitcoinmagazine.com/feed"),
]

# Ticker yang kita track. Cari match word-boundary di title.
# Kata umum yang bisa false-match (OP = "op-ed", ARB = "arbitrage") dihandle
# lewat boundary + case sensitive check.
TRACKED_TICKERS = ["BTC", "ETH", "SOL", "BNB", "XRP", "AVAX", "ARB", "OP"]
# Juga cover nama lengkap → map ke ticker
NAME_TO_TICKER = {
    r"\bBitcoin\b": "BTC",
    r"\bEthereum\b": "ETH",
    r"\bSolana\b": "SOL",
    r"\bBinance Coin\b": "BNB",
    r"\bRipple\b": "XRP",
    r"\bAvalanche\b": "AVAX",
    r"\bArbitrum\b": "ARB",
    r"\bOptimism\b": "OP",
}


def _hash_id(url: str) -> str:
    """SHA1 of url, first 16 hex. Cukup untuk dedup, compact."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _extract_symbols(title: str) -> str:
    """Return comma-separated tickers yang disebut di title."""
    found = set()
    # Ticker case-sensitive match (hindari "ARB" match "arbitrage" lowercase)
    for tkr in TRACKED_TICKERS:
        if re.search(rf"\b{tkr}\b", title):
            found.add(tkr)
    # Nama lengkap (case-insensitive)
    for pattern, tkr in NAME_TO_TICKER.items():
        if re.search(pattern, title, re.IGNORECASE):
            found.add(tkr)
    return ",".join(sorted(found))


def _parse_published(entry) -> datetime | None:
    """Extract published time dari feedparser entry, return UTC aware."""
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def fetch_feed(source: str, url: str, timeout: int = 10) -> list[dict] | None:
    """Pull satu RSS feed.

    Return:
      - list[dict] siap upsert (bisa kosong kalau feed valid tapi tanpa entry baru)
      - None kalau feed unreachable (DNS/connection/HTTP error setelah retry)

    Pemisahan None vs [] penting untuk tracking FEED_DEAD: feed yang return
    valid-empty jangan di-count sebagai failure.
    """
    rows = []
    fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        resp = retry_network(
            lambda: requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; trading-agent-bot/0.1)"},
                timeout=timeout,
            ),
            label=f"feed {source}",
        )
    except Exception as e:
        # Network exhausted (DNS/connection) atau non-retryable HTTP error.
        # Treat sebagai unreachable, caller akan mark failure.
        if is_retryable(e) or isinstance(e, requests.exceptions.RequestException):
            print(f"[news] {source} fetch timeout/error: {e}", flush=True)
        else:
            print(f"[news] {source} unexpected fetch error: {e!r}", flush=True)
        return None

    if resp.status_code != 200:
        print(f"[news] {source} HTTP {resp.status_code}", flush=True)
        return None

    try:
        parsed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"[news] {source} parse error: {e}", flush=True)
        return None

    if parsed.bozo and not parsed.entries:
        print(f"[news] {source} feed broken: {parsed.get('bozo_exception')}", flush=True)
        return None

    for entry in parsed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        pub = _parse_published(entry)
        if not pub:
            continue
        rows.append({
            "id": _hash_id(link),
            "source": source,
            "title": title[:500],  # cap title length
            "url": link[:1000],
            "published_at": pub.replace(tzinfo=None),  # DuckDB TIMESTAMP naive UTC
            "fetched_at": fetched_at,
            "symbols": _extract_symbols(title),
        })
    return rows


def ingest_all_feeds(storage: Storage) -> dict:
    """Pull semua feed, upsert ke DB. Return stats per-source.

    Stats shape:
      {
        "<source>": int (rows upserted, atau 0),
        "_total": int,
        "_duration_sec": float,
        "_failed": [<source>, ...],     # feed unreachable/broken
        "_ok": [<source>, ...],          # feed reachable (bisa rows=0)
      }

    Per-feed try independent — satu feed down tidak membatalkan yang lain.
    """
    stats: dict = {}
    failed: list[str] = []
    ok: list[str] = []
    total = 0
    t0 = time.time()
    for source, url in FEEDS:
        rows = fetch_feed(source, url)
        if rows is None:
            stats[source] = 0
            failed.append(source)
            continue
        n = storage.upsert_news(rows)
        stats[source] = n
        total += n
        ok.append(source)
    stats["_total"] = total
    stats["_duration_sec"] = round(time.time() - t0, 2)
    stats["_failed"] = failed
    stats["_ok"] = ok
    return stats


if __name__ == "__main__":
    with Storage() as s:
        print(ingest_all_feeds(s))
