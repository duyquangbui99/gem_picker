"""Tests for the safety-critical dry-run enforcement in result_parser.py:
if Claude ignores the dry-run instruction and populates an order anyway,
this must be caught rather than silently logged as a real trade."""

import json

import pytest

from gempicker.judge.result_parser import ResultValidationError, parse_and_validate


def _write_result(path, **overrides):
    payload = {
        "date": "2026-07-19",
        "dry_run": True,
        "asset_class": "crypto",
        "symbol": "ABC",
        "score": 70.0,
        "risk_tier": "medium",
        "rationale": "test",
        "red_flags": [],
        "order": None,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload))


def test_valid_dry_run_result_parses(tmp_path):
    path = tmp_path / "result.json"
    _write_result(path)
    result = parse_and_validate(path, "2026-07-19", dry_run=True)
    assert result.symbol == "ABC"
    assert result.order is None


def test_dry_run_violation_raises(tmp_path):
    path = tmp_path / "result.json"
    _write_result(
        path,
        order={
            "order_id": "fake-123",
            "mcp_tool_called": "mcp__coinbase__create_order",
            "filled_price": 1.0,
            "filled_qty": 5.0,
            "filled_usd": 5.0,
            "raw_response": {},
        },
    )
    with pytest.raises(ResultValidationError, match="dry-run violation"):
        parse_and_validate(path, "2026-07-19", dry_run=True)


def test_live_run_without_order_or_red_flags_raises(tmp_path):
    path = tmp_path / "result.json"
    _write_result(path, dry_run=False, order=None, red_flags=[])
    with pytest.raises(ResultValidationError, match="produced no order"):
        parse_and_validate(path, "2026-07-19", dry_run=False)


def test_live_run_without_order_but_with_red_flags_is_allowed(tmp_path):
    path = tmp_path / "result.json"
    _write_result(path, dry_run=False, order=None, red_flags=["MCP trade tool call failed: timeout"])
    result = parse_and_validate(path, "2026-07-19", dry_run=False)
    assert result.order is None


def test_missing_result_file_raises(tmp_path):
    with pytest.raises(ResultValidationError, match="did not write"):
        parse_and_validate(tmp_path / "nonexistent.json", "2026-07-19", dry_run=True)


def test_date_mismatch_raises(tmp_path):
    path = tmp_path / "result.json"
    _write_result(path, date="2026-07-20")
    with pytest.raises(ResultValidationError, match="!= expected"):
        parse_and_validate(path, "2026-07-19", dry_run=True)
