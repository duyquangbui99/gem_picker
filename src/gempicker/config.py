from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    finnhub_api_key: str
    fmp_api_key: str
    coingecko_api_key: str
    etherscan_api_key: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_user_agent: str
    sec_edgar_contact_email: str

    gempicker_trade_usd: float = 5.00
    stock_shortlist_size: int = 8
    crypto_shortlist_size: int = 8
    stock_min_market_cap: float = 50_000_000
    stock_max_market_cap: float = 2_000_000_000
    stock_min_avg_dollar_volume: float = 500_000
    crypto_market_cap_floor: float = 10_000_000
    crypto_top_rank_exclusion: int = 100

    fmp_daily_call_budget: int = 250
    # Max Finnhub profile fetches (new + TTL refresh) per screening run, at
    # ~1/sec. Beyond it, expired-but-present profiles are served stale and
    # never-seen symbols are skipped until a future run or `warm-cache`.
    stock_profile_fetch_budget: int = 1000

    data_dir: Path = Field(default=PROJECT_ROOT / "data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "gempicker.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def shortlists_dir(self) -> Path:
        return self.data_dir / "shortlists"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.shortlists_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
