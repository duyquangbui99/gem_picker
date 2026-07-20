from gempicker.models import ScoredCandidate
from gempicker.scoring.normalize import clamp, min_max_score, momentum_score, weighted_composite

WEIGHTS = {
    "revenue_growth": 0.30,
    "insider_activity": 0.15,
    "price_momentum": 0.25,
    "social_momentum": 0.30,
}

# Dollar volume of open-market insider activity at which the buy/sell balance
# gets full weight; below it the score shrinks toward neutral so a single
# token-sized trade can't max out (or crater) the signal.
INSIDER_FULL_CONVICTION_USD = 250_000


def insider_activity_score(transactions: dict | None) -> float | None:
    """0-100 from net open-market insider buying: all-buys -> 100, all-sells
    -> 0, dampened toward 50 when total dollars traded are small. Zero
    open-market activity is genuinely neutral (50), while None (data
    unavailable) is excluded from the composite entirely."""
    if transactions is None:
        return None
    total = transactions["buy_usd"] + transactions["sell_usd"]
    if total == 0:
        return 50.0
    net_ratio = (transactions["buy_usd"] - transactions["sell_usd"]) / total
    conviction = min(1.0, total / INSIDER_FULL_CONVICTION_USD)
    return clamp(50 + 50 * net_ratio * conviction)


def score_stock_candidate(
    symbol: str,
    profile: dict,
    revenue_growth: float | None,
    insider_transactions: dict | None,
    momentum_30d: float | None,
    stocktwits_snapshot: dict | None,
    reddit_mentions: int | None,
) -> ScoredCandidate:
    flags: list[str] = []

    revenue_score = min_max_score(revenue_growth, -20, 50)
    insider_score = insider_activity_score(insider_transactions)
    price_momentum = momentum_score(momentum_30d)

    # Each social source contributes only when it actually returned data. An
    # unavailable source must not inject a neutral 50 into the average --
    # verified live on the 2026-07-19 run, where Reddit was silently down and
    # 6 of 8 stocks got the identical social score, flattening a 30%-weight
    # signal into a constant.
    social_components: list[float] = []
    if stocktwits_snapshot and stocktwits_snapshot["message_count"] > 0:
        bull_ratio = stocktwits_snapshot["bullish_count"] / max(
            stocktwits_snapshot["bullish_count"] + stocktwits_snapshot["bearish_count"], 1
        )
        volume_score = min_max_score(stocktwits_snapshot["message_count"], 0, 30) or 0
        social_components.append((bull_ratio * 100 + volume_score) / 2)
    if reddit_mentions is not None:
        social_components.append(min_max_score(reddit_mentions, 0, 15))
    social_momentum = round(sum(social_components) / len(social_components), 2) if social_components else None

    score, breakdown = weighted_composite(
        {
            "revenue_growth": revenue_score,
            "insider_activity": insider_score,
            "price_momentum": price_momentum,
            "social_momentum": social_momentum,
        },
        WEIGHTS,
    )

    if revenue_growth is None:
        flags.append("no_revenue_data")
    if insider_transactions is None:
        flags.append("no_insider_data")
    if social_momentum is None:
        flags.append("no_social_data")

    return ScoredCandidate(
        symbol=symbol,
        asset_class="stock",
        name=profile.get("name"),
        market_cap=profile.get("marketCapitalization", 0) * 1_000_000 if profile.get("marketCapitalization") else None,
        score=score,
        score_breakdown=breakdown,
        flags=flags,
        meta={"exchange": profile.get("exchange")},
    )
