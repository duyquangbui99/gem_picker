from gempicker.models import ScoredCandidate
from gempicker.scoring.normalize import min_max_score, momentum_score, weighted_composite

WEIGHTS = {
    "price_momentum": 0.30,
    "tvl_trend": 0.25,
    "onchain_activity": 0.20,
    "social_momentum": 0.25,
}


def score_crypto_candidate(
    coin: dict,
    tvl_info: dict | None,
    transfer_count: int | None,
    community_data: dict | None,
    coinbase_product_id: str,
) -> ScoredCandidate:
    flags: list[str] = []

    price_momentum = momentum_score(coin.get("price_change_percentage_7d_in_currency"))

    tvl_trend = None
    if tvl_info is not None:
        tvl_trend = min_max_score(tvl_info.get("change_7d"), -30, 30)
    # missing TVL just means "not a DeFi protocol token" — not a red flag

    onchain_activity = min_max_score(transfer_count, 0, 500) if transfer_count is not None else None

    # Each community source contributes only when CoinGecko actually returned
    # data for it. The old neutral fallbacks collapsed every low-profile coin
    # to the same constant -- verified live on the 2026-07-19 run, where all 8
    # picks scored exactly 25.0 (reddit activity 0 scored as a genuine zero,
    # missing sentiment injected as 50). Zero reddit activity is treated as
    # missing rather than bearish because the free tier reports 0 both for
    # dead subreddits and for coins it simply has no subreddit mapping for.
    # The old `min_max_score(...) or 50` fallback also turned a genuine
    # rock-bottom sentiment score of 0.0 into a neutral 50 (falsy float).
    social_momentum = None
    if community_data is not None:
        components: list[float] = []
        reddit_activity = (community_data.get("reddit_average_posts_48h") or 0) + (
            community_data.get("reddit_average_comments_48h") or 0
        )
        if reddit_activity > 0:
            components.append(min_max_score(reddit_activity, 0, 20))
        sentiment_score = min_max_score(community_data.get("sentiment_votes_up_percentage"), 30, 90)
        if sentiment_score is not None:
            components.append(sentiment_score)
        if components:
            social_momentum = round(sum(components) / len(components), 2)

    score, breakdown = weighted_composite(
        {
            "price_momentum": price_momentum,
            "tvl_trend": tvl_trend,
            "onchain_activity": onchain_activity,
            "social_momentum": social_momentum,
        },
        WEIGHTS,
    )

    if coin.get("total_volume") and coin.get("market_cap") and coin["total_volume"] / coin["market_cap"] < 0.02:
        flags.append("thin_liquidity")

    return ScoredCandidate(
        symbol=coin["symbol"].upper(),
        asset_class="crypto",
        name=coin.get("name"),
        market_cap=coin.get("market_cap"),
        score=score,
        score_breakdown=breakdown,
        flags=flags,
        meta={
            "coingecko_id": coin["id"],
            "coinbase_product_id": coinbase_product_id,
            "market_cap_rank": coin.get("market_cap_rank"),
        },
    )
