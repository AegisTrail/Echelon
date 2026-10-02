"""End-to-end monitor pass with stubbed network (no real HTTP)."""

from config_manager import ConfigManager
from github_client import FetchResult
from models import AppConfig, SnippetConfig
from monitor import SnippetMonitor


class FakeRemote:
    """Stub for RemoteClient: parse is real (parse_any_url), network is fake."""

    def __init__(self, content, etag="E1"):
        self.content = content
        self.etag = etag
        self.commits_called = 0

    def update_tokens(self, tokens):
        pass

    def fetch(self, parsed, etag: str = ""):
        if etag and etag == self.etag:
            return FetchResult(content="", etag=etag, not_modified=True)
        return FetchResult(content=self.content, etag=self.etag)

    def get_latest_commit(self, parsed):
        self.commits_called += 1
        from github_client import CommitInfo

        return CommitInfo(
            sha="deadbeef",
            url="https://github.com/o/r/commit/deadbeef",
            message="fix",
            author="a",
            date="d",
        )


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def notify_change(self, snippet, diff_text, diff_summary=None, diff_source=None, **kw):
        self.calls.append(
            {"url": snippet.file_url, "diff": diff_text, "sha": kw.get("commit_sha", "")}
        )


def _mgr(tmp_path):
    mgr = ConfigManager(path=str(tmp_path / "config.json"))
    mgr.save(AppConfig(interval_seconds=300, snippets=[]))
    return mgr


def test_pure_move_reanchors_without_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(tmp_path)
    cfg = mgr.load(validate=False)
    cfg.snippets.append(
        SnippetConfig(
            id="s1",
            owner="o",
            repo="r",
            branch="main",
            file_path="f.py",
            start_line=2,
            end_line=3,
            file_url="https://github.com/o/r/blob/main/f.py#L2-L3",
            last_seen_code="b\nc\n",
            original_code="b\nc\n",
        )
    )
    mgr.save(cfg)
    remote = FakeRemote("x\na\nb\nc\nd\n")  # block moved 2-3 -> 3-4, same content
    notifier = FakeNotifier()
    mon = SnippetMonitor(mgr, remote, notifier)
    stats = mon.run_once()
    assert notifier.calls == []  # silent re-anchor
    assert stats["drifted"] == 1
    assert mgr.load(validate=False).snippets[0].start_line == 3


def test_real_change_alerts_records_history_and_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(tmp_path)
    cfg = mgr.load(validate=False)
    cfg.snippets.append(
        SnippetConfig(
            id="s1",
            owner="o",
            repo="r",
            branch="main",
            file_path="f.py",
            start_line=1,
            end_line=2,
            file_url="https://github.com/o/r/blob/main/f.py#L1-L2",
            last_seen_code="a\nb\n",
            original_code="a\nb\n",
        )
    )
    mgr.save(cfg)
    remote = FakeRemote("a\nB\nc\n")
    notifier = FakeNotifier()
    mon = SnippetMonitor(mgr, remote, notifier)
    stats = mon.run_once()
    assert stats["changed"] == 1
    assert len(notifier.calls) == 1
    assert notifier.calls[0]["sha"] == "deadbeef"
    assert remote.commits_called == 1


def test_etag_304_skips_work(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    mgr = _mgr(tmp_path)
    cfg = mgr.load(validate=False)
    cfg.snippets.append(
        SnippetConfig(
            id="s1",
            owner="o",
            repo="r",
            branch="main",
            file_path="f.py",
            start_line=1,
            end_line=1,
            file_url="https://github.com/o/r/blob/main/f.py#L1-L1",
            last_seen_code="a\n",
            original_code="a\n",
            etag="E1",
        )
    )
    mgr.save(cfg)
    remote = FakeRemote("a\nCHANGED\n", etag="E1")
    notifier = FakeNotifier()
    stats = SnippetMonitor(mgr, remote, notifier).run_once()
    assert stats["checked"] == 1 and stats["changed"] == 0
    assert notifier.calls == [] and remote.commits_called == 0
