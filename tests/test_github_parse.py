import pytest

from github_client import GitHubClient
from models import ConfigError

CLIENT = GitHubClient()


def test_parse_standard_url():
    p = CLIENT.parse_github_url("https://github.com/owner/repo/blob/main/path/file.py#L26-L31")
    assert (p.owner, p.repo, p.branch, p.file_path) == ("owner", "repo", "main", "path/file.py")
    assert (p.start_line, p.end_line) == (26, 31)


def test_parse_single_line():
    p = CLIENT.parse_github_url("https://github.com/o/r/blob/v1/f.py#L5")
    assert (p.start_line, p.end_line) == (5, 5)


def test_parse_rejects_non_github():
    with pytest.raises(ConfigError):
        CLIENT.parse_github_url("https://example.com/o/r/blob/main/f.py#L1-L2")


def test_parse_rejects_missing_fragment():
    with pytest.raises(ConfigError):
        CLIENT.parse_github_url("https://github.com/o/r/blob/main/f.py")


def test_parse_rejects_traversal():
    with pytest.raises(ConfigError):
        CLIENT.parse_github_url("https://github.com/o/r/blob/main/../secret#L1-L2")


def test_parse_rejects_huge_range():
    with pytest.raises(ConfigError):
        CLIENT.parse_github_url("https://github.com/o/r/blob/main/f.py#L1-L999")


def test_extract_lines_ok():
    content = "a\nb\nc\nd\n"
    assert CLIENT.extract_lines(content, 2, 3) == "b\nc\n"


def test_extract_lines_out_of_range():
    from github_client import GitHubError

    with pytest.raises(GitHubError):
        CLIENT.extract_lines("a\nb\n", 1, 10)
