"""SEC EDGAR — official, free, unlimited (fair-use ~10 req/s, requires a
contact email in the User-Agent per SEC's fair-access policy). Ground-truth
fundamentals for any US small-cap; no better free source exists.

Note on insider buying: parsing exact Form 4 buy/sell dollar amounts requires
downloading and parsing individual XML filings, which is out of scope for
v1. As a documented simplification, `recent_form4_count` uses Form 4 FILING
FREQUENCY in the trailing window as a rough insider-activity proxy, not a
precise net-buy dollar figure."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def _session_with_ua(contact_email: str) -> dict:
    return {"User-Agent": f"gem-picker/0.1 ({contact_email})"}


@with_retry
def _get(session: requests.Session, contact_email: str, url: str) -> dict:
    resp = session.get(url, headers=_session_with_ua(contact_email), timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_ticker_cik_map(session: requests.Session, contact_email: str, cache_dir: Path, ttl_seconds: int = 604_800) -> dict[str, str]:
    """{TICKER (upper) -> zero-padded 10-digit CIK}. Cached weekly."""

    def fetch() -> dict:
        return _get(session, contact_email, TICKERS_URL)

    raw = cached_json(cache_dir, "sec_ticker_cik_map", ttl_seconds, fetch)
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}


def get_company_facts(session: requests.Session, contact_email: str, cache_dir: Path, cik: str, ttl_seconds: int = 604_800) -> dict | None:
    """Raw XBRL company facts. Returns None if the company has no facts on
    file (a strong signal of a shell company — used as a hard filter)."""

    def fetch() -> dict:
        return _get(session, contact_email, FACTS_URL.format(cik=cik))

    try:
        return cached_json(cache_dir, f"sec_facts_{cik}", ttl_seconds, fetch)
    except requests.HTTPError:
        return None


def get_revenue_growth(facts: dict | None) -> float | None:
    """YoY revenue growth % from the most recent two annual (10-K) periods,
    or None if the concept isn't present (some small-caps report under a
    different XBRL tag, or are pre-revenue)."""
    if not facts:
        return None
    try:
        units = facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]
    except KeyError:
        return None

    annual = sorted(
        (u for u in units if u.get("form") == "10-K" and u.get("fp") == "FY"),
        key=lambda u: u["end"],
    )
    if len(annual) < 2:
        return None
    prev, latest = annual[-2]["val"], annual[-1]["val"]
    if not prev:
        return None
    return round(100 * (latest - prev) / abs(prev), 2)


def recent_form4_count(session: requests.Session, contact_email: str, cache_dir: Path, cik: str, days: int = 90, ttl_seconds: int = 86_400) -> int | None:
    """Count of Form 4 filings in the trailing window, as a rough insider-
    activity proxy (see module docstring)."""

    def fetch() -> dict:
        return _get(session, contact_email, SUBMISSIONS_URL.format(cik=cik))

    try:
        data = cached_json(cache_dir, f"sec_submissions_{cik}", ttl_seconds, fetch)
    except requests.HTTPError:
        return None

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    return sum(1 for form, date in zip(forms, dates) if form == "4" and date >= cutoff)
