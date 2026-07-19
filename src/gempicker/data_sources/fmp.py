"""Financial Modeling Prep free tier — only 250 requests/day, so this is
applied ONLY to the already-shortlisted top candidates for supplementary
company profile data, never for broad universe screening. A simple daily
counter file enforces the budget so a bug can't silently burn the whole
day's quota.

Note: FMP's legacy `/api/v3/*` endpoints (including the `stock-screener`
and `key-metrics` endpoints this module originally targeted) return 403 for
any key created after August 2025 -- "no longer supported". Their new
`/stable/*` API also paywalls `key-metrics` and `stock-screener` on the free
plan (402 "Restricted Endpoint"). `/stable/profile` is free and verified
working (returns price, marketCap, volume/averageVolume, beta, dividend,
52-week range, industry, exchange) -- used here as the enrichment source
instead."""

import json
from datetime import date
from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, with_retry

BASE_URL = "https://financialmodelingprep.com/stable"


class BudgetExceeded(Exception):
    pass


def _budget_path(cache_dir: Path) -> Path:
    return cache_dir / "fmp_budget.json"


def get_calls_used_today(cache_dir: Path) -> int:
    path = _budget_path(cache_dir)
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    return data["count"] if data.get("date") == date.today().isoformat() else 0


def _increment_budget(cache_dir: Path) -> int:
    path = _budget_path(cache_dir)
    today = date.today().isoformat()
    count = get_calls_used_today(cache_dir) + 1
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"date": today, "count": count}))
    return count


@with_retry
def _get(session: requests.Session, api_key: str, path: str, params: dict) -> list | dict:
    resp = session.get(f"{BASE_URL}{path}", params={**params, "apikey": api_key}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_company_profile(session: requests.Session, api_key: str, cache_dir: Path, symbol: str, daily_budget: int) -> dict | None:
    if get_calls_used_today(cache_dir) >= daily_budget:
        raise BudgetExceeded(f"FMP daily budget of {daily_budget} calls exhausted")

    try:
        data = _get(session, api_key, "/profile", {"symbol": symbol})
        _increment_budget(cache_dir)
        return data[0] if data else None
    except requests.HTTPError:
        _increment_budget(cache_dir)  # failed calls still count against quota
        return None
