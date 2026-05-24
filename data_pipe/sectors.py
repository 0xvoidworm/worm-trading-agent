"""Sector classifier untuk diversification check.

Manual mapping symbol → sector (top ~100 by mc rank yang relevan untuk
Binance Spot trading). Update periodik kalau ada coin baru aktif di scanner.

Sectors:
  STORE_OF_VALUE  : BTC
  L1              : ETH SOL AVAX SUI APT TON ADA DOT ATOM ALGO
  L1_AI           : NEAR (AI-integrated L1)
  L2_ETH          : ARB OP BASE STRK ZK MNT MANTLE
  L2_BTC          : STX SBTC
  CEX             : BNB CRO OKB GT KCS
  PAYMENT         : XRP LTC XLM BCH
  PRIVACY         : ZEC XMR DASH
  AI_INFRA        : TAO FET AGIX OCEAN RNDR WLD GRT
  LIQUID_STAKING  : LDO RPL JTO ETHFI EZETH
  DEX             : UNI CRV JUP CAKE SUSHI BAL
  LENDING         : AAVE COMP MORPHO RDNT
  ORACLE          : LINK PYTH BAND API3
  STABLECOIN_DAO  : MKR FXS FRAX
  RWA             : ONDO CFG POLYX TRU MAPLE
  MEME            : DOGE SHIB PEPE WIF BONK FLOKI POPCAT BRETT MEW BOME
  NFT_MEME        : PENGU MILK
  GAMING          : IMX RON GALA AXS SAND MANA PRIME GMT
  EXCHANGE_TOKEN  : DYDX HYPE
  L1_DEFI         : INJ KAVA OSMO
  DEPIN           : RNDR FIL HNT IOTX NOIA
  PRIVACY_DEFI    : ZRO
"""
from __future__ import annotations


