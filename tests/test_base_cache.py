"""Regression tests for cached_json's handling of an unreadable local cache
file. Found live on 2026-07-20: this project lives under an iCloud Drive
"Desktop & Documents" synced folder on a near-full disk; macOS evicts local
file content to dataless placeholders (file exists, nonzero size, 0 disk
blocks) that time out on read when materialized on demand. A crash inside
one candidate's cache read (TimeoutError, a local OSError) previously
propagated all the way out of the stock-screener's scoring loop and zeroed
out the entire day's shortlist rather than just that one candidate.
"""

import json

import pytest

from gempicker.data_sources.base import cached_json


def test_unreadable_cache_file_falls_back_to_fetch(tmp_path, monkeypatch):
    cache_path = tmp_path / "flaky.json"
    cache_path.write_text('{"stale": true}')

    real_read_text = type(cache_path).read_text
    calls = {"n": 0}

    def flaky_read_text(self, *a, **kw):
        # Only the read inside cached_json (the first call) simulates the
        # materialization timeout; later reads (the test's own verification)
        # behave normally, matching a real transient hiccup.
        calls["n"] += 1
        if self == cache_path and calls["n"] == 1:
            raise TimeoutError("[Errno 60] Operation timed out")
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr("pathlib.Path.read_text", flaky_read_text)

    result = cached_json(tmp_path, "flaky", 3600, lambda: {"fresh": True})
    assert result == {"fresh": True}
    assert json.loads(cache_path.read_text()) == {"fresh": True}  # self-heals: fetch result is rewritten


def test_corrupt_cache_json_falls_back_to_fetch(tmp_path):
    cache_path = tmp_path / "corrupt.json"
    cache_path.write_text("{not valid json")

    result = cached_json(tmp_path, "corrupt", 3600, lambda: {"fresh": True})
    assert result == {"fresh": True}


def test_healthy_cache_is_still_used_without_fetching(tmp_path):
    cache_path = tmp_path / "healthy.json"
    cache_path.write_text('{"cached": true}')

    def boom():
        raise AssertionError("fetch_fn should not be called when cache is healthy")

    result = cached_json(tmp_path, "healthy", 3600, boom)
    assert result == {"cached": True}
