"""Finnhub free tier (60 calls/min) — US stock universe, company profile
(market cap), quotes, and basic financials.

Note: the "Financials As Reported" endpoint AND the `/stock/candle` OHLCV
endpoint are both premium-only on the free tier as of mid-2026 (verified
live: candle returns 403 "You don't have access to this resource."). Raw
financials come from SEC EDGAR instead (see sec_edgar.py). Volume and price
momentum come from `/stock/metric` (free, "Basic Financials"), which
conveniently already provides average trading volume and pre-computed
price-return percentages — no manual candle math needed."""

import threading
import time
from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://finnhub.io/api/v1"

# Free tier is rate-limited to 60 calls/min; nothing else was throttling
# calls, which risks bursts of 429s during a cold-cache run that screens
# hundreds of symbols. A simple minimum-interval throttle keeps us under
# the limit without needing a full token-bucket implementation.
_MIN_INTERVAL_SECONDS = 1.05
_last_call_lock = threading.Lock()
_last_call_time = 0.0


def _throttle() -> None:
    global _last_call_time
    with _last_call_lock:
        wait = _last_call_time + _MIN_INTERVAL_SECONDS - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.time()


@with_retry
def _get(session: requests.Session, api_key: str, path: str, params: dict) -> dict | list:
    _throttle()
    resp = session.get(f"{BASE_URL}{path}", params={**params, "token": api_key}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_us_symbols(session: requests.Session, api_key: str, cache_dir: Path, ttl_seconds: int = 604_800) -> list[dict]:
    """Full US-listed common-stock universe. Cached weekly."""

    def fetch() -> list[dict]:
        symbols = _get(session, api_key, "/stock/symbol", {"exchange": "US"})
        return [s for s in symbols if s.get("type") == "Common Stock"]

    return cached_json(cache_dir, "finnhub_us_symbols", ttl_seconds, fetch)


def get_company_profile(session: requests.Session, api_key: str, cache_dir: Path, symbol: str, ttl_seconds: int = 604_800) -> dict | None:
    """Market cap + basic profile. Cached weekly per symbol — market cap
    doesn't need re-fetching every run."""

    def fetch() -> dict:
        return _get(session, api_key, "/stock/profile2", {"symbol": symbol})

    try:
        data = cached_json(cache_dir, f"finnhub_profile_{symbol}", ttl_seconds, fetch)
        return data or None
    except requests.HTTPError:
        return None


def get_quote(session: requests.Session, api_key: str, cache_dir: Path, symbol: str, ttl_seconds: int = 3600) -> dict | None:
    """Current price snapshot (free tier). No volume field — pair with
    get_basic_financials() for that."""

    def fetch() -> dict:
        return _get(session, api_key, "/quote", {"symbol": symbol})

    try:
        data = cached_json(cache_dir, f"finnhub_quote_{symbol}", ttl_seconds, fetch)
        return data if data and data.get("c") else None
    except requests.HTTPError:
        return None


def get_basic_financials(session: requests.Session, api_key: str, cache_dir: Path, symbol: str, ttl_seconds: int = 3600) -> dict | None:
    """Free-tier "Basic Financials" (`/stock/metric`) — includes average
    trading volume (in millions of shares) and pre-computed price-return
    percentages, replacing what would otherwise need OHLCV candle math."""

    def fetch() -> dict:
        return _get(session, api_key, "/stock/metric", {"symbol": symbol, "metric": "all"})

    try:
        data = cached_json(cache_dir, f"finnhub_metric_{symbol}", ttl_seconds, fetch)
        return data.get("metric") or None
    except requests.HTTPError:
        return None


def avg_dollar_volume(metric: dict | None, current_price: float | None) -> float | None:
    """10-day average trading volume (shares, reported in millions) x price."""
    if not metric or not current_price:
        return None
    avg_shares_millions = metric.get("10DayAverageTradingVolume")
    if avg_shares_millions is None:
        return None
    return avg_shares_millions * 1_000_000 * current_price


def momentum_pct(metric: dict | None) -> float | None:
    """~13-week price return %, already computed by Finnhub — a reasonable
    stand-in for the 30-day momentum window candles would have given."""
    if not metric:
        return None
    return metric.get("13WeekPriceReturnDaily")