# Manual mapping. Source: CoinGecko categories + market knowledge.
# Format: BASE_SYMBOL → SECTOR_LABEL
SECTOR_MAP: dict[str, str] = {
    # Store of value
    "BTC": "STORE_OF_VALUE",

    # Layer 1
    "ETH": "L1",
    "SOL": "L1",
    "AVAX": "L1",
    "SUI": "L1",
    "APT": "L1",
    "TON": "L1",
    "ADA": "L1",
    "DOT": "L1",
    "ATOM": "L1",
    "ALGO": "L1",
    "TRX": "L1",
    "BCH": "L1_FORK",
    "ETC": "L1_FORK",
    "NEAR": "L1_AI",
    "ICP": "L1",
    "SEI": "L1",
    "TIA": "MODULAR",

    # Layer 2
    "ARB": "L2_ETH",
    "OP": "L2_ETH",
    "MNT": "L2_ETH",
    "STRK": "L2_ETH",
    "MANTA": "L2_ETH",
    "ZK": "L2_ETH",
    "METIS": "L2_ETH",
    "POL": "L2_ETH",  # ex-MATIC
    "STX": "L2_BTC",

    # CEX tokens
    "BNB": "CEX",
    "CRO": "CEX",
    "OKB": "CEX",
    "GT": "CEX",
    "KCS": "CEX",

    # Payment
    "XRP": "PAYMENT",
    "LTC": "PAYMENT",
    "XLM": "PAYMENT",

    # Privacy
    "ZEC": "PRIVACY",
    "XMR": "PRIVACY",
    "DASH": "PRIVACY",

    # AI infrastructure
    "TAO": "AI_INFRA",
    "FET": "AI_INFRA",
    "AGIX": "AI_INFRA",
    "OCEAN": "AI_INFRA",
    "RNDR": "AI_DEPIN",
    "WLD": "AI_IDENTITY",
    "GRT": "DATA_INFRA",
    "AKT": "AI_INFRA",
    "AI": "AI_INFRA",
    "VIRTUAL": "AI_INFRA",

    # Liquid staking
    "LDO": "LIQUID_STAKING",
    "RPL": "LIQUID_STAKING",
    "JTO": "LIQUID_STAKING_SOL",
    "ETHFI": "LIQUID_STAKING",
    "EZETH": "LIQUID_STAKING",
    "EIGEN": "RESTAKING",

    # DEX
    "UNI": "DEX",
    "CRV": "DEX",
    "JUP": "DEX",
    "CAKE": "DEX",
    "SUSHI": "DEX",
    "BAL": "DEX",
    "RAY": "DEX",

    # Lending
    "AAVE": "LENDING",
    "COMP": "LENDING",
    "MORPHO": "LENDING",
    "RDNT": "LENDING",

    # Oracle
    "LINK": "ORACLE",
    "PYTH": "ORACLE",
    "BAND": "ORACLE",
    "API3": "ORACLE",

    # Stablecoin / DAO
    "MKR": "STABLECOIN_DAO",
    "FXS": "STABLECOIN_DAO",
    "ENA": "STABLECOIN_NEW",

    # RWA
    "ONDO": "RWA",
    "CFG": "RWA",
    "POLYX": "RWA",
    "TRU": "RWA",

    # Meme
    "DOGE": "MEME",
    "SHIB": "MEME",
    "PEPE": "MEME",
    "WIF": "MEME",
    "BONK": "MEME",
    "FLOKI": "MEME",
    "POPCAT": "MEME",
    "BRETT": "MEME",
    "MEW": "MEME",
    "BOME": "MEME",
    "TRUMP": "MEME",
    "TURBO": "MEME",
    "MEME": "MEME",

    # NFT / Meme NFT
    "PENGU": "NFT_MEME",
    "MILK": "NFT_MEME",
    "BLUR": "NFT_INFRA",
    "APE": "NFT_INFRA",

    # Gaming
    "IMX": "GAMING_INFRA",
    "RON": "GAMING",
    "GALA": "GAMING",
    "AXS": "GAMING",
    "SAND": "GAMING",
    "MANA": "GAMING",
    "PRIME": "GAMING",
    "GMT": "GAMING",
    "PIXEL": "GAMING",
    "ACE": "GAMING",

    # Exchange / perp DEX
    "DYDX": "PERP_DEX",
    "HYPE": "PERP_DEX",
    "GMX": "PERP_DEX",

    # L1 DeFi
    "INJ": "L1_DEFI",
    "KAVA": "L1_DEFI",
    "OSMO": "L1_DEFI",

    # DePIN
    "FIL": "DEPIN",
    "HNT": "DEPIN",
    "IOTX": "DEPIN",
    "NOIA": "DEPIN",
    "GRASS": "DEPIN",
}


def get_sector(symbol: str) -> str:
    """Return sector label for symbol. Strips USDT/ suffix if present.

    Returns "UNKNOWN" if not in map (caller can decide policy: treat as
    own sector vs skip diversification check).
    """
    if not symbol:
        return "UNKNOWN"
    base = symbol.upper().split("/")[0]
    return SECTOR_MAP.get(base, "UNKNOWN")


def same_sector(sym_a: str, sym_b: str) -> bool:
    """True if both symbols map to the same KNOWN sector.

    Two UNKNOWN sectors return False (we don't assert relationship) — caller
    can decide conservative policy.
    """
    sa = get_sector(sym_a)
    sb = get_sector(sym_b)
    if sa == "UNKNOWN" or sb == "UNKNOWN":
        return False
    return sa == sb


def diversification_status(
    candidate_symbol: str,
    open_position_symbols: list[str],
) -> dict:
    """Check if candidate would concentrate same-sector with open positions.

    Returns:
        {
            "candidate_sector": str,
            "concentration_risk": bool,
            "matching_open": [symbols sharing sector],
        }
    """
    cand_sec = get_sector(candidate_symbol)
    matching = [
        s for s in open_position_symbols
        if same_sector(s, candidate_symbol) and s != candidate_symbol
    ]
    return {
        "candidate_sector": cand_sec,
        "concentration_risk": len(matching) > 0,
        "matching_open": matching,
    }
