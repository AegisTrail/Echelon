"""Monitor stability: one bad snippet must never sink the whole pass."""

import time

import monitor
from config_manager import ConfigManager
from github_client import FetchResult
from models import AppConfig, SnippetConfig
from monitor import SnippetMonitor


def _snippet(url="https://github.com/o/r/blob/main/f.py#L1-L1", **kw):
    base = dict(
        id="s-" + str(abs(hash(url)) % 10_000),
        owner="o",
        repo="r",
        branch="main",
        file_path="f.py",
        start_line=1,
        end_line=1,
        file_url=url,
        last_seen_code="a\n",
        original_code="a\n",
    )
    base.update(kw)
    return SnippetConfig(**base)


class FakeRemote:
    def __init__(self, content="a\n", fail_with=None):
        self.content = content
        self.fail_with = fail_with
        self.calls = 0

    def update_tokens(self, tokens):
        pass

    def fetch(self, parsed, etag=""):
        self.calls += 1
        if self.fail_with:
            raise self.fail_with
        return FetchResult(content=self.content, etag="")

    def get_latest_commit(self, parsed):
        return None


class FakeNotifier:
    def __init__(self):
        self.calls = 0

    def notify_change(self, *a, **k):
        self.calls += 1


def _mgr(tmp_path, snippets):
    mgr = ConfigManager(path=str(tmp_path / "config.json"))
    mgr.save(AppConfig(interval_seconds=300, snippets=snippets))
    return mgr


def test_failing_snippet_isolated_and_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))

    class Flaky(FakeRemote):
        def fetch(self, parsed, etag=""):
            if "bad" in parsed.file_url:
                raise RuntimeError("boom")
            return FetchResult(content="a\n", etag="")

    mgr = _mgr(
        tmp_path,
        [
            _snippet("https://github.com/o/bad/blob/main/f.py#L1-L1"),
            _snippet("https://github.com/o/good/blob/main/f.py#L1-L1"),
        ],
    )
    stats = SnippetMonitor(mgr, Flaky(), FakeNotifier()).run_once()
    assert stats["errors"] == 1 and stats["checked"] == 1


def test_invalid_url_counts_as_error_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(tmp_path, [_snippet("https://example.com/no-fragment")])
    stats = SnippetMonitor(mgr, FakeRemote(), FakeNotifier()).run_once()
    assert stats["errors"] == 1 and stats["checked"] == 0


def test_disabled_and_interval_snippets_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(
        tmp_path,
        [
            _snippet("https://github.com/o/a/blob/main/f.py#L1-L1", enabled=False),
            _snippet("https://github.com/o/b/blob/main/f.py#L1-L1", interval_seconds=3600),
        ],
    )
    remote = FakeRemote()
    mon = SnippetMonitor(mgr, remote, FakeNotifier())
    mon._last_check[mgr.load(validate=False).snippets[1].id] = time.time()
    stats = mon.run_once()
    assert stats["skipped"] == 2 and stats["checked"] == 0 and remote.calls == 0


def test_cooldown_suppresses_repeat_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = ConfigManager(path=str(tmp_path / "config.json"))
    mgr.save(
        AppConfig(
            interval_seconds=300,
            notify_cooldown_seconds=3600,
            snippets=[_snippet("https://github.com/o/r/blob/main/f.py#L1-L1")],
        )
    )
    remote = FakeRemote(content="CHANGED\n")
    notifier = FakeNotifier()
    mon = SnippetMonitor(mgr, remote, notifier)
    first = mon.run_once()
    # Tamper last_seen back to force the "same" diff twice in a row.
    cfg = mgr.load(validate=False)
    cfg.snippets[0].last_seen_code = "a\n"
    mgr.save(cfg)
    second = mon.run_once()
    assert first["changed"] == 1 and second["changed"] == 1
    assert notifier.calls == 1  # second identical alert suppressed by cooldown


def test_history_failure_does_not_lose_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(tmp_path, [_snippet("https://github.com/o/r/blob/main/f.py#L1-L1")])
    monkeypatch.setattr(
        monitor, "record_change", lambda *a, **k: (_ for _ in ()).throw(OSError("db gone"))
    )
    stats = SnippetMonitor(mgr, FakeRemote(content="NEW\n"), FakeNotifier()).run_once()
    assert stats["changed"] == 1
    assert mgr.load(validate=False).snippets[0].last_seen_code == "NEW\n"
