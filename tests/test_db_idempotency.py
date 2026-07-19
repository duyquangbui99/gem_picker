from gempicker.db import claim_day, get_connection, mark_error, record_judge_result
from gempicker.models import JudgeResult


def test_claim_day_blocks_second_call_same_day(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    assert claim_day(conn, "2026-07-19", dry_run=True) is True
    assert claim_day(conn, "2026-07-19", dry_run=True) is False


def test_claim_day_dry_run_and_live_are_independent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    assert claim_day(conn, "2026-07-19", dry_run=True) is True
    # a live claim on the same date is a separate slot, not blocked by the dry-run claim
    assert claim_day(conn, "2026-07-19", dry_run=False) is True


def test_claim_day_different_dates_independent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    assert claim_day(conn, "2026-07-19", dry_run=True) is True
    assert claim_day(conn, "2026-07-20", dry_run=True) is True


def test_abandoned_claim_is_reclaimable(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    claim_day(conn, "2026-07-19", dry_run=True)
    # simulate a crashed run from 3 hours ago (past the 2-hour abandonment window)
    conn.execute(
        "UPDATE picks SET created_at_utc = datetime('now', '-3 hours') WHERE trade_date=?",
        ("2026-07-19",),
    )
    conn.commit()
    assert claim_day(conn, "2026-07-19", dry_run=True) is True


def test_record_judge_result_updates_claimed_row(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    claim_day(conn, "2026-07-19", dry_run=True)

    result = JudgeResult(
        date="2026-07-19",
        dry_run=True,
        asset_class="crypto",
        symbol="ABC",
        score=72.5,
        risk_tier="medium",
        rationale="test rationale",
        order=None,
    )
    record_judge_result(conn, "2026-07-19", True, result, claude_session_id="sess-1", claude_cost_usd=0.05)

    row = conn.execute("SELECT * FROM picks WHERE trade_date=? AND dry_run=1", ("2026-07-19",)).fetchone()
    assert row["symbol"] == "ABC"
    assert row["order_status"] == "skipped_dry_run"
    assert row["claude_session_id"] == "sess-1"


def test_mark_error_sets_status(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    claim_day(conn, "2026-07-19", dry_run=True)
    mark_error(conn, "2026-07-19", True, status="judge_error")
    row = conn.execute("SELECT order_status FROM picks WHERE trade_date=? AND dry_run=1", ("2026-07-19",)).fetchone()
    assert row["order_status"] == "judge_error"
