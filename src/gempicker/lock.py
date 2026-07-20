"""A cross-process lock so a Quick Screen, a full Run, and/or a cron-fired
run can never execute concurrently — they all hit the same rate-limited free
APIs, so overlapping runs both waste the daily "new lookups" budget twice
and risk combined rate-limit violations. Session-local UI state (disabling
buttons) can't catch this on its own: Streamlit's rerun model can abandon a
running script without killing the subprocess it launched, so the guard has
to live at the process level, not just in the UI."""

import os
from contextlib import contextmanager
from pathlib import Path


class PipelineLockedError(Exception):
    pass


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def lock_status(data_dir: Path) -> int | None:
    """Returns the PID holding the lock if it's genuinely alive, else None."""
    lock_path = data_dir / ".pipeline.lock"
    if not lock_path.exists():
        return None
    try:
        pid = int(lock_path.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid if _is_pid_alive(pid) else None


@contextmanager
def pipeline_lock(data_dir: Path):
    lock_path = data_dir / ".pipeline.lock"
    data_dir.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            # O_EXCL makes creation atomic: only one process can win this race.
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held_by = lock_status(data_dir)
            if held_by is not None:
                raise PipelineLockedError(f"another gempicker run is already in progress (pid {held_by})")
            # lock file exists but its PID is dead -- a crashed run left it
            # behind. Clean it up and retry the atomic create.
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        else:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break

    try:
        yield
    finally:
        try:
            if lock_path.exists() and lock_path.read_text().strip() == str(os.getpid()):
                lock_path.unlink()
        except OSError:
            pass
