import os

import pytest

from gempicker.lock import PipelineLockedError, lock_status, pipeline_lock


def test_lock_acquire_and_release(tmp_path):
    assert lock_status(tmp_path) is None
    with pipeline_lock(tmp_path):
        assert lock_status(tmp_path) == os.getpid()
    assert lock_status(tmp_path) is None


def test_lock_contention_raises(tmp_path):
    with pipeline_lock(tmp_path):
        with pytest.raises(PipelineLockedError, match="already in progress"):
            with pipeline_lock(tmp_path):
                pass


def test_lock_released_on_exception(tmp_path):
    with pytest.raises(ValueError):
        with pipeline_lock(tmp_path):
            raise ValueError("boom")
    assert lock_status(tmp_path) is None


def test_stale_lock_from_dead_pid_is_reclaimable(tmp_path):
    lock_path = tmp_path / ".pipeline.lock"
    tmp_path.mkdir(parents=True, exist_ok=True)
    # a PID essentially guaranteed not to be alive
    lock_path.write_text("999999")
    assert lock_status(tmp_path) is None
    with pipeline_lock(tmp_path):
        assert lock_status(tmp_path) == os.getpid()


def test_lock_status_ignores_garbage_content(tmp_path):
    lock_path = tmp_path / ".pipeline.lock"
    tmp_path.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not-a-pid")
    assert lock_status(tmp_path) is None
