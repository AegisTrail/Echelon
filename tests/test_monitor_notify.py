"""Monitor + notifier unit tests (no network)."""

from fanout import FanoutNotifier
from models import SnippetConfig
from monitor import build_diff


def _snippet():
    return SnippetConfig(
        id="s1",
        owner="o",
        repo="r",
        branch="main",
        file_path="f.py",
        start_line=1,
        end_line=2,
        file_url="https://github.com/o/r/blob/main/f.py#L1-L2",
        last_seen_code="a\n",
    )


class Recorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def notify_change(self, snippet, diff_text, diff_summary=None, diff_source=None, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((snippet.id, diff_text, diff_summary, kw.get("commit_sha", "")))


def test_build_diff_contains_change_lines():
    d = build_diff("a\nb\n", "a\nB\n")
    assert "-b" in d and "+B" in d
    assert "---" not in d and "+++" not in d


def test_build_diff_empty_when_same():
    assert build_diff("a\n", "a\n") == ""


def test_fanout_delivers_to_all_and_survives_failure():
    good1, bad, good2 = Recorder(), Recorder(fail=True), Recorder()
    FanoutNotifier([good1, bad, good2]).notify_change(_snippet(), "diff", commit_sha="abc")
    assert len(good1.calls) == 1 and len(good2.calls) == 1
    assert good1.calls[0][3] == "abc"


def test_fanout_empty_is_safe():
    FanoutNotifier([]).notify_change(_snippet(), "diff")  # must not raise
