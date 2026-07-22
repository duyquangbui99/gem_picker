"""Reddit API via a free script-app OAuth (client-credentials flow) —
mention volume in small-cap/crypto-focused subreddits as a social-momentum
signal. Requires a free Reddit app registered at reddit.com/prefs/apps."""

from pathlib import Path
from time import time

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/r/{subreddit}/search"

_token_cache: dict[str, tuple[str, float]] = {}


@with_retry
def _get_access_token(session: requests.Session, client_id: str, client_secret: str, user_agent: str) -> str:
    cached = _token_cache.get(client_id)
    if cached and cached[1] > time():
        return cached[0]

    resp = session.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        headers={"User-Agent": user_agent},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    token_data = resp.json()
    token = token_data["access_token"]
    _token_cache[client_id] = (token, time() + token_data.get("expires_in", 3600) - 60)
    return token


def count_mentions(
    session: requests.Session,
    client_id: str,
    client_secret: str,
    user_agent: str,
    cache_dir: Path,
    query: str,
    subreddits: list[str],
    ttl_seconds: int = 3600,
) -> int | None:
    """Count of posts mentioning `query` in the trailing week across the
    given subreddits. Returns None on auth/lookup failure (neutral, not a
    red flag), or immediately if no credentials are configured -- verified
    live: with client_id/secret blank, every call was still making a real
    (guaranteed-401) request to Reddit's OAuth endpoint, once per candidate
    per run, for a feature that can never succeed without credentials."""
    if not client_id or not client_secret:
        return None

    def fetch() -> int:
        token = _get_access_token(session, client_id, client_secret, user_agent)
        headers = {"Authorization": f"bearer {token}", "User-Agent": user_agent}
        total = 0
        for sub in subreddits:
            resp = session.get(
                SEARCH_URL.format(subreddit=sub),
                params={"q": query, "restrict_sr": "1", "sort": "new", "t": "week", "limit": 25},
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            total += len(resp.json().get("data", {}).get("children", []))
        return total

    try:
        return cached_json(cache_dir, f"reddit_mentions_{query}", ttl_seconds, fetch)
    except requests.RequestException:
        return None
