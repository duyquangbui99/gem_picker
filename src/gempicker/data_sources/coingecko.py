"""CoinGecko free Demo API — primary crypto screening source (price, market
cap, volume, ranking). Free Demo tier: 100 calls/min, 10k calls/month."""

from pathlib import Path
from typing import Any

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://api.coingecko.com/api/v3"


@with_retry
def _get(session: requests.Session, api_key: str, path: str, params: dict[str, Any]) -> Any:
    resp = session.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"x-cg-demo-api-key": api_key},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_markets_universe(
    session: requests.Session,
    api_key: str,
    cache_dir: Path,
    max_pages: int = 4,
    per_page: int = 250,
    ttl_seconds: int = 3600,
) -> list[dict]:
    """Top `max_pages * per_page` coins by market cap, with price/volume/rank.
    Cached hourly — market cap ranking doesn't need re-fetching every run."""

    def fetch() -> list[dict]:
        all_coins: list[dict] = []
        for page in range(1, max_pages + 1):
            coins = _get(
                session,
                api_key,
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": page,
                    "sparkline": "false",
                    "price_change_percentage": "7d,30d",
                },
            )
            if not coins:
                break
            all_coins.extend(coins)
        return all_coins

    return cached_json(cache_dir, "coingecko_markets_universe", ttl_seconds, fetch)


def get_coin_detail(session: requests.Session, api_key: str, cache_dir: Path, coin_id: str, ttl_seconds: int = 3600) -> dict:
    """Per-coin detail (community/public-interest stats) for shortlist
    enrichment only — too expensive to call for the whole universe."""

    def fetch() -> dict:
        return _get(
            session,
            api_key,
            f"/coins/{coin_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "false",
                "community_data": "true",
                "developer_data": "false",
            },
        )

    return cached_json(cache_dir, f"coingecko_detail_{coin_id}", ttl_seconds, fetch)
