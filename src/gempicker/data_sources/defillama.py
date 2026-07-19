"""DeFiLlama public API (no auth, effectively no rate limit) — TVL and
protocol-revenue trend, a real "fundamentals" signal for DeFi tokens."""

from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

PROTOCOLS_URL = "https://api.llama.fi/protocols"


@with_retry
def _fetch_protocols(session: requests.Session) -> list[dict]:
    resp = session.get(PROTOCOLS_URL, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_symbol_tvl_map(session: requests.Session, cache_dir: Path, ttl_seconds: int = 3600) -> dict[str, dict]:
    """Returns {TOKEN_SYMBOL (upper) -> {tvl, change_1d, change_7d, mcap}}.
    Only DeFi-category tokens have an entry here; screeners should treat a
    missing entry as "not applicable" (neutral score), not zero/bad."""

    def fetch() -> list[dict]:
        return _fetch_protocols(session)

    protocols = cached_json(cache_dir, "defillama_protocols", ttl_seconds, fetch)

    by_symbol: dict[str, dict] = {}
    for p in protocols:
        symbol = p.get("symbol")
        if not symbol or symbol.upper() == "-":
            continue
        by_symbol[symbol.upper()] = {
            "tvl": p.get("tvl"),
            "change_1d": p.get("change_1d"),
            "change_7d": p.get("change_7d"),
            "mcap": p.get("mcap"),
        }
    return by_symbol
