from gempicker.scoring.crypto_scoring import score_crypto_candidate
from gempicker.scoring.normalize import min_max_score, momentum_score, weighted_composite
from gempicker.scoring.stock_scoring import insider_activity_score, score_stock_candidate


def test_min_max_score_clamps_and_scales():
    assert min_max_score(None, 0, 100) is None
    assert min_max_score(-50, 0, 100) == 0
    assert min_max_score(150, 0, 100) == 100
    assert min_max_score(50, 0, 100) == 50


def test_momentum_score_penalizes_overextension():
    stagnant = momentum_score(0)
    sweet_spot = momentum_score(20)
    blown_off_top = momentum_score(140)
    negative = momentum_score(-30)

    assert sweet_spot > stagnant
    assert sweet_spot > blown_off_top  # a 140% pump scores worse than a healthy 20% gain
    assert negative < stagnant


def test_weighted_composite_renormalizes_over_missing_signals():
    # only two of three signals present; weights renormalize over those two,
    # then the whole thing is dampened by sqrt(available_weight/total_weight)
    # to reflect the incomplete evidence base (see the sparse-signal test below).
    score, breakdown = weighted_composite(
        {"a": 100.0, "b": 0.0, "c": None},
        {"a": 0.5, "b": 0.3, "c": 0.2},
    )
    assert "c" not in breakdown
    # raw = 100 * (0.5/0.8) = 62.5; confidence = sqrt(0.8/1.0) = 0.8944; 62.5*0.8944 = 55.9
    assert score == 55.9


def test_weighted_composite_all_missing_returns_zero():
    score, breakdown = weighted_composite({"a": None}, {"a": 1.0})
    assert score == 0.0
    assert breakdown == {}


def test_weighted_composite_single_sparse_signal_cannot_reach_a_perfect_score():
    """Regression test for a real bug found in production: a stock (FTH)
    with only one of four signals available (insider_activity, from a raw
    Form-4 filing count) scored a "perfect" 100 because the old
    implementation let that one signal absorb 100% of the renormalized
    weight. A single noisy metric on an otherwise-unknown candidate must not
    be able to outscore a candidate with a full, moderate evidence base."""
    weights = {"revenue_growth": 0.30, "insider_activity": 0.15, "price_momentum": 0.25, "social_momentum": 0.30}

    sparse_score, _ = weighted_composite(
        {"revenue_growth": None, "insider_activity": 100.0, "price_momentum": None, "social_momentum": None},
        weights,
    )
    well_rounded_score, _ = weighted_composite(
        {"revenue_growth": 65.0, "insider_activity": 50.0, "price_momentum": 60.0, "social_momentum": 55.0},
        weights,
    )

    assert sparse_score < 50.0  # nowhere near "perfect" despite its one signal being maxed out
    assert sparse_score < well_rounded_score


def test_score_crypto_candidate_flags_thin_liquidity():
    coin = {
        "id": "some-coin",
        "symbol": "SOME",
        "name": "Some Coin",
        "market_cap": 50_000_000,
        "total_volume": 200_000,  # 0.4% of market cap, below the 2% floor
        "price_change_percentage_7d_in_currency": 10,
    }
    candidate = score_crypto_candidate(coin, None, None, None, "SOME-USD")
    assert "thin_liquidity" in candidate.flags
    assert candidate.meta["coinbase_product_id"] == "SOME-USD"


def test_score_crypto_candidate_empty_community_data_is_not_a_constant_25():
    """Regression test for a real bug found in production: CoinGecko's free
    tier returned no reddit activity and no sentiment for any candidate, and
    the old (0 + neutral-50)/2 fallback gave all 8 crypto picks the identical
    social score of 25.0. Empty community data must mean "no signal"."""
    coin = {
        "id": "coin-b",
        "symbol": "BBB",
        "name": "Coin B",
        "market_cap": 100_000_000,
        "total_volume": 5_000_000,
        "price_change_percentage_7d_in_currency": 15,
    }
    empty_community = {"reddit_average_posts_48h": 0, "reddit_average_comments_48h": 0, "sentiment_votes_up_percentage": None}
    candidate = score_crypto_candidate(coin, None, None, empty_community, "BBB-USD")
    assert "social_momentum" not in candidate.score_breakdown

    sentiment_only = score_crypto_candidate(
        coin, None, None, {**empty_community, "sentiment_votes_up_percentage": 90}, "BBB-USD"
    )
    assert sentiment_only.score_breakdown["social_momentum"] == 100.0

    # a genuinely bad sentiment must score low, not fall back to neutral
    # (the old `min_max_score(...) or 50` turned a 0.0 score into 50)
    bearish = score_crypto_candidate(
        coin, None, None, {**empty_community, "sentiment_votes_up_percentage": 20}, "BBB-USD"
    )
    assert bearish.score_breakdown["social_momentum"] == 0.0


