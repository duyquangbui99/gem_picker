"""StockTwits public symbol stream (no API key needed) — retail sentiment
and mention volume, a real signal specifically for small-caps."""

from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://api.stocktwits.com/api/2/streams/symbol"


@with_retry
def _fetch(session: requests.Session, symbol: str) -> dict:
    resp = session.get(f"{BASE_URL}/{symbol}.json", timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_sentiment_snapshot(session: requests.Session, cache_dir: Path, symbol: str, ttl_seconds: int = 3600) -> dict | None:
    """Returns {message_count, bullish_count, bearish_count} from the most
    recent ~30 messages, or None if the symbol has no StockTwits activity."""

    def fetch() -> dict:
        return _fetch(session, symbol)

    try:
        data = cached_json(cache_dir, f"stocktwits_{symbol}", ttl_seconds, fetch)
    except requests.HTTPError:
        return None

    messages = data.get("messages", [])
    if not messages:
        return None

    bullish = sum(1 for m in messages if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
    bearish = sum(1 for m in messages if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")

    return {"message_count": len(messages), "bullish_count": bullish, "bearish_count": bearish}
