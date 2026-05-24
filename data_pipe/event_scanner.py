"""Event + narrative scanners for Phase 2 strategy layer.

Two source families (both free, no auth):

1. NEWS-DRIVEN EVENTS — keyword filter on existing news DB (RSS feeds:
   coindesk, cointelegraph, theblock, decrypt, bitcoinmag). Looks for
   announcement-pattern hits: "Binance lists/delists", "launchpool",
   "token burn", "airdrop", "partnership". Symbol extracted from news.symbols
   tag or title via regex.

2. NARRATIVE TRENDING — CoinGecko `/search/trending` (7-day search volume
   ranking). Free endpoint, no auth, rate-limited ~10/min. Returns top
   ~15 trending coins. Filter against Binance Spot tradeable list.

Heuristic scoring (Phase 2 stub):
- Event news within last 6h + Binance tradeable    → score 7-9
- Trending in CoinGecko + Binance tradeable        → score 5-7
- Older signals or not-tradeable                   → score 1-3

Real LLM scoring (Phase 3) replaces heuristic with structured prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests


COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
HTTP_TIMEOUT = 15

# Keyword patterns for event detection in news title
EVENT_PATTERNS = {
    "LISTING": re.compile(r"\b(binance lists?|new listing|will list|listed on binance)\b", re.I),
    "DELISTING": re.compile(r"\b(binance delist|will delist|will remove|trading pairs removed)\b", re.I),
    "LAUNCHPOOL": re.compile(r"\b(launchpool|launchpad)\b", re.I),
    "TOKEN_BURN": re.compile(r"\b(token burn|burn event|coin burn)\b", re.I),
    "AIRDROP": re.compile(r"\b(airdrop|claim event)\b", re.I),
    "PARTNERSHIP": re.compile(r"\b(partnership|partners with|collab)\b", re.I),
    "UPGRADE": re.compile(r"\b(mainnet upgrade|hard fork|protocol upgrade)\b", re.I),
}

# Match ticker patterns: $BTC, BTC, BTC/USDT
SYMBOL_RE = re.compile(r"\$?\b([A-Z]{2,8})(?:/USDT|/USD)?\b")


@dataclass
class Candidate:
    symbol: str           # "BNB/USDT" format
    base: str             # "BNB"
    kind: str             # "EVENT" / "NARRATIVE" / "REL_VALUE"
    event_type: Optional[str]  # LISTING / TRENDING / etc
    score: float          # 0-10 heuristic
    rationale: str        # short thesis text
    source_url: Optional[str] = None
    detected_at: datetime = None  # type: ignore[assignment]
    sector: str = "UNKNOWN"

    def __post_init__(self):
        if self.detected_at is None:
            self.detected_at = datetime.now(timezone.utc)
        if self.sector == "UNKNOWN":
            from .sectors import get_sector
            self.sector = get_sector(self.symbol)


# ─── news-driven events ────────────────────────────────────────────────────

def scan_news_events(
    storage,
    lookback_hours: int = 6,
    limit: int = 50,
) -> list[Candidate]:
    """Scan recent news for event patterns. Returns Candidates ranked by recency."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    df = storage._conn.execute(
        """
        SELECT published_at, source, title, url, symbols
        FROM news
        WHERE published_at >= ?
        ORDER BY published_at DESC
        LIMIT ?
        """,
        [cutoff, limit],
    ).fetchdf()
    if df.empty:
        return []

    candidates: list[Candidate] = []
    for _, row in df.iterrows():
        title = str(row["title"] or "")
        for event_type, pattern in EVENT_PATTERNS.items():
            if not pattern.search(title):
                continue
            # Find symbol — first try `symbols` field
            sym_field = str(row.get("symbols") or "").strip()
            bases: list[str] = []
            if sym_field:
                bases = [s.strip() for s in sym_field.split(",") if s.strip()]
            if not bases:
                # Fallback: extract from title via regex
                hits = SYMBOL_RE.findall(title)
                # Filter out common English words
                stopwords = {"THE", "AND", "FOR", "WITH", "NEW", "WILL", "BINANCE", "USDT", "USD", "CRYPTO", "TOKEN", "COIN"}
                bases = [h for h in hits if h not in stopwords][:1]
            if not bases:
                continue
            base = bases[0].upper()
            published = row.get("published_at")
            age_h = 24.0
            if hasattr(published, "to_pydatetime"):
                p = published.to_pydatetime()
                if p.tzinfo is None:
                    p = p.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - p).total_seconds() / 3600
            # Score: 9 if < 1h, 7 if < 3h, 5 if < 6h, 3 otherwise
            score = 9.0 if age_h < 1 else (7.0 if age_h < 3 else (5.0 if age_h < 6 else 3.0))
            # Delisting events negative — short candidate, but we do spot long-only,
            # so flag as AVOID (score 1) instead.
            if event_type == "DELISTING":
                score = 1.0
            candidates.append(Candidate(
                symbol=f"{base}/USDT",
                base=base,
                kind="EVENT",
                event_type=event_type,
                score=score,
                rationale=f"{event_type} news ({age_h:.1f}h ago, source {row['source']}): {title[:120]}",
                source_url=str(row.get("url") or ""),
            ))
    return candidates


