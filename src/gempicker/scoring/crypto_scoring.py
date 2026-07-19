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

    social_momentum = None
    if community_data is not None:
        reddit_posts = community_data.get("reddit_average_posts_48h") or 0
        reddit_comments = community_data.get("reddit_average_comments_48h") or 0
        sentiment_up_pct = community_data.get("sentiment_votes_up_percentage")
        activity_score = min_max_score(reddit_posts + reddit_comments, 0, 20) or 0
        sentiment_score = min_max_score(sentiment_up_pct, 30, 90) or 50
        social_momentum = round((activity_score + sentiment_score) / 2, 2)

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
