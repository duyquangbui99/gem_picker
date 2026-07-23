import random
import time
from datetime import date

from gempicker.config import Settings
from gempicker.data_sources import fmp, sec_edgar, stocktwits
from gempicker.data_sources import finnhub as finnhub_ds
from gempicker.data_sources import reddit as reddit_ds
from gempicker.data_sources.base import new_session
from gempicker.models import ScoredCandidate
from gempicker.scoring.stock_scoring import score_stock_candidate

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
    # mic pre-filter: drop OTC/foreign venues using data already in the free
    # universe payload, BEFORE any per-symbol profile spend -- 73% of the raw
    # "US" universe is OTC that the exchange allowlist below would reject
    # anyway, previously at the cost of one rate-limited call each.
    listed = [s for s in universe if s.get("symbol") and s.get("mic") in finnhub_ds.MAJOR_US_MICS]
    symbols = [s["symbol"] for s in listed]
    print(
        f"[stocks] universe: {len(universe)} US common-stock symbols; "
        f"{len(symbols)} on major exchanges ({len(universe) - len(symbols)} OTC/other skipped free via mic)",
        flush=True,
    )

    fresh_set = {sym for sym in symbols if finnhub_ds.is_profile_cache_fresh(settings.cache_dir, sym)}
    needs_fetch = [sym for sym in symbols if sym not in fresh_set]
    # Shuffle with a per-day deterministic seed: when the fetch budget can't
    # cover everything, each day samples the not-yet-fresh remainder
    # uniformly instead of always burning the budget on the same
    # alphabetical prefix of the universe (which left everything after the
    # daily pointer systematically invisible during warm-up).
    random.Random(date.today().isoformat()).shuffle(needs_fetch)
    scan_order = sorted(fresh_set) + needs_fetch
    budget = settings.stock_profile_fetch_budget
    print(
        f"[stocks] market-cap pre-filter: {len(fresh_set)} profiles fresh in cache, "
        f"{len(needs_fetch)} need fetch/refresh (budget {budget}/run at ~1/sec; beyond it, "
        "stale cache is served if present, never-seen symbols wait for a future run or warm-cache)",
        flush=True,
    )

    fetches_used = 0
    served_stale = 0
    skipped_unseen = 0
    excluded_non_major = 0
    market_cap_survivors: list[str] = []
    for i, symbol in enumerate(scan_order, start=1):
        if symbol in fresh_set:
            profile = finnhub_ds.get_company_profile(
                session, settings.finnhub_api_key, settings.cache_dir, symbol,
                ttl_seconds=finnhub_ds.profile_ttl_seconds(symbol),
            )
        elif fetches_used < budget:
            fetches_used += 1
            profile = finnhub_ds.get_company_profile(
                session, settings.finnhub_api_key, settings.cache_dir, symbol,
                ttl_seconds=finnhub_ds.profile_ttl_seconds(symbol),
            )
        elif finnhub_ds.has_profile_cache(settings.cache_dir, symbol):
            served_stale += 1
            profile = finnhub_ds.get_company_profile(
                session, settings.finnhub_api_key, settings.cache_dir, symbol,
                ttl_seconds=finnhub_ds.STALE_OK_TTL,
            )
        else:
            skipped_unseen += 1
            continue

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
                f"[stocks] ...{i}/{len(scan_order)} symbols checked, "
                f"{fetches_used}/{budget} fetches used ({served_stale} served stale, {skipped_unseen} unseen-skipped), "
                f"{len(market_cap_survivors)} in market-cap range so far",
                flush=True,
            )

    print(
        f"[stocks] market-cap pre-filter done: {len(market_cap_survivors)} candidates in "
        f"${settings.stock_min_market_cap/1e6:.0f}M-${settings.stock_max_market_cap/1e6:.0f}M range "
        f"({fetches_used} fetched, {served_stale} served from stale cache, {skipped_unseen} unseen symbols deferred, "
        f"{excluded_non_major} excluded by exchange allowlist)",
        flush=True,
    )
    if skipped_unseen:
        print(
            f"[stocks] note: {skipped_unseen} symbols have never been profiled and were deferred by the "
            "fetch budget -- run `gempicker warm-cache` once to eliminate this backlog",
            flush=True,
        )

    cik_map = sec_edgar.get_ticker_cik_map(session, settings.sec_edgar_contact_email, settings.cache_dir)

    print(f"[stocks] checking liquidity + SEC EDGAR financials for {len(market_cap_survivors)} candidates...", flush=True)
    hard_filter_survivors: list[str] = []
    for symbol in market_cap_survivors:
        # daily_ttl_seconds, not the 1hr default: this pipeline runs once a
        # day, so a 1hr TTL was a guaranteed cache miss for every candidate
        # on every run -- this liquidity check was the single largest
        # recurring cost in the pipeline as a result.
        quote = finnhub_ds.get_quote(
            session, settings.finnhub_api_key, settings.cache_dir, symbol, ttl_seconds=finnhub_ds.daily_ttl_seconds(symbol)
        )
        metric = finnhub_ds.get_basic_financials(
            session, settings.finnhub_api_key, settings.cache_dir, symbol, ttl_seconds=finnhub_ds.daily_ttl_seconds(symbol)
        )
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
    industry_by_symbol: dict[str, str] = {}
    reddit_unavailable = 0
    scoring_failures: list[str] = []

    # Diagnostic step-level logging (added while chasing a run that appeared
    # to hang for 2+ hours during this exact loop, with no prior visibility
    # into which candidate or which of the 6 per-candidate calls it was stuck
    # on): every sub-step prints before it runs, so whatever line printed
    # last when a hang is observed tells us exactly where it stalled.
    def _log_step(i: int, total: int, symbol: str, step: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] [stocks] [{i}/{total}] {symbol}: {step}", flush=True)

    scoring_started = time.monotonic()
    total_to_score = len(hard_filter_survivors)
    for i, symbol in enumerate(hard_filter_survivors, start=1):
        try:
            # STALE_OK_TTL: this profile was already read (possibly served
            # stale) in the pre-filter; re-reading with the normal TTL here
            # could trigger a surprise refetch outside the budget accounting.
            _log_step(i, total_to_score, symbol, "profile")
            profile = finnhub_ds.get_company_profile(
                session, settings.finnhub_api_key, settings.cache_dir, symbol, ttl_seconds=finnhub_ds.STALE_OK_TTL
            )
            industry_by_symbol[symbol] = (profile or {}).get("finnhubIndustry") or "Unknown"
            _log_step(i, total_to_score, symbol, "basic financials")
            metric = finnhub_ds.get_basic_financials(
                session, settings.finnhub_api_key, settings.cache_dir, symbol, ttl_seconds=finnhub_ds.daily_ttl_seconds(symbol)
            )
            cik = cik_map[symbol]
            _log_step(i, total_to_score, symbol, "SEC company facts")
            facts = sec_edgar.get_company_facts(session, settings.sec_edgar_contact_email, settings.cache_dir, cik)

            revenue_growth = sec_edgar.get_revenue_growth(facts)
            _log_step(i, total_to_score, symbol, "SEC insider transactions (Form 4s)")
            insider_transactions = sec_edgar.recent_insider_transactions(session, settings.sec_edgar_contact_email, settings.cache_dir, cik)
            momentum = finnhub_ds.momentum_pct(metric)
            _log_step(i, total_to_score, symbol, "StockTwits")
            stwits = stocktwits.get_sentiment_snapshot(session, settings.cache_dir, symbol)
            _log_step(i, total_to_score, symbol, "Reddit")
            reddit_mentions = reddit_ds.count_mentions(
                session,
                settings.reddit_client_id,
                settings.reddit_client_secret,
                settings.reddit_user_agent,
                settings.cache_dir,
                symbol,
                STOCK_SUBREDDITS,
            )
            if reddit_mentions is None:
                reddit_unavailable += 1

            scored.append(
                score_stock_candidate(symbol, profile, revenue_growth, insider_transactions, momentum, stwits, reddit_mentions)
            )
        except Exception as e:
            # One symbol's data pull failing (rate limit, transient I/O,
            # malformed API response, ...) must not discard every candidate
            # already successfully scored -- verified live: an unrelated
            # local-cache read hiccup on a single symbol previously escaped
            # this loop entirely and zeroed out the whole day's stock
            # shortlist (caught only by pipeline.py's screener-level
            # isolation, discarding all 1640 survivors' work, not just one).
            scoring_failures.append(f"{symbol}: {e}")

        if i % 50 == 0 or i == total_to_score:
            elapsed = time.monotonic() - scoring_started
            rate = i / elapsed if elapsed > 0 else 0
            eta_seconds = (total_to_score - i) / rate if rate > 0 else float("inf")
            print(
                f"[stocks] ...scored {i}/{total_to_score} "
                f"({elapsed:.0f}s elapsed, {rate:.2f}/s, ~{eta_seconds / 60:.1f} min remaining)",
                flush=True,
            )

    if scoring_failures:
        print(
            f"[stocks] warning: {len(scoring_failures)}/{len(hard_filter_survivors)} candidates failed to score "
            f"and were skipped: {', '.join(scoring_failures[:5])}"
            + (f" (+{len(scoring_failures) - 5} more)" if len(scoring_failures) > 5 else ""),
            flush=True,
        )

    if reddit_unavailable:
        print(
            f"[stocks] warning: Reddit mentions unavailable for {reddit_unavailable}/{len(hard_filter_survivors)} "
            "candidates (check REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET); social score is StockTwits-only for those",
            flush=True,
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    shortlist = scored[: settings.stock_shortlist_size]

    # Diagnostic only (not written to the shortlist JSON): shows where every
    # scored candidate landed by industry, not just the 8 that made the cut --
    # added because the score-driven top 8 skewed toward biotech/consumer
    # names on 2026-07-21 and it wasn't obvious from the shortlist alone
    # whether that was a hard filter excluding other sectors (it isn't; there
    # is no sector filter anywhere in this pipeline) or just how the score
    # distribution fell out that day.
    if scored:
        industry_scores: dict[str, list[float]] = {}
        for c in scored:
            industry_scores.setdefault(industry_by_symbol.get(c.symbol, "Unknown"), []).append(c.score)
        cutoff = shortlist[-1].score
        print(
            f"[stocks] industry breakdown of all {len(scored)} scored candidates "
            f"(shortlist cutoff score: {cutoff}, '*' = at least one candidate cleared it):",
            flush=True,
        )
        for industry, industry_candidate_scores in sorted(
            industry_scores.items(), key=lambda kv: max(kv[1]), reverse=True
        ):
            best = max(industry_candidate_scores)
            avg = sum(industry_candidate_scores) / len(industry_candidate_scores)
            marker = "*" if best >= cutoff else " "
            print(
                f"    [{marker}] {industry:40} n={len(industry_candidate_scores):4}  "
                f"best={best:6.2f}  avg={avg:6.2f}",
                flush=True,
            )

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