# ─── CoinGecko trending ────────────────────────────────────────────────────

def fetch_coingecko_trending() -> list[dict]:
    """Top trending coins last 7 days. Free endpoint, no auth.

    Returns list of dicts with keys: id, name, symbol, market_cap_rank, score.
    """
    r = requests.get(COINGECKO_TRENDING_URL, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out: list[dict] = []
    for c in data.get("coins", []):
        item = c.get("item", {})
        out.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "symbol": (item.get("symbol") or "").upper(),
            "market_cap_rank": item.get("market_cap_rank"),
            "score": item.get("score"),
            "price_btc": item.get("price_btc"),
        })
    return out


def scan_narrative_trending(broker) -> list[Candidate]:
    """CoinGecko trending → filter to Binance Spot tradeable USDT pairs.

    Heuristic score 5-7: rank-weighted (higher market cap rank = bigger
    score because more liquidity but less explosive; smaller rank = more
    speculative but bigger upside potential — we score speculative mid-7
    and liquid mid-5).
    """
    try:
        trending = fetch_coingecko_trending()
    except Exception as e:
        return [Candidate(
            symbol="(scan_failed)", base="?", kind="NARRATIVE",
            event_type="ERROR", score=0.0,
            rationale=f"CoinGecko fetch error: {type(e).__name__}: {e}",
        )]

    # Build set of tradeable USDT spot pairs from broker
    try:
        broker._ensure_markets()
        tradeable_bases = {
            m.get("base") for m in broker._ex.markets.values()
            if m.get("quote") == "USDT" and m.get("spot") and m.get("active", True)
        }
    except Exception:
        tradeable_bases = set()

    candidates: list[Candidate] = []
    for i, c in enumerate(trending):
        base = c["symbol"]
        if base not in tradeable_bases:
            continue
        mc_rank = c.get("market_cap_rank") or 999
        # speculative high score (rank 100+) = 7, large-cap low score = 5
        if mc_rank is None or mc_rank > 100:
            score = 7.0
        elif mc_rank > 30:
            score = 6.0
        else:
            score = 5.0
        candidates.append(Candidate(
            symbol=f"{base}/USDT",
            base=base,
            kind="NARRATIVE",
            event_type="TRENDING",
            score=score,
            rationale=(
                f"CoinGecko trending #{i+1} ({c['name']}, mc_rank={mc_rank}, "
                f"search_score={c.get('score')}) — narrative momentum"
            ),
        ))
    return candidates


# ─── orchestrator ──────────────────────────────────────────────────────────

def scan_all_candidates(storage, broker) -> list[Candidate]:
    """Run all scanners (news, CoinGecko, spot pairs), merge, dedup by symbol
    (keep highest score)."""
    from .spot_pairs import scan_spot_pairs, pair_signals_as_candidates
    events = scan_news_events(storage)
    narrative = scan_narrative_trending(broker)
    try:
        pair_signals = scan_spot_pairs(storage)
        rel_value = pair_signals_as_candidates(pair_signals)
    except Exception:
        rel_value = []
    by_symbol: dict[str, Candidate] = {}
    for c in events + narrative + rel_value:
        existing = by_symbol.get(c.symbol)
        if existing is None or c.score > existing.score:
            by_symbol[c.symbol] = c
    return sorted(by_symbol.values(), key=lambda c: -c.score)
