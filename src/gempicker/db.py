import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gempicker.models import JudgeResult, ShortlistPayload

SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT UNIQUE NOT NULL,
    generated_at_utc TEXT NOT NULL,
    shortlist_json TEXT NOT NULL,
    stock_universe_size INTEGER NOT NULL,
    crypto_universe_size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    asset_class TEXT,
    symbol TEXT,
    name TEXT,
    score REAL,
    score_breakdown_json TEXT,
    risk_tier TEXT,
    rationale TEXT,
    red_flags_json TEXT,
    order_status TEXT NOT NULL,
    order_id TEXT,
    mcp_tool_called TEXT,
    filled_price REAL,
    filled_qty REAL,
    filled_usd REAL,
    raw_order_response_json TEXT,
    claude_session_id TEXT,
    claude_cost_usd REAL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(trade_date, dry_run)
);
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(created_at_utc: str, hours: int) -> bool:
    created = datetime.fromisoformat(created_at_utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(hours=hours)


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def claim_day(conn: sqlite3.Connection, trade_date: str, dry_run: bool) -> bool:
    """Atomically claim a (trade_date, dry_run) slot. Returns False if already
    claimed by a live/completed run today; a 'started' row older than 2 hours
    is treated as an abandoned/crashed run and is re-claimable."""
    row = conn.execute(
        "SELECT order_status, created_at_utc FROM picks WHERE trade_date=? AND dry_run=?",
        (trade_date, int(dry_run)),
    ).fetchone()

    if row is not None:
        abandoned = row["order_status"] == "started" and _is_stale(row["created_at_utc"], hours=2)
        if not abandoned:
            return False

    now = utcnow_iso()
    conn.execute(
        """INSERT INTO picks (trade_date, dry_run, order_status, created_at_utc, updated_at_utc)
           VALUES (?, ?, 'started', ?, ?)
           ON CONFLICT(trade_date, dry_run) DO UPDATE SET
             order_status='started', created_at_utc=excluded.created_at_utc,
             updated_at_utc=excluded.updated_at_utc""",
        (trade_date, int(dry_run), now, now),
    )
    conn.commit()
    return True


def record_shortlist(conn: sqlite3.Connection, shortlist: ShortlistPayload) -> None:
    conn.execute(
        """INSERT INTO shortlists (trade_date, generated_at_utc, shortlist_json,
             stock_universe_size, crypto_universe_size)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(trade_date) DO UPDATE SET
             generated_at_utc=excluded.generated_at_utc,
             shortlist_json=excluded.shortlist_json,
             stock_universe_size=excluded.stock_universe_size,
             crypto_universe_size=excluded.crypto_universe_size""",
        (
            shortlist.date,
            shortlist.generated_at_utc,
            shortlist.model_dump_json(),
            shortlist.meta.stock_universe_size,
            shortlist.meta.crypto_universe_size,
        ),
    )
    conn.commit()


def record_judge_result(
    conn: sqlite3.Connection,
    trade_date: str,
    dry_run: bool,
    result: JudgeResult,
    claude_session_id: str | None = None,
    claude_cost_usd: float | None = None,
) -> None:
    order = result.order
    order_status = "skipped_dry_run" if dry_run else ("filled" if order and order.order_id else "error")
    conn.execute(
        """UPDATE picks SET
             asset_class=?, symbol=?, score=?, score_breakdown_json=?, risk_tier=?,
             rationale=?, red_flags_json=?, order_status=?, order_id=?, mcp_tool_called=?,
             filled_price=?, filled_qty=?, filled_usd=?, raw_order_response_json=?,
             claude_session_id=?, claude_cost_usd=?, updated_at_utc=?
           WHERE trade_date=? AND dry_run=?""",
        (
            result.asset_class,
            result.symbol,
            result.score,
            json.dumps({}),
            result.risk_tier,
            result.rationale,
            json.dumps(result.red_flags),
            order_status,
            order.order_id if order else None,
            order.mcp_tool_called if order else None,
            order.filled_price if order else None,
            order.filled_qty if order else None,
            order.filled_usd if order else None,
            json.dumps(order.raw_response) if order and order.raw_response else None,
            claude_session_id,
            claude_cost_usd,
            utcnow_iso(),
            trade_date,
            int(dry_run),
        ),
    )
    conn.commit()


def mark_error(conn: sqlite3.Connection, trade_date: str, dry_run: bool, status: str = "judge_error") -> None:
    conn.execute(
        "UPDATE picks SET order_status=?, updated_at_utc=? WHERE trade_date=? AND dry_run=?",
        (status, utcnow_iso(), trade_date, int(dry_run)),
    )
    conn.commit()


def get_recent_picks(conn: sqlite3.Connection, limit: int = 14) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM picks WHERE dry_run=0 ORDER BY trade_date DESC LIMIT ?",
        (limit,),
    ).fetchall()


def export_csv(conn: sqlite3.Connection, out_path: Path) -> None:
    rows = conn.execute("SELECT * FROM picks WHERE dry_run=0 ORDER BY trade_date").fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        if not rows:
            return
        writer = csv.writer(f)
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow(tuple(row))
