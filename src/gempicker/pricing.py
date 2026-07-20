"""Current-price lookups for the manual-trades P&L tracker. Reuses the same
free data sources as the screeners (Finnhub quote for stocks, CoinGecko for
crypto) rather than adding a new dependency."""

from pathlib import Path

import requests

from gempicker.config import Settings
from gempicker.data_sources import coingecko
from gempicker.data_sources import finnhub as finnhub_ds
from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, new_session, with_retry

COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"


@with_retry
def _search_coin_id(session: requests.Session, symbol: str) -> str | None:
    resp = session.get(COINGECKO_SEARCH_URL, params={"query": symbol}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    coins = resp.json().get("coins", [])
    exact = [c for c in coins if c.get("symbol", "").upper() == symbol.upper()]
    candidates = exact or coins
    if not candidates:
        return None
    # prefer the highest-market-cap-rank match (lowest rank number) among candidates
    candidates.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
    return candidates[0]["id"]


def resolve_coingecko_id(session: requests.Session, cache_dir: Path, symbol: str) -> str | None:
    def fetch() -> dict:
        return {"id": _search_coin_id(session, symbol)}

    try:
        return cached_json(cache_dir, f"coingecko_symbol_lookup_{symbol.upper()}", 604_800, fetch).get("id")
    except requests.RequestException:
        return None


def get_current_crypto_price(settings: Settings, symbol: str, coingecko_id: str | None = None) -> float | None:
    session = new_session("gempicker/0.1 (pricing)")
    coin_id = coingecko_id or resolve_coingecko_id(session, settings.cache_dir, symbol)
    if not coin_id:
        return None
    return coingecko.get_simple_price(session, settings.coingecko_api_key, coin_id)


def get_current_stock_price(settings: Settings, symbol: str) -> float | None:
    session = new_session("gempicker/0.1 (pricing)")
    quote = finnhub_ds.get_quote(session, settings.finnhub_api_key, settings.cache_dir, symbol, ttl_seconds=300)
    return quote.get("c") if quote else None


def get_current_price(settings: Settings, asset_class: str, symbol: str, coingecko_id: str | None = None) -> float | None:
    if asset_class == "crypto":
        return get_current_crypto_price(settings, symbol, coingecko_id)
    return get_current_stock_price(settings, symbol)
