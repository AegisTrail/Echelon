"""Retry policy: what is retried, what is not, and delay behavior."""

import requests

import net


class FakeResp:
    def __init__(self, status=200, text="ok", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def _patch(monkeypatch, behavior):
    """behavior: list of FakeResp or Exception, consumed per call."""
    calls = []
    sleeps = []
    queue = list(behavior)

    def fake_get(url, **kw):
        calls.append(url)
        item = queue.pop(0) if queue else FakeResp()
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(net.requests, "get", fake_get)
    monkeypatch.setattr(net.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


def test_success_no_retry(monkeypatch):
    calls, sleeps = _patch(monkeypatch, [FakeResp()])
    assert net.get("https://x.test/f").status_code == 200
    assert len(calls) == 1 and sleeps == []


def test_404_and_401_never_retried(monkeypatch):
    for status in (404, 401, 403):
        calls, sleeps = _patch(monkeypatch, [FakeResp(status=status)])
        assert net.get("https://x.test/f").status_code == status
        assert len(calls) == 1 and sleeps == []


def test_500_then_success(monkeypatch):
    calls, sleeps = _patch(monkeypatch, [FakeResp(status=500), FakeResp(text="recovered")])
    assert net.get("https://x.test/f").text == "recovered"
    assert len(calls) == 2 and len(sleeps) == 1 and sleeps[0] >= 1.0


def test_timeout_then_success(monkeypatch):
    calls, sleeps = _patch(
        monkeypatch, [requests.Timeout("t"), requests.ConnectionError("c"), FakeResp()]
    )
    assert net.get("https://x.test/f").status_code == 200
    assert len(calls) == 3 and len(sleeps) == 2


def test_exhausted_5xx_returns_last_response(monkeypatch):
    calls, sleeps = _patch(monkeypatch, [FakeResp(status=503)] * 5)
    assert net.get("https://x.test/f").status_code == 503
    assert len(calls) == net.MAX_ATTEMPTS


def test_exhausted_connection_errors_raise(monkeypatch):
    calls, _ = _patch(monkeypatch, [requests.ConnectionError("down")] * 5)
    try:
        net.get("https://x.test/f")
    except requests.ConnectionError:
        pass
    else:
        raise AssertionError("expected ConnectionError")
    assert len(calls) == net.MAX_ATTEMPTS


def test_retry_after_honored_and_capped(monkeypatch):
    calls, sleeps = _patch(
        monkeypatch, [FakeResp(status=429, headers={"Retry-After": "7"}), FakeResp()]
    )
    net.get("https://x.test/f")
    assert sleeps and sleeps[0] == 7.0

    calls, sleeps = _patch(
        monkeypatch, [FakeResp(status=429, headers={"Retry-After": "9999"}), FakeResp()]
    )
    net.get("https://x.test/f")
    assert sleeps[0] == net.MAX_DELAY


def test_backoff_grows(monkeypatch):
    _, sleeps = _patch(monkeypatch, [FakeResp(status=500)] * 2 + [FakeResp()])
    net.get("https://x.test/f")
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]
