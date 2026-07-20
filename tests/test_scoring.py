from gempicker.scoring.crypto_scoring import score_crypto_candidate
from gempicker.scoring.normalize import min_max_score, momentum_score, weighted_composite
from gempicker.scoring.stock_scoring import score_stock_candidate


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


def test_score_stock_candidate_flags_missing_revenue_data():
    profile = {"name": "Test Co", "marketCapitalization": 500, "exchange": "NASDAQ"}
    candidate = score_stock_candidate("TEST", profile, None, 2, 10, None, None)
    assert "no_revenue_data" in candidate.flags
    assert candidate.market_cap == 500_000_000


def test_score_stock_candidate_social_uses_bull_ratio():
    profile = {"name": "Test Co", "marketCapitalization": 500, "exchange": "NASDAQ"}
    bullish = score_stock_candidate(
        "TEST", profile, 10, 1, 10,
        {"message_count": 10, "bullish_count": 9, "bearish_count": 1}, 5,
    )
    bearish = score_stock_candidate(
        "TEST", profile, 10, 1, 10,
        {"message_count": 10, "bullish_count": 1, "bearish_count": 9}, 5,
    )
    assert bullish.score_breakdown["social_momentum"] > bearish.score_breakdown["social_momentum"]
