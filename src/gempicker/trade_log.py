"""Thin facade over db.py for the pick-recording concerns, kept as its own
module to match the pipeline's conceptual stages (screen -> judge -> log)."""

import sqlite3
from pathlib import Path

from gempicker.db import export_csv, get_recent_picks, record_judge_result, record_shortlist
from gempicker.models import JudgeResult, ShortlistPayload


def log_shortlist(conn: sqlite3.Connection, shortlist: ShortlistPayload) -> None:
    record_shortlist(conn, shortlist)


def log_pick(
    conn: sqlite3.Connection,
    trade_date: str,
    dry_run: bool,
    result: JudgeResult,
    claude_session_id: str | None,
    claude_cost_usd: float | None,
) -> None:
    record_judge_result(conn, trade_date, dry_run, result, claude_session_id, claude_cost_usd)


def recent(conn: sqlite3.Connection, limit: int = 14):
    return get_recent_picks(conn, limit)


def export(conn: sqlite3.Connection, out_path: Path) -> None:
    export_csv(conn, out_path)
