from pathlib import Path

from gempicker.config import Settings
from gempicker.data_sources import fmp, sec_edgar, stocktwits
from gempicker.data_sources import finnhub as finnhub_ds
from gempicker.data_sources import reddit as reddit_ds
from gempicker.data_sources.base import new_session
from gempicker.models import ScoredCandidate
from gempicker.scoring.stock_scoring import score_stock_candidate

# Finnhub's free tier is 60 calls/min; a cold cache across the whole US
# small-cap universe (thousands of tickers) would take well over an hour on
# day one. Cap fresh profile lookups per run and let the weekly-TTL cache
# gradually warm up over the first ~1-2 weeks; already-cached symbols are
# always evaluated fully regardless of this cap.
MAX_NEW_PROFILE_LOOKUPS_PER_RUN = 400

STOCK_SUBREDDITS = ["pennystocks", "stocks", "wallstreetbets"]


def _is_profile_cached(cache_dir: Path, symbol: str) -> bool:
    path = cache_dir / f"finnhub_profile_{symbol}.json"
    return path.exists()


def run(settings: Settings) -> tuple[list[ScoredCandidate], int]:
    """Returns (top scored stock candidates, full universe size considered)."""
    session = new_session("gempicker/0.1 (stock screener)")

    universe = finnhub_ds.get_us_symbols(session, settings.finnhub_api_key, settings.cache_dir)
    symbols = [s["symbol"] for s in universe if s.get("symbol")]

    cached_first = sorted(symbols, key=lambda sym: not _is_profile_cached(settings.cache_dir, sym))

    new_lookups_used = 0
    market_cap_survivors: list[str] = []
    for symbol in cached_first:
        was_cached = _is_profile_cached(settings.cache_dir, symbol)
        if not was_cached:
            if new_lookups_used >= MAX_NEW_PROFILE_LOOKUPS_PER_RUN:
                continue
            new_lookups_used += 1

        profile = finnhub_ds.get_company_profile(session, settings.finnhub_api_key, settings.cache_dir, symbol)
        if not profile:
            continue
        mcap = (profile.get("marketCapitalization") or 0) * 1_000_000
        if settings.stock_min_market_cap <= mcap <= settings.stock_max_market_cap:
            market_cap_survivors.append(symbol)

    cik_map = sec_edgar.get_ticker_cik_map(session, settings.sec_edgar_contact_email, settings.cache_dir)

    hard_filter_survivors: list[str] = []
    for symbol in market_cap_survivors:
        quote = finnhub_ds.get_quote(session, settings.finnhub_api_key, settings.cache_dir, symbol)
        metric = finnhub_ds.get_basic_financials(session, settings.finnhub_api_key, settings.cache_dir, symbol)
        avg_vol = finnhub_ds.avg_dollar_volume(metric, quote.get("c") if quote else None)
        if not avg_vol or avg_vol < settings.stock_min_avg_dollar_volume:
            continue

        cik = cik_map.get(symbol)
        if not cik:
            continue
        facts = sec_edgar.get_company_facts(session, settings.sec_edgar_contact_email, settings.cache_dir, cik)
        if not facts:
            continue  # no financials on file at all is a shell-company red flag

        hard_filter_survivors.append(symbol)

    fmp_calls_used = fmp.get_calls_used_today(settings.cache_dir)
    scored: list[ScoredCandidate] = []
    for symbol in hard_filter_survivors:
        profile = finnhub_ds.get_company_profile(session, settings.finnhub_api_key, settings.cache_dir, symbol)
        metric = finnhub_ds.get_basic_financials(session, settings.finnhub_api_key, settings.cache_dir, symbol)
        cik = cik_map[symbol]
        facts = sec_edgar.get_company_facts(session, settings.sec_edgar_contact_email, settings.cache_dir, cik)

        revenue_growth = sec_edgar.get_revenue_growth(facts)
        form4_count = sec_edgar.recent_form4_count(session, settings.sec_edgar_contact_email, settings.cache_dir, cik)
        momentum = finnhub_ds.momentum_pct(metric)
        stwits = stocktwits.get_sentiment_snapshot(session, settings.cache_dir, symbol)
        reddit_mentions = reddit_ds.count_mentions(
            session,
            settings.reddit_client_id,
            settings.reddit_client_secret,
            settings.reddit_user_agent,
            settings.cache_dir,
            symbol,
            STOCK_SUBREDDITS,
        )

        scored.append(
            score_stock_candidate(symbol, profile, revenue_growth, form4_count, momentum, stwits, reddit_mentions)
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    shortlist = scored[: settings.stock_shortlist_size]

    # FMP enrichment only for the final shortlist, budget-capped
    for candidate in shortlist:
        if fmp_calls_used >= settings.fmp_daily_call_budget:
            candidate.flags.append("fmp_budget_exhausted")
            continue
        try:
            fmp_profile = fmp.get_company_profile(session, settings.fmp_api_key, settings.cache_dir, candidate.symbol, settings.fmp_daily_call_budget)
            fmp_calls_used = fmp.get_calls_used_today(settings.cache_dir)
            if fmp_profile:
                candidate.meta["fmp_profile"] = fmp_profile
        except fmp.BudgetExceeded:
            candidate.flags.append("fmp_budget_exhausted")

    return shortlist, len(symbols)
