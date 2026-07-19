"""Coinbase Exchange public product list (no auth needed). Used as a HARD
FILTER on the crypto universe: CoinGecko's universe is much broader than
what's actually tradeable on Coinbase, and a pick that can't be bought is
useless."""

from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://api.exchange.coinbase.com/products"


@with_retry
def _fetch_products(session: requests.Session) -> list[dict]:
    resp = session.get(BASE_URL, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_tradeable_symbols(session: requests.Session, cache_dir: Path, ttl_seconds: int = 86_400) -> dict[str, str]:
    """Returns {BASE_SYMBOL (upper) -> product_id} for USD-quoted, online,
    non-restricted products. Cached daily."""

    def fetch() -> list[dict]:
        return _fetch_products(session)

    products = cached_json(cache_dir, "coinbase_products", ttl_seconds, fetch)

    tradeable: dict[str, str] = {}
    for p in products:
        if (
            p.get("quote_currency") == "USD"
            and p.get("status") == "online"
            and not p.get("trading_disabled", False)
            and not p.get("limit_only", False)
        ):
            tradeable[p["base_currency"].upper()] = p["id"]
    return tradeable
