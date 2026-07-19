from datetime import date as date_cls
from pathlib import Path

import typer

from gempicker.config import PROJECT_ROOT, get_settings
from gempicker.db import claim_day, get_connection, mark_error
from gempicker.judge.claude_runner import ClaudeRunError, run_claude_judge
from gempicker.judge.prompt_builder import build_prompt
from gempicker.judge.result_parser import ResultValidationError, parse_and_validate
from gempicker.pipeline import build_daily_shortlist
from gempicker.trade_log import export, log_pick, log_shortlist, recent

app = typer.Typer()


@app.command()
def screen(date: str = typer.Option(None, help="YYYY-MM-DD, defaults to today")) -> None:
    """Run the deterministic screening pipeline only; writes the shortlist JSON. No LLM, no trades."""
    settings = get_settings()
    trade_date = date or date_cls.today().isoformat()
    shortlist, path = build_daily_shortlist(settings, trade_date)
    typer.echo(f"Shortlist written to {path}")
    typer.echo(f"  stocks: {len(shortlist.stocks)} (universe {shortlist.meta.stock_universe_size})")
    typer.echo(f"  crypto: {len(shortlist.crypto)} (universe {shortlist.meta.crypto_universe_size})")
    for c in (shortlist.stocks + shortlist.crypto):
        typer.echo(f"    {c.asset_class:6} {c.symbol:8} score={c.score}")


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
        shortlist, shortlist_path = build_daily_shortlist(settings, trade_date)
        log_shortlist(conn, shortlist)

        result_path = settings.shortlists_dir / f"{trade_date}.result.json"
        if result_path.exists():
            result_path.unlink()

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

    except (ClaudeRunError, ResultValidationError) as e:
        mark_error(conn, trade_date, dry_run, status="judge_error")
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        mark_error(conn, trade_date, dry_run, status="error")
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
