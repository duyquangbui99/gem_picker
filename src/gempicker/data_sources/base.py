import json
import time
from pathlib import Path
from typing import Any, Callable

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

DEFAULT_TIMEOUT = 15


def new_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def with_retry(fn: Callable) -> Callable:
    """Wrap a fetch function with exponential-backoff retry for transient
    network/rate-limit errors, since free-tier APIs are flaky under load."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    )(fn)


def cached_json(cache_dir: Path, cache_key: str, ttl_seconds: int, fetch_fn: Callable[[], Any]) -> Any:
    """Generic file-backed JSON cache. Avoids re-hitting rate-limited free
    APIs for data that doesn't change meaningfully within the TTL window
    (e.g. market cap doesn't need re-fetching every run)."""
    cache_path = cache_dir / f"{cache_key}.json"
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            try:
                return json.loads(cache_path.read_text())
            except (OSError, json.JSONDecodeError):
                # A cache file existing doesn't guarantee it's readable --
                # verified live: this project lives under an iCloud Drive
                # "Desktop & Documents" synced folder on a nearly-full disk,
                # where macOS evicts local content to dataless placeholders
                # (nonzero logical size, 0 disk blocks) and a read has to
                # materialize it on demand; that materialization can time out
                # (OSError/TimeoutError), and an interrupted write under the
                # same sync pressure can also leave a truncated/corrupt file.
                # Either way this is exactly what a cache is for: treat it as
                # a miss and refetch rather than letting a local I/O hiccup
                # crash the caller.
                pass

    data = fetch_fn()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data))
    return data
