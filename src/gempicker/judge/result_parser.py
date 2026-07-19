import json
from pathlib import Path

from gempicker.models import JudgeResult


class ResultValidationError(Exception):
    pass


def parse_and_validate(result_path: Path, expected_date: str, dry_run: bool) -> JudgeResult:
    if not result_path.exists():
        raise ResultValidationError(f"judge did not write a result file at {result_path}")

    data = json.loads(result_path.read_text())
    result = JudgeResult.model_validate(data)

    if result.date != expected_date:
        raise ResultValidationError(f"result date {result.date!r} != expected {expected_date!r}")

    if dry_run and result.order is not None:
        raise ResultValidationError(
            "dry-run violation: judge populated an order despite explicit dry-run instructions"
        )
    if not dry_run and result.order is None and not result.red_flags:
        raise ResultValidationError(
            "live run produced no order and no red_flags explaining why — trade may not have executed"
        )

    return result
