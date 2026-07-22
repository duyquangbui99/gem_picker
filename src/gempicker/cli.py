import traceback
from datetime import date as date_cls
from pathlib import Path

import typer

from gempicker.config import PROJECT_ROOT, get_settings
from gempicker.data_sources import finnhub
from gempicker.data_sources.base import new_session
from gempicker.db import claim_day, get_connection, mark_error
from gempicker.judge.claude_runner import ClaudeRunError, run_claude_judge
from gempicker.judge.prompt_builder import build_prompt
from gempicker.judge.result_parser import ResultValidationError, parse_and_validate
from gempicker.lock import PipelineLockedError, pipeline_lock
from gempicker.pipeline import build_daily_shortlist
from gempicker.trade_log import export, log_pick, log_shortlist, recent

app = typer.Typer()


@app.command()
def screen(date: str = typer.Option(None, help="YYYY-MM-DD, defaults to today")) -> None:
    """Run the deterministic screening pipeline only; writes the shortlist JSON. No LLM, no trades."""
    settings = get_settings()
    trade_date = date or date_cls.today().isoformat()
    try:
        with pipeline_lock(settings.data_dir):
            shortlist, path = build_daily_shortlist(settings, trade_date)
    except PipelineLockedError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Shortlist written to {path}")
    typer.echo(f"  stocks: {len(shortlist.stocks)} (universe {shortlist.meta.stock_universe_size})")
    typer.echo(f"  crypto: {len(shortlist.crypto)} (universe {shortlist.meta.crypto_universe_size})")
    for c in (shortlist.stocks + shortlist.crypto):
        typer.echo(f"    {c.asset_class:6} {c.symbol:8} score={c.score}")


@app.command(name="warm-cache")
def warm_cache(limit: int = typer.Option(0, help="Max profiles to fetch this session (0 = no limit)")) -> None:
    """Backfill/refresh the Finnhub profile cache for every major-exchange
    universe symbol, at the free-tier rate limit (~57/min). Resumable:
    interrupt anytime, already-cached symbols are skipped next invocation.
    Holds the pipeline lock, so no screen/run can execute concurrently."""
    settings = get_settings()
    session = new_session("gempicker/0.1 (cache warmer)")
    try:
        with pipeline_lock(settings.data_dir):
            universe = finnhub.get_us_symbols(session, settings.finnhub_api_key, settings.cache_dir)
            symbols = [s["symbol"] for s in universe if s.get("symbol") and s.get("mic") in finnhub.MAJOR_US_MICS]
            todo = [sym for sym in symbols if not finnhub.is_profile_cache_fresh(settings.cache_dir, sym)]
            if limit:
                todo = todo[:limit]
            typer.echo(
                f"{len(symbols)} major-exchange symbols in universe; {len(todo)} need fetch/refresh "
                f"(ETA ~{len(todo) * 1.05 / 60:.0f} min at the free-tier rate limit)"
            )
            fetched = failed = 0
            try:
                for i, sym in enumerate(todo, start=1):
                    profile = finnhub.get_company_profile(
                        session, settings.finnhub_api_key, settings.cache_dir, sym,
                        ttl_seconds=finnhub.profile_ttl_seconds(sym),
                    )
                    # None with no cache file written = HTTP error (empty
                    # profiles for dead tickers DO get cached, and count as done)
                    if profile is None and not finnhub.has_profile_cache(settings.cache_dir, sym):
                        failed += 1
                    else:
                        fetched += 1
                    if i % 100 == 0:
                        typer.echo(f"...{i}/{len(todo)} ({fetched} ok, {failed} failed)")
            except KeyboardInterrupt:
                typer.echo(f"\ninterrupted at {fetched + failed}/{len(todo)} -- progress is saved, just re-run to resume")
                raise typer.Exit(code=130)
            typer.echo(f"warm-cache done: {fetched} fetched, {failed} failed, {len(symbols) - len(todo)} were already fresh")
    except PipelineLockedError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def run(
    date: str = typer.Option(None, help="YYYY-MM-DD, defaults to today"),
    live: bool = typer.Option(False, help="Execute a real trade via MCP. Default is dry-run."),
) -> None:
    """Full daily pipeline: screen -> judge (headless Claude) -> record. Idempotent per (date, live)."""
    settings = get_settings()
    trade_date = date or date_cls.today().isoformat()
    dry_run = not live
    conn = get_connection(settings.db_path)

    if not claim_day(conn, trade_date, dry_run):
        typer.echo(f"{trade_date} (dry_run={dry_run}) already claimed — exiting.")
        raise typer.Exit(code=0)

    try:
        with pipeline_lock(settings.data_dir):
            shortlist, shortlist_path = build_daily_shortlist(settings, trade_date)
            log_shortlist(conn, shortlist)

            result_path = settings.shortlists_dir / f"{trade_date}.result.json"
            if result_path.exists():
                result_path.unlink()

            typer.echo("Shortlist built. Invoking Claude for judgment...")
            prompt = build_prompt(shortlist_path, result_path, trade_date, settings.gempicker_trade_usd, dry_run)
            cli_meta = run_claude_judge(prompt, PROJECT_ROOT)
            result = parse_and_validate(result_path, trade_date, dry_run)

        log_pick(
            conn,
            trade_date,
            dry_run,
            result,
            claude_session_id=cli_meta.get("session_id"),
            claude_cost_usd=cli_meta.get("total_cost_usd"),
        )
        typer.echo(f"Picked {result.asset_class} {result.symbol} (risk={result.risk_tier}, score={result.score})")
        if not dry_run:
            typer.echo(f"Order: {result.order}")

    except PipelineLockedError as e:
        mark_error(conn, trade_date, dry_run, status="lock_contention", error_message=str(e))
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    except (ClaudeRunError, ResultValidationError) as e:
        mark_error(conn, trade_date, dry_run, status="judge_error", error_message=str(e))
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        mark_error(conn, trade_date, dry_run, status="error", error_message=traceback.format_exc())
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def report(last: int = typer.Option(14, help="Number of most recent live picks to show")) -> None:
    """Show recent live picks from the trade log."""
    settings = get_settings()
    conn = get_connection(settings.db_path)
    rows = recent(conn, last)
    if not rows:
        typer.echo("No live picks recorded yet.")
        return
    for row in rows:
        filled_usd = row["filled_usd"] or 0
        typer.echo(
            f"{row['trade_date']}  {row['asset_class'] or '?':6}  {row['symbol'] or '?':8}  "
            f"risk={row['risk_tier'] or '?':6}  status={row['order_status']:14}  "
            f"${filled_usd:.2f} @ {row['filled_price']}"
        )


@app.command(name="export-csv")
def export_csv_cmd(out: str = typer.Option("data/picks_export.csv")) -> None:
    """Dump the live pick history to CSV for taxes/spreadsheets."""
    settings = get_settings()
    conn = get_connection(settings.db_path)
    out_path = Path(out)
    export(conn, out_path)
    typer.echo(f"Exported to {out_path}")


if __name__ == "__main__":
    app()
