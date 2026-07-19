from typing import Any, Literal

from pydantic import BaseModel, Field

AssetClass = Literal["stock", "crypto"]
RiskTier = Literal["low", "medium", "high"]


class ScoredCandidate(BaseModel):
    symbol: str
    asset_class: AssetClass
    name: str | None = None
    market_cap: float | None = None
    score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    # asset-specific identifiers/extras: coingecko_id, coinbase_product_id, cik, etc.
    meta: dict[str, Any] = Field(default_factory=dict)


class ShortlistMeta(BaseModel):
    stock_universe_size: int
    crypto_universe_size: int
    fmp_calls_used_today: int


class ShortlistPayload(BaseModel):
    date: str
    generated_at_utc: str
    stocks: list[ScoredCandidate]
    crypto: list[ScoredCandidate]
    meta: ShortlistMeta


class OrderResult(BaseModel):
    order_id: str | None = None
    mcp_tool_called: str | None = None
    filled_price: float | None = None
    filled_qty: float | None = None
    filled_usd: float | None = None
    raw_response: dict[str, Any] | None = None


class JudgeResult(BaseModel):
    date: str
    dry_run: bool
    asset_class: AssetClass
    symbol: str
    score: float
    risk_tier: RiskTier
    rationale: str
    red_flags: list[str] = Field(default_factory=list)
    order: OrderResult | None = None
