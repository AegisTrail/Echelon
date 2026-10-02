"""HTTP with bounded retries: backoff, jitter, Retry-After.

Policy:
- Retry: timeouts, connection errors, HTTP 429, HTTP 5xx.
- Never retry: 4xx (other than 429), successful responses.
- After exhausting attempts, connection errors raise; a final 429/5xx
  response is *returned* so callers map it to their own error type.
"""

from __future__ import annotations

import random
import time

import requests

MAX_ATTEMPTS = 3
BASE_DELAY = 1.0
MAX_DELAY = 30.0


def _retry_after_seconds(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After", "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), MAX_DELAY))
    except ValueError:
        return None


def _backoff_delay(attempt: int) -> float:
    # Jitter only decorrelates retries; cryptographic randomness not needed.
    return min(BASE_DELAY * (2**attempt), MAX_DELAY) + random.uniform(0, 0.5)  # noqa: S311


def get(
    url: str, *, timeout: int = 15, attempts: int = MAX_ATTEMPTS, **kwargs
) -> requests.Response:
    """GET with retries. kwargs are passed to requests.get (headers/params/auth)."""
    attempts = max(1, attempts)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(_backoff_delay(attempt))
            continue
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt < attempts - 1:
                delay = _retry_after_seconds(resp)
                time.sleep(delay if delay is not None else _backoff_delay(attempt))
                continue
            return resp
        return resp
    assert last_exc is not None
    raise last_exc
