from gempicker.config import Settings
from gempicker.data_sources import coinbase_products, coingecko, defillama, etherscan
from gempicker.data_sources.base import new_session
from gempicker.models import ScoredCandidate
from gempicker.scoring.crypto_scoring import score_crypto_candidate

MIN_VOLUME_MCAP_RATIO = 0.02
ENRICHMENT_POOL_MULTIPLIER = 3  # only fetch expensive per-coin detail for the top N*multiplier pre-screened survivors


def _hard_filters(coin: dict, settings: Settings, tradeable: dict[str, str]) -> str | None:
    """Returns a coinbase_product_id if the coin passes, else None."""
    symbol = (coin.get("symbol") or "").upper()
    market_cap = coin.get("market_cap")
    rank = coin.get("market_cap_rank")
    volume = coin.get("total_volume")

    if symbol not in tradeable:
        return None
    if market_cap is None or market_cap < settings.crypto_market_cap_floor:
        return None
    if rank is None or rank <= settings.crypto_top_rank_exclusion:
        return None
    if not volume or not market_cap or volume / market_cap < MIN_VOLUME_MCAP_RATIO:
        return None
    return tradeable[symbol]


def run(settings: Settings) -> tuple[list[ScoredCandidate], int]:
    """Returns (top scored crypto candidates, full universe size considered)."""
    session = new_session("gempicker/0.1 (crypto screener)")

    universe = coingecko.get_markets_universe(session, settings.coingecko_api_key, settings.cache_dir)
    print(f"[crypto] universe: {len(universe)} coins from CoinGecko", flush=True)
    tradeable = coinbase_products.get_tradeable_symbols(session, settings.cache_dir)
    print(f"[crypto] {len(tradeable)} symbols tradeable on Coinbase", flush=True)

    survivors: list[tuple[dict, str]] = []
    for coin in universe:
        product_id = _hard_filters(coin, settings, tradeable)
        if product_id:
            survivors.append((coin, product_id))
    print(f"[crypto] hard filters (Coinbase-tradeable, cap range, liquidity): {len(survivors)} survivors", flush=True)

    # cheap pre-rank by raw 7d momentum to cap how many get expensive per-coin enrichment calls
    survivors.sort(key=lambda cp: cp[0].get("price_change_percentage_7d_in_currency") or -999, reverse=True)
    enrichment_pool = survivors[: settings.crypto_shortlist_size * ENRICHMENT_POOL_MULTIPLIER]

    tvl_map = defillama.get_symbol_tvl_map(session, settings.cache_dir)

    print(f"[crypto] enriching + scoring top {len(enrichment_pool)} candidates...", flush=True)
    scored: list[ScoredCandidate] = []
    for coin, product_id in enrichment_pool:
        symbol = coin["symbol"].upper()
        tvl_info = tvl_map.get(symbol)

        community_data = None
        try:
            detail = coingecko.get_coin_detail(session, settings.coingecko_api_key, settings.cache_dir, coin["id"])
            community_data = detail.get("community_data")
        except Exception:
            community_data = None  # missing enrichment is neutral, not fatal

        transfer_count = None
        platforms = coin.get("platforms") or {}
        eth_contract = platforms.get("ethereum") if isinstance(platforms, dict) else None
        if eth_contract:
            transfer_count = etherscan.recent_transfer_count(session, settings.etherscan_api_key, settings.cache_dir, eth_contract)

        scored.append(score_crypto_candidate(coin, tvl_info, transfer_count, community_data, product_id))

    scored.sort(key=lambda c: c.score, reverse=True)
    shortlist = scored[: settings.crypto_shortlist_size]
    print(f"[crypto] done: shortlist of {len(shortlist)} (top score {shortlist[0].score if shortlist else 'n/a'})", flush=True)
    return shortlist, len(universe)
