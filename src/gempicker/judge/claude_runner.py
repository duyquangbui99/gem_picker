"""Invokes Claude Code headless as the judgment + MCP-execution step. The
actual structured JudgeResult is written by Claude to a result file per the
prompt's instructions (see result_parser.py) rather than parsed out of the
CLI's own JSON wrapper — asking a conversational agent to also perfectly
double as the transport layer is fragile; a file is not."""

import json
import subprocess
from pathlib import Path


class ClaudeRunError(Exception):
    pass


def run_claude_judge(prompt: str, project_root: Path, timeout: int = 300) -> dict:
    """Runs `claude -p` non-interactively. cwd=project_root so .mcp.json and
    .claude/settings.json auto-load (no --bare, which would skip MCP
    discovery entirely). Returns the CLI's own JSON wrapper (session_id,
    cost_usd, etc.) for logging purposes."""
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeRunError(f"claude -p timed out after {timeout}s") from e

    if proc.returncode != 0:
        raise ClaudeRunError(f"claude -p exited {proc.returncode}: {proc.stderr[-2000:]}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeRunError(f"could not parse claude CLI output as JSON: {e}\nstdout tail: {proc.stdout[-2000:]}") from e

    # returncode can be 0 even when the CLI itself reports an internal error
    if payload.get("is_error"):
        raise ClaudeRunError(f"claude -p reported is_error=true: {payload.get('result')}")

    return payload
