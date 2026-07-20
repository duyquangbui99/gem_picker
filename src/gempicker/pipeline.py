import traceback
from pathlib import Path

from gempicker.config import Settings
from gempicker.data_sources import fmp
from gempicker.db import utcnow_iso
from gempicker.models import ScoredCandidate, ShortlistMeta, ShortlistPayload
from gempicker.screeners import crypto_screener, stock_screener


def _run_screener_isolated(label: str, fn) -> tuple[list[ScoredCandidate], int]:
    """Runs one screener and isolates failures to it. Without this, a crash
    in one screener (e.g. a CoinGecko rate-limit error) would propagate and
    throw away whatever the OTHER screener already produced -- wasteful when
    the stock screener alone can legitimately take 15+ minutes of
    rate-limited work. A failed side just contributes an empty shortlist
    instead of taking the whole run down with it."""
    try:
        return fn()
    except Exception:
        print(f"[{label}] ERROR: screener crashed, continuing with an empty {label} shortlist:", flush=True)
        print(traceback.format_exc(), flush=True)
        return [], 0


def build_daily_shortlist(settings: Settings, trade_date: str) -> tuple[ShortlistPayload, Path]:
    print(f"=== Building shortlist for {trade_date} ===", flush=True)

    stock_candidates, stock_universe_size = _run_screener_isolated("stocks", lambda: stock_screener.run(settings))
    crypto_candidates, crypto_universe_size = _run_screener_isolated("crypto", lambda: crypto_screener.run(settings))

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
