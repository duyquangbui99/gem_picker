"""SEC EDGAR — official, free, unlimited (fair-use ~10 req/s, requires a
contact email in the User-Agent per SEC's fair-access policy). Ground-truth
fundamentals for any US small-cap; no better free source exists."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from xml.etree import ElementTree

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FORM4_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}"


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


# Most filers moved to the ASC 606 concepts years ago and their legacy
# "Revenues" tag silently stops updating, so tag choice must follow data
# freshness rather than a fixed lookup. Verified live on the 2026-07-19 run:
# SKIL's newest "Revenues" data was from 2022, AVTX's from 2018, yet both
# scored 100 on "growth" computed from those stale entries.
REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
)
_ANNUAL_PERIOD_DAYS = (330, 400)
# ~16 months covers a normal 10-K cycle (FY end + up-to-90-day filing window
# + our weekly facts cache) with margin; anything older means the tag went
# stale or the company stopped filing.
MAX_REVENUE_AGE_DAYS = 490
# Below this prior-year base, growth % is noise, not a fundamentals signal
# (a clinical-stage biotech doubling a $5M milestone payment is not "100%
# revenue growth" in any sense the 30% weight is meant to reward).
MIN_PRIOR_ANNUAL_REVENUE = 10_000_000


def _annual_periods(gaap: dict, tag: str) -> list[dict]:
    """10-K/FY entries for one XBRL tag, restricted to genuinely annual
    durations (SPAC/fiscal-transition filings tag multi-month stub periods as
    form=10-K/fp=FY too), deduplicated per period with the latest filing
    winning (restatements), sorted oldest-first by period end."""
    try:
        units = gaap[tag]["units"]["USD"]
    except KeyError:
        return []
    lo, hi = _ANNUAL_PERIOD_DAYS
    by_period: dict[tuple[str, str], dict] = {}
    for u in units:
        if u.get("form") != "10-K" or u.get("fp") != "FY":
            continue
        start, end = u.get("start"), u.get("end")
        if not start or not end:
            continue
        if not lo <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= hi:
            continue
        key = (start, end)
        if key not in by_period or (u.get("filed") or "") > (by_period[key].get("filed") or ""):
            by_period[key] = u
    return sorted(by_period.values(), key=lambda u: u["end"])


def get_revenue_growth(facts: dict | None) -> float | None:
    """YoY revenue growth % from the two most recent consecutive annual
    periods of whichever revenue tag has the freshest data. Returns None
    whenever the data can't support a trustworthy number — no annual periods,
    stale tag, non-consecutive periods, or a tiny prior-year base — so the
    composite treats it as missing evidence instead of a fake signal."""
    if not facts:
        return None
    gaap = facts.get("facts", {}).get("us-gaap", {})
    annual = max(
        (_annual_periods(gaap, tag) for tag in REVENUE_TAGS),
        key=lambda periods: periods[-1]["end"] if periods else "",
    )
    if len(annual) < 2:
        return None
    prev, latest = annual[-2], annual[-1]

    latest_end = date.fromisoformat(latest["end"])
    if (datetime.now(timezone.utc).date() - latest_end).days > MAX_REVENUE_AGE_DAYS:
        return None
    if abs((date.fromisoformat(latest["start"]) - date.fromisoformat(prev["end"])).days) > 45:
        return None  # a missing year in between would overstate YoY growth
    if not prev["val"] or prev["val"] < MIN_PRIOR_ANNUAL_REVENUE:
        return None
    return round(100 * (latest["val"] - prev["val"]) / abs(prev["val"]), 2)


@with_retry
def _get_text(session: requests.Session, contact_email: str, url: str) -> str:
    resp = session.get(url, headers=_session_with_ua(contact_email), timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_form4(xml_text: str) -> dict:
    """Open-market purchase (transaction code P) and sale (code S) dollar
    totals from a Form 4's non-derivative table. Other codes (grants A,
    option exercises M, tax withholding F, ...) aren't conviction signals,
    and a raw filing count is actively misleading — verified live on the
    2026-07-19 run, where MRAM's 19 filings were all sales yet scored a
    perfect insider score under the old frequency proxy."""
    root = ElementTree.fromstring(xml_text)
    summary = {"buy_usd": 0.0, "sell_usd": 0.0, "buy_count": 0, "sell_count": 0}
    for tx in root.iter("nonDerivativeTransaction"):
        code = tx.findtext("transactionCoding/transactionCode")
        shares = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
        price = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        if code == "P":
            summary["buy_usd"] += shares * price
            summary["buy_count"] += 1
        elif code == "S":
            summary["sell_usd"] += shares * price
            summary["sell_count"] += 1
    return summary


# Filings are immutable once accepted, so parsed Form 4 summaries cache
# effectively forever; only the first sighting of each filing costs a request.
_FORM4_CACHE_TTL = 10 * 365 * 86_400


def recent_insider_transactions(
    session: requests.Session,
    contact_email: str,
    cache_dir: Path,
    cik: str,
    days: int = 90,
    max_filings: int = 40,
    ttl_seconds: int = 86_400,
) -> dict | None:
    """Net open-market insider buying vs. selling in the trailing window,
    from the individual Form 4 XMLs. Returns {buy_usd, sell_usd, buy_count,
    sell_count, filings} (all-zero means Form 4s were checked and none were
    open-market trades), or None if the data couldn't be fetched."""

    def fetch() -> dict:
        return _get(session, contact_email, SUBMISSIONS_URL.format(cik=cik))

    try:
        data = cached_json(cache_dir, f"sec_submissions_{cik}", ttl_seconds, fetch)
    except requests.HTTPError:
        return None

    recent = data.get("filings", {}).get("recent", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    filings = [
        (accession, doc)
        for form, filed, accession, doc in zip(
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
        )
        if form == "4" and filed >= cutoff
    ][:max_filings]

    totals = {"buy_usd": 0.0, "sell_usd": 0.0, "buy_count": 0, "sell_count": 0, "filings": len(filings)}
    parsed = 0
    for accession, doc in filings:
        acc_no_dashes = accession.replace("-", "")

        def fetch_form4(acc_no_dashes: str = acc_no_dashes, doc: str = doc) -> dict:
            # primaryDocument may carry an "xslF345X.../" render prefix; the
            # bare filename in the same archive folder is the raw XML.
            url = FORM4_URL.format(cik_int=int(cik), accession=acc_no_dashes, doc=doc.split("/")[-1])
            summary = _parse_form4(_get_text(session, contact_email, url))
            sleep(0.12)  # SEC fair-access: stay well under 10 req/s on a cold cache
            return summary

        try:
            summary = cached_json(cache_dir, f"sec_form4_{acc_no_dashes}", _FORM4_CACHE_TTL, fetch_form4)
        except (requests.HTTPError, ElementTree.ParseError):
            continue
        parsed += 1
        for key in ("buy_usd", "sell_usd", "buy_count", "sell_count"):
            totals[key] += summary[key]

    if filings and parsed == 0:
        return None  # filings exist but none were readable: unknown, not neutral
    return totals
