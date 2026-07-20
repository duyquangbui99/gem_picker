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

# Verified live: Finnhub's "US" symbol universe includes OTC-listed and
# foreign-exchange ADRs (e.g. exchange="OTC MARKETS", "ASX - ALL MARKETS")
# that consistently fail the later SEC EDGAR filings check anyway (no
# CIK / no financials on file) -- but only after burning 2 more rate-limited
# Finnhub calls each to check their liquidity first. Excluding them here,
# using the `exchange` field already returned by the profile call (no extra
# API cost), both improves pick quality (OTC is exactly the thin/manipulable
# end of the market a "gem" screen shouldn't want) and cuts the expensive
# phase's candidate count substantially.
#
# An allowlist of known-legitimate exchanges is used rather than a denylist
# of known-bad ones (e.g. "OTC MARKETS") -- a denylist would need to
# anticipate every foreign/OTC venue Finnhub might return (already seen at
# least one non-OTC example, "ASX - ALL MARKETS", that isn't a real US
# listing), whereas the small set of legitimate major US venues is easy to
# enumerate confidently. Includes the small-cap tiers (NASDAQ Capital
# Market, NYSE American) since that's exactly where real small-cap gems
# are likely to be listed.
_MAJOR_EXCHANGE_KEYWORDS = ("NASDAQ", "NEW YORK STOCK EXCHANGE", "NYSE", "CBOE", "IEX")


def _is_major_exchange(exchange: str | None) -> bool:
    if not exchange:
        return False
    exchange_upper = exchange.upper()
    return any(kw in exchange_upper for kw in _MAJOR_EXCHANGE_KEYWORDS)


def _is_profile_cached(cache_dir: Path, symbol: str) -> bool:
    path = cache_dir / f"finnhub_profile_{symbol}.json"
    return path.exists()


# Verified live: Finnhub and FMP disagreed on one small-cap's market cap by
# ~19x (Finnhub: $667M, FMP: $34.7M -- actually below the configured floor),
# for a thinly-covered stock where free-tier data is more likely to be stale
# or wrong. Only Finnhub's figure is used for the hard $50M-$2B eligibility
# filter (FMP is budget-capped to 250/day, so it can't gate the initial
# screen), so a material disagreement here means that filter decision might
# be wrong in either direction. Flagging rather than silently excluding --
# not certain enough which source is right to discard a candidate outright,
# but this needs to be visible to whoever reviews the pick, human or Claude.
MARKET_CAP_DISAGREEMENT_RATIO = 2.0


def _flag_market_cap_disagreement(candidate: ScoredCandidate, fmp_profile: dict, settings: Settings) -> None:
    fmp_market_cap = fmp_profile.get("marketCap")
    if not fmp_market_cap or not candidate.market_cap:
        return
    ratio = fmp_market_cap / candidate.market_cap
    if ratio < 1 / MARKET_CAP_DISAGREEMENT_RATIO or ratio > MARKET_CAP_DISAGREEMENT_RATIO:
        candidate.flags.append(
            f"market_cap_data_mismatch: Finnhub reports ${candidate.market_cap/1e6:.0f}M, "
            f"FMP reports ${fmp_market_cap/1e6:.0f}M -- eligibility for the "
            f"${settings.stock_min_market_cap/1e6:.0f}M-${settings.stock_max_market_cap/1e6:.0f}M range is uncertain"
        )


def run(settings: Settings) -> tuple[list[ScoredCandidate], int]:
    """Returns (top scored stock candidates, full universe size considered)."""
    session = new_session("gempicker/0.1 (stock screener)")

    universe = finnhub_ds.get_us_symbols(session, settings.finnhub_api_key, settings.cache_dir)
    symbols = [s["symbol"] for s in universe if s.get("symbol")]
    print(f"[stocks] universe: {len(symbols)} US common-stock symbols", flush=True)

    cached_first = sorted(symbols, key=lambda sym: not _is_profile_cached(settings.cache_dir, sym))
    already_cached = sum(1 for sym in symbols if _is_profile_cached(settings.cache_dir, sym))
    print(
        f"[stocks] market-cap pre-filter: {already_cached} symbols cached from previous runs, "
        f"up to {MAX_NEW_PROFILE_LOOKUPS_PER_RUN} new lookups this run (rate-limited ~1/sec on Finnhub's free tier)",
        flush=True,
    )

    new_lookups_used = 0
    excluded_non_major = 0
    market_cap_survivors: list[str] = []
    for i, symbol in enumerate(cached_first, start=1):
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
            if _is_major_exchange(profile.get("exchange")):
                market_cap_survivors.append(symbol)
            else:
                excluded_non_major += 1

        if i % 100 == 0:
            print(
                f"[stocks] ...{i}/{len(cached_first)} symbols checked, "
                f"{new_lookups_used}/{MAX_NEW_PROFILE_LOOKUPS_PER_RUN} new API calls used, "
                f"{len(market_cap_survivors)} in market-cap range on a major exchange so far "
                f"({excluded_non_major} excluded as OTC/foreign-listed)",
                flush=True,
            )

    print(f"[stocks] market-cap pre-filter done: {len(market_cap_survivors)} candidates in ${settings.stock_min_market_cap/1e6:.0f}M-${settings.stock_max_market_cap/1e6:.0f}M range on a major exchange ({excluded_non_major} OTC/foreign-listed excluded)", flush=True)

    cik_map = sec_edgar.get_ticker_cik_map(session, settings.sec_edgar_contact_email, settings.cache_dir)

    print(f"[stocks] checking liquidity + SEC EDGAR financials for {len(market_cap_survivors)} candidates...", flush=True)
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

    print(f"[stocks] liquidity + SEC filter done: {len(hard_filter_survivors)} candidates survived, scoring now...", flush=True)

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
                _flag_market_cap_disagreement(candidate, fmp_profile, settings)
        except fmp.BudgetExceeded:
            candidate.flags.append("fmp_budget_exhausted")

    print(f"[stocks] done: shortlist of {len(shortlist)} (top score {shortlist[0].score if shortlist else 'n/a'})", flush=True)
    return shortlist, len(symbols)
