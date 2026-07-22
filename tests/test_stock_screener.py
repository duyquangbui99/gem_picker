"""Unit tests for the profile-cache freshness/budget machinery.

Regression context (found live, 2026-07-19): the screener decided "cached =
free" by file existence alone, but cached profiles carry a TTL -- an expired
file still triggered a real rate-limited refresh inside cached_json without
counting against the per-run fetch budget. Separately, uncached symbols were
scanned in universe order, so the daily budget always burned down the same
alphabetical prefix and everything beyond it was systematically invisible.
"""

import os
import time

from gempicker.data_sources import finnhub


def test_profile_ttl_is_stable_and_jittered():
    ttl = finnhub.profile_ttl_seconds("AAPL")
    assert ttl == finnhub.profile_ttl_seconds("AAPL")  # crc32: stable across runs, unlike salted hash()
    assert finnhub._PROFILE_TTL_BASE <= ttl < finnhub._PROFILE_TTL_BASE + finnhub._PROFILE_TTL_JITTER
    ttls = {finnhub.profile_ttl_seconds(f"SYM{i}") for i in range(30)}
    assert len(ttls) > 1  # expiries actually spread instead of clustering on one day


def test_daily_ttl_is_stable_jittered_and_shorter_than_profile_ttl():
    """Regression test for a real bug found in production: get_quote/
    get_basic_financials defaulted to a 1hr TTL, a guaranteed cache miss for
    every candidate on a pipeline that runs once a day -- making the
    liquidity-check phase the largest recurring cost in the pipeline, every
    single run, forever (unlike the one-time profile-fetch cost)."""
    ttl = finnhub.daily_ttl_seconds("AAPL")
    assert ttl == finnhub.daily_ttl_seconds("AAPL")
    assert finnhub._DAILY_TTL_BASE <= ttl < finnhub._DAILY_TTL_BASE + finnhub._DAILY_TTL_JITTER
    assert finnhub._DAILY_TTL_BASE + finnhub._DAILY_TTL_JITTER < finnhub._PROFILE_TTL_BASE
    ttls = {finnhub.daily_ttl_seconds(f"SYM{i}") for i in range(30)}
    assert len(ttls) > 1


def test_profile_cache_freshness_respects_ttl(tmp_path):
    symbol = "TEST"
    path = tmp_path / f"finnhub_profile_{symbol}.json"

    assert not finnhub.is_profile_cache_fresh(tmp_path, symbol)
    assert not finnhub.has_profile_cache(tmp_path, symbol)

    path.write_text("{}")
    assert finnhub.is_profile_cache_fresh(tmp_path, symbol)
    assert finnhub.has_profile_cache(tmp_path, symbol)

    expired = time.time() - (finnhub.profile_ttl_seconds(symbol) + 60)
    os.utime(path, (expired, expired))
    assert not finnhub.is_profile_cache_fresh(tmp_path, symbol)  # expired file is NOT a free cache hit
    assert finnhub.has_profile_cache(tmp_path, symbol)  # ...but is still servable as stale


def test_stale_ok_ttl_serves_expired_cache_without_fetching(tmp_path):
    symbol = "TEST"
    path = tmp_path / f"finnhub_profile_{symbol}.json"
    path.write_text('{"marketCapitalization": 500}')
    expired = time.time() - (finnhub.profile_ttl_seconds(symbol) + 60)
    os.utime(path, (expired, expired))

    # session=None: any attempted network call would raise AttributeError
    profile = finnhub.get_company_profile(None, "key", tmp_path, symbol, ttl_seconds=finnhub.STALE_OK_TTL)
    assert profile == {"marketCapitalization": 500}
