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
import zlib
from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://finnhub.io/api/v1"

# MIC venue codes for genuine US-exchange listings in the /stock/symbol
# universe. Verified live (2026-07-19): 73% of Finnhub's "US" common-stock
# universe (13,487 of 18,447) is OTC ("OOTC"), which the screener excludes
# anyway -- but previously only AFTER spending a rate-limited profile call
# per symbol to learn its exchange. The mic field is already in the free
# universe payload, so OTC can be skipped before any per-symbol spend,
# shrinking the real profile-cache workload to ~5,000 symbols.
MAJOR_US_MICS = {
    "XNAS", "XNGS", "XNCM", "XNMS",  # Nasdaq tiers
    "XNYS", "XASE", "ARCX",          # NYSE, NYSE American, NYSE Arca
    "BATS", "XCBO", "IEXG", "MEMX",  # CBOE BZX/CBOE, IEX, MEMX
}

_PROFILE_TTL_BASE = 604_800     # 7 days
_PROFILE_TTL_JITTER = 259_200   # spread each symbol's expiry across +0-3 days

_DAILY_TTL_BASE = 72_000     # 20 hours
_DAILY_TTL_JITTER = 28_800   # spread across +0-8 hours (range: 20-28h)

# Passing this as ttl_seconds serves any existing cache file without
# refetching -- used when the fetch budget is exhausted (a weeks-old market
# cap still gates a $50M-$2B band fine) and must never trigger a fetch.
STALE_OK_TTL = 10 ** 12


def profile_ttl_seconds(symbol: str) -> int:
    """Per-symbol jittered TTL so a cache warmed in one big backfill doesn't
    expire -- and try to re-fetch -- all on the same day a week later. crc32
    rather than hash(): hash() is salted per process, which would silently
    re-randomize every TTL on every run."""
    return _PROFILE_TTL_BASE + zlib.crc32(symbol.encode()) % _PROFILE_TTL_JITTER


def daily_ttl_seconds(symbol: str) -> int:
    """Like profile_ttl_seconds but for data meant to refresh roughly once a
    day (quote, basic financials) rather than once a week. The default
    ttl_seconds on get_quote/get_basic_financials (1 hour) is a guaranteed
    cache miss for a pipeline that runs once daily -- verified live: the
    liquidity-check phase was re-fetching quote+financials for every
    market-cap survivor on every single run, with no budget cap, making it
    the largest recurring cost in the whole pipeline (~70min/day) even after
    the profile-fetch problem was fixed. 20-28h (not exactly 24h) gives
    slack for the run time shifting slightly day to day without drifting
    into a permanent miss."""
    return _DAILY_TTL_BASE + zlib.crc32(symbol.encode()) % _DAILY_TTL_JITTER


def has_profile_cache(cache_dir: Path, symbol: str) -> bool:
    return (cache_dir / f"finnhub_profile_{symbol}.json").exists()


def is_profile_cache_fresh(cache_dir: Path, symbol: str) -> bool:
    """True when a cached profile exists AND is within its TTL. The
    existence-only check this replaces let TTL-expired files masquerade as
    free cache hits: cached_json would still do a real network refresh for
    them, bypassing the screener's fetch budget entirely."""
    path = cache_dir / f"finnhub_profile_{symbol}.json"
    return path.exists() and (time.time() - path.stat().st_mtime) < profile_ttl_seconds(symbol)

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
