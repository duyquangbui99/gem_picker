from gempicker.screeners.crypto_screener import _hard_filters


def _coin(**overrides):
    base = {
        "symbol": "abc",
        "market_cap": 50_000_000,
        "market_cap_rank": 150,
        "total_volume": 2_000_000,  # 4% of market cap, above the 2% liquidity floor
    }
    base.update(overrides)
    return base


def test_passes_all_filters(settings):
    tradeable = {"ABC": "ABC-USD"}
    assert _hard_filters(_coin(), settings, tradeable) == "ABC-USD"


def test_rejects_not_tradeable_on_coinbase(settings):
    tradeable = {"XYZ": "XYZ-USD"}
    assert _hard_filters(_coin(), settings, tradeable) is None


def test_rejects_below_market_cap_floor(settings):
    tradeable = {"ABC": "ABC-USD"}
    assert _hard_filters(_coin(market_cap=5_000_000), settings, tradeable) is None


def test_rejects_inside_top_100(settings):
    tradeable = {"ABC": "ABC-USD"}
    assert _hard_filters(_coin(market_cap_rank=50), settings, tradeable) is None


def test_rejects_thin_volume(settings):
    tradeable = {"ABC": "ABC-USD"}
    # 0.5% of market cap, below the 2% liquidity floor
    assert _hard_filters(_coin(total_volume=250_000), settings, tradeable) is None


def test_rejects_missing_market_cap(settings):
    tradeable = {"ABC": "ABC-USD"}
    assert _hard_filters(_coin(market_cap=None), settings, tradeable) is None
