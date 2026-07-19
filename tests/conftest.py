import pytest

from gempicker.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        finnhub_api_key="test",
        fmp_api_key="test",
        coingecko_api_key="test",
        etherscan_api_key="test",
        reddit_client_id="test",
        reddit_client_secret="test",
        reddit_user_agent="test-agent",
        sec_edgar_contact_email="test@example.com",
        data_dir=tmp_path / "data",
        _env_file=None,
    )