def test_score_crypto_candidate_missing_tvl_is_neutral_not_penalized():
    coin = {
        "id": "coin-a",
        "symbol": "AAA",
        "name": "Coin A",
        "market_cap": 100_000_000,
        "total_volume": 5_000_000,
        "price_change_percentage_7d_in_currency": 15,
    }
    with_tvl = score_crypto_candidate(
        coin, {"change_7d": 20}, None, None, "AAA-USD"
    )
    without_tvl = score_crypto_candidate(coin, None, None, None, "AAA-USD")
    # both should produce a valid score; tvl_trend key only present when data exists
    assert "tvl_trend" in with_tvl.score_breakdown
    assert "tvl_trend" not in without_tvl.score_breakdown


BUYS_ONLY = {"buy_usd": 500_000.0, "sell_usd": 0.0, "buy_count": 3, "sell_count": 0}
SELLS_ONLY = {"buy_usd": 0.0, "sell_usd": 500_000.0, "buy_count": 0, "sell_count": 3}
NO_TRADES = {"buy_usd": 0.0, "sell_usd": 0.0, "buy_count": 0, "sell_count": 0}


def test_score_stock_candidate_flags_missing_revenue_data():
    profile = {"name": "Test Co", "marketCapitalization": 500, "exchange": "NASDAQ"}
    candidate = score_stock_candidate("TEST", profile, None, BUYS_ONLY, 10, None, None)
    assert "no_revenue_data" in candidate.flags
    assert candidate.market_cap == 500_000_000


def test_score_stock_candidate_social_uses_bull_ratio():
    profile = {"name": "Test Co", "marketCapitalization": 500, "exchange": "NASDAQ"}
    bullish = score_stock_candidate(
        "TEST", profile, 10, NO_TRADES, 10,
        {"message_count": 10, "bullish_count": 9, "bearish_count": 1}, 5,
    )
    bearish = score_stock_candidate(
        "TEST", profile, 10, NO_TRADES, 10,
        {"message_count": 10, "bullish_count": 1, "bearish_count": 9}, 5,
    )
    assert bullish.score_breakdown["social_momentum"] > bearish.score_breakdown["social_momentum"]


def test_insider_score_rewards_buying_and_punishes_selling():
    """Regression test for a real bug found in production: the old signal
    scored raw Form 4 filing frequency, so MRAM's 19 all-sale filings in 90
    days produced a perfect 100 insider score. Direction must matter."""
    assert insider_activity_score(BUYS_ONLY) == 100.0
    assert insider_activity_score(SELLS_ONLY) == 0.0
    assert insider_activity_score(NO_TRADES) == 50.0  # checked, nothing traded: neutral
    assert insider_activity_score(None) is None  # data unavailable: excluded


def test_insider_score_small_dollar_trades_shrink_toward_neutral():
    tiny_buy = {"buy_usd": 25_000.0, "sell_usd": 0.0, "buy_count": 1, "sell_count": 0}
    assert 50.0 < insider_activity_score(tiny_buy) < 60.0  # not a conviction signal


def test_score_stock_candidate_missing_social_sources_excluded_not_neutral():
    """Regression test for a real bug found in production: with Reddit
    unconfigured, its None result was averaged in as a neutral 50, so 6 of 8
    shortlisted stocks got the identical social score at 30% weight."""
    profile = {"name": "Test Co", "marketCapitalization": 500, "exchange": "NASDAQ"}
    no_social = score_stock_candidate("TEST", profile, 10, NO_TRADES, 10, None, None)
    assert "social_momentum" not in no_social.score_breakdown
    assert "no_social_data" in no_social.flags

    reddit_only = score_stock_candidate("TEST", profile, 10, NO_TRADES, 10, None, 15)
    assert reddit_only.score_breakdown["social_momentum"] == 100.0  # sole source used alone
