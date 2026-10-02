import pytest

from models import AppConfig, ConfigError, SnippetConfig


def _snippet(**kw):
    base = dict(
        id="abc",
        owner="o",
        repo="r",
        branch="main",
        file_path="f.py",
        start_line=1,
        end_line=2,
        file_url="https://github.com/o/r/blob/main/f.py#L1-L2",
    )
    base.update(kw)
    return SnippetConfig(**base)


def test_valid_snippet_passes():
    _snippet().validate()


def test_bad_owner_rejected():
    with pytest.raises(ConfigError):
        _snippet(owner="bad/owner!").validate()


def test_bad_range_rejected():
    with pytest.raises(ConfigError):
        _snippet(start_line=5, end_line=2).validate()


def test_huge_snippet_rejected():
    with pytest.raises(ConfigError):
        _snippet(start_line=1, end_line=600).validate()


def test_bad_regex_rejected():
    with pytest.raises(ConfigError):
        _snippet(anchor_regex="(unclosed").validate()


def test_bad_interval_rejected():
    with pytest.raises(ConfigError):
        AppConfig(interval_seconds=1).validate()
    with pytest.raises(ConfigError):
        AppConfig(webhook_url="http://evil.example/hook").validate()


def test_from_dict_tolerates_missing_keys():
    cfg = AppConfig.from_dict({})
    assert cfg.snippets == [] and cfg.interval_seconds == 300


def test_from_dict_rejects_bad_shape():
    with pytest.raises(ConfigError):
        AppConfig.from_dict({"snippets": "nope"})


def test_namespace_with_subgroup_allowed():
    _snippet(owner="group/sub", provider="gitlab", host="gitlab.com").validate()


def test_bad_provider_rejected():
    with pytest.raises(ConfigError):
        _snippet(provider="cvs").validate()


def test_raw_allows_empty_branch():
    _snippet(
        provider="raw",
        host="example.com",
        branch="",
        file_url="https://example.com/f.txt#L1-L2",
    ).validate()


def test_non_raw_requires_branch():
    with pytest.raises(ConfigError):
        _snippet(provider="gitlab", branch="").validate()


def test_non_github_file_url_allowed():
    _snippet(
        provider="gitlab",
        host="gitlab.com",
        file_url="https://gitlab.com/g/r/-/blob/main/f.py#L1-L2",
    ).validate()
