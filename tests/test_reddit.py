"""Regression test for a real bug found in production: with
REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET left blank (a supported, documented
configuration -- see README), count_mentions was still making a real,
guaranteed-to-fail network request to Reddit's OAuth endpoint for every
single scored candidate, every run."""

from gempicker.data_sources.reddit import count_mentions


class _ExplodingSession:
    """Any network call at all is the bug -- fail loudly instead of hanging
    or silently succeeding against the real network."""

    def post(self, *a, **kw):
        raise AssertionError("count_mentions should not touch the network with blank credentials")

    def get(self, *a, **kw):
        raise AssertionError("count_mentions should not touch the network with blank credentials")


def test_blank_credentials_short_circuit_without_network_call(tmp_path):
    result = count_mentions(_ExplodingSession(), "", "", "test-agent", tmp_path, "ABC", ["stocks"])
    assert result is None


def test_blank_client_id_only_still_short_circuits(tmp_path):
    result = count_mentions(_ExplodingSession(), "", "secret", "test-agent", tmp_path, "ABC", ["stocks"])
    assert result is None


def test_blank_client_secret_only_still_short_circuits(tmp_path):
    result = count_mentions(_ExplodingSession(), "id", "", "test-agent", tmp_path, "ABC", ["stocks"])
    assert result is None
