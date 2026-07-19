from pathlib import Path

from gempicker.config import Settings
from gempicker.data_sources import fmp
from gempicker.db import utcnow_iso
from gempicker.models import ShortlistMeta, ShortlistPayload
from gempicker.screeners import crypto_screener, stock_screener


def build_daily_shortlist(settings: Settings, trade_date: str) -> tuple[ShortlistPayload, Path]:
    stock_candidates, stock_universe_size = stock_screener.run(settings)
    crypto_candidates, crypto_universe_size = crypto_screener.run(settings)

    shortlist = ShortlistPayload(
        date=trade_date,
        generated_at_utc=utcnow_iso(),
        stocks=stock_candidates,
        crypto=crypto_candidates,
        meta=ShortlistMeta(
            stock_universe_size=stock_universe_size,
            crypto_universe_size=crypto_universe_size,
            fmp_calls_used_today=fmp.get_calls_used_today(settings.cache_dir),
        ),
    )

    path = settings.shortlists_dir / f"{trade_date}.json"
    path.write_text(shortlist.model_dump_json(indent=2))
    return shortlist, path
