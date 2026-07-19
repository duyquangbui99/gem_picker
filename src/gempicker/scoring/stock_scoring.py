from gempicker.models import ScoredCandidate
from gempicker.scoring.normalize import min_max_score, momentum_score, weighted_composite

WEIGHTS = {
    "revenue_growth": 0.30,
    "insider_activity": 0.15,
    "price_momentum": 0.25,
    "social_momentum": 0.30,
}


def score_stock_candidate(
    symbol: str,
    profile: dict,
    revenue_growth: float | None,
    form4_count: int | None,
    momentum_30d: float | None,
    stocktwits_snapshot: dict | None,
    reddit_mentions: int | None,
) -> ScoredCandidate:
    flags: list[str] = []

    revenue_score = min_max_score(revenue_growth, -20, 50)
    insider_score = min_max_score(form4_count, 0, 10)
    price_momentum = momentum_score(momentum_30d)

    social_momentum = None
    if stocktwits_snapshot is not None or reddit_mentions is not None:
        stwits_score = 50.0
        if stocktwits_snapshot and stocktwits_snapshot["message_count"] > 0:
            bull_ratio = stocktwits_snapshot["bullish_count"] / max(
                stocktwits_snapshot["bullish_count"] + stocktwits_snapshot["bearish_count"], 1
            )
            volume_score = min_max_score(stocktwits_snapshot["message_count"], 0, 30) or 0
            stwits_score = (bull_ratio * 100 + volume_score) / 2
        reddit_score = min_max_score(reddit_mentions, 0, 15) if reddit_mentions is not None else 50.0
        social_momentum = round((stwits_score + reddit_score) / 2, 2)

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
