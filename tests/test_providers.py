"""Provider parsing + fetch dispatch + branch probing (network stubbed)."""

import base64

import pytest
import requests

import remote_client
from models import ConfigError
from providers import parse_any_url
from remote_client import RemoteClient
from utils import snippet_id_from_parsed


class FakeResp:
    def __init__(self, status=200, text="", json_data=None, headers=None):
        self.status_code = status
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _stub(monkeypatch, handler):
    calls: list = []

    def fake_get(url, **kw):
        calls.append((url, kw))
        return handler(url, kw)

    monkeypatch.setattr(remote_client.requests, "get", fake_get)
    return calls


# ---------- parsing ----------


def test_github_shape():
    p = parse_any_url("https://github.com/o/r/blob/main/f.py#L26-L31")
    assert (p.provider, p.host, p.owner, p.repo, p.branch) == (
        "github",
        "github.com",
        "o",
        "r",
        "main",
    )
    assert (p.start_line, p.end_line) == (26, 31)


def test_ghe_shape_routes_to_github_provider():
    p = parse_any_url("https://ghe.corp.example/o/r/blob/main/f.py#L1-L2")
    assert p.provider == "github" and p.host == "ghe.corp.example"


def test_gitlab_simple_and_subgroups():
    p = parse_any_url("https://gitlab.com/group/sub/repo/-/blob/main/a/f.py#L2-L5")
    assert p.provider == "gitlab" and p.owner == "group/sub" and p.repo == "repo"
    assert (p.branch, p.file_path) == ("main", "a/f.py")
    assert (p.start_line, p.end_line) == (2, 5)


def test_gitlab_single_line_fragment():
    p = parse_any_url("https://gitlab.com/g/r/-/blob/v2/f.py#L7")
    assert (p.start_line, p.end_line) == (7, 7)


def test_gitea_branch_commit_tag():
    b = parse_any_url("https://codeberg.org/o/r/src/branch/main/f.py#L1-L2")
    assert (b.provider, b.branch, b.file_path) == ("gitea", "main", "f.py") and not b.fixed_ref
    c = parse_any_url("https://codeberg.org/o/r/src/commit/abc123/f.py#L1-L2")
    assert (c.branch, c.fixed_ref) == ("abc123", True)
    t = parse_any_url("https://codeberg.org/o/r/src/tag/v1.0/f.py#L1-L2")
    assert (t.branch, t.fixed_ref) == ("v1.0", True)


def test_bitbucket_lines_fragment():
    p = parse_any_url("https://bitbucket.org/o/r/src/main/f.py#lines-26:31")
    assert p.provider == "bitbucket" and (p.start_line, p.end_line) == (26, 31)
    single = parse_any_url("https://bitbucket.org/o/r/src/main/f.py#lines-9")
    assert (single.start_line, single.end_line) == (9, 9)


def test_bitbucket_branch_named_branch_not_gitea():
    p = parse_any_url("https://bitbucket.org/o/r/src/branch/f.py#L1-L2")
    assert p.provider == "bitbucket" and p.branch == "branch" and p.file_path == "f.py"


def test_sourcehut_shape():
    p = parse_any_url("https://git.sr.ht/~u/repo/tree/main/item/a/f.py#L3-L4")
    assert (p.provider, p.owner, p.branch, p.file_path) == ("sourcehut", "~u", "main", "a/f.py")


def test_raw_text_fallback():
    p = parse_any_url("https://example.com/files/notes.txt#L10-L20")
    assert p.provider == "raw" and p.branch == "" and (p.start_line, p.end_line) == (10, 20)


def test_rejects_http_and_unknown_and_traversal():
    with pytest.raises(ConfigError):
        parse_any_url("http://github.com/o/r/blob/main/f.py#L1-L2")
    with pytest.raises(ConfigError):
        parse_any_url("https://example.com/no-fragment-here")
    with pytest.raises(ConfigError):
        parse_any_url("https://github.com/o/r/blob/main/../x#L1-L2")
    with pytest.raises(ConfigError):
        parse_any_url("https://github.com/o/r/blob/main/f.py#L9-L2")


def test_snippet_id_unique_per_host():
    a = parse_any_url("https://github.com/o/r/blob/main/f.py#L1-L2")
    b = parse_any_url("https://ghe.corp.example/o/r/blob/main/f.py#L1-L2")
    assert snippet_id_from_parsed(a) != snippet_id_from_parsed(b)


# ---------- fetch dispatch ----------


def test_gitlab_fetch_uses_files_api_with_token(monkeypatch):
    calls = _stub(
        monkeypatch,
        lambda url, kw: FakeResp(text="hello\n") if "/api/v4/" in url else FakeResp(status=404),
    )
    client = RemoteClient(tokens={"gitlab_token": "GLTOK"})
    p = parse_any_url("https://gitlab.com/g/sub/r/-/blob/main/f.py#L1-L1")
    content = client.fetch(p).content
    assert content == "hello\n"
    url, kw = calls[0]
    assert "/api/v4/projects/g%2Fsub%2Fr/repository/files/f.py/raw" in url
    assert kw["headers"].get("PRIVATE-TOKEN") == "GLTOK"
    assert kw["params"]["ref"] == "main"


def test_gitea_fetch_decodes_base64(monkeypatch):
    b64 = base64.b64encode(b"code\n").decode()
    _stub(monkeypatch, lambda url, kw: FakeResp(json_data={"content": b64}))
    client = RemoteClient()
    p = parse_any_url("https://codeberg.org/o/r/src/branch/main/f.py#L1-L1")
    assert client.fetch(p).content == "code\n"


def test_ghe_fetch_uses_contents_api(monkeypatch):
    b64 = base64.b64encode(b"x = 1\n").decode()
    calls = _stub(monkeypatch, lambda url, kw: FakeResp(json_data={"content": b64}))
    client = RemoteClient(tokens={"github_token": "T"})
    p = parse_any_url("https://ghe.corp.example/o/r/blob/main/f.py#L1-L1")
    assert client.fetch(p).content == "x = 1\n"
    url, kw = calls[0]
    assert "/api/v3/repos/o/r/contents/f.py" in url
    assert kw["headers"]["Authorization"] == "Bearer T"


def test_bitbucket_fetch_uses_basic_auth(monkeypatch):
    calls = _stub(monkeypatch, lambda url, kw: FakeResp(text="bb\n"))
    client = RemoteClient(tokens={"bitbucket_username": "u", "bitbucket_app_password": "p"})
    p = parse_any_url("https://bitbucket.org/o/r/src/main/f.py#lines-1")
    assert client.fetch(p).content == "bb\n"
    url, kw = calls[0]
    assert url.startswith("https://api.bitbucket.org/2.0/repositories/o/r/src/main/f.py")
    assert kw["auth"] == ("u", "p")


def test_gitlab_commit_parsing(monkeypatch):
    _stub(
        monkeypatch,
        lambda url, kw: FakeResp(
            json_data=[
                {"id": "abc", "web_url": "W", "title": "T", "author_name": "A", "created_at": "D"}
            ]
        ),
    )
    p = parse_any_url("https://gitlab.com/g/r/-/blob/main/f.py#L1-L1")
    info = RemoteClient().get_latest_commit(p)
    assert info and (info.sha, info.url, info.message, info.author) == ("abc", "W", "T", "A")


def test_commit_lookup_never_raises(monkeypatch):
    _stub(monkeypatch, lambda url, kw: FakeResp(status=500))
    p = parse_any_url("https://codeberg.org/o/r/src/branch/main/f.py#L1-L1")
    assert RemoteClient().get_latest_commit(p) is None


# ---------- branch probing ----------


def test_resolve_probes_longest_branch_first(monkeypatch):
    seen: list = []

    def handler(url, kw):
        seen.append(url)
        if "/feature/x/f.py" in url:
            return FakeResp(text="one\ntwo\n")
        return FakeResp(status=404)

    _stub(monkeypatch, handler)
    p = parse_any_url("https://github.com/o/r/blob/feature/x/f.py#L1-L2")
    branch, path, content = RemoteClient().resolve(p)
    assert (branch, path) == ("feature/x", "f.py")
    assert content == "one\ntwo\n"
    # Longest candidate attempted first.
    assert "/feature/x/f.py" in seen[0]


def test_resolve_gives_up_with_clear_error(monkeypatch):
    _stub(monkeypatch, lambda url, kw: FakeResp(status=404))
    p = parse_any_url("https://github.com/o/r/blob/nope/f.py#L1-L1")
    with pytest.raises(Exception, match="Could not resolve"):
        RemoteClient().resolve(p)


def test_gitea_remainder_excludes_kind_marker():
    # Regression: probing must split branch/path, never include "branch/".
    p = parse_any_url("https://codeberg.org/o/r/src/branch/main/f.py#L1-L2")
    assert p.remainder == "main/f.py"
    p2 = parse_any_url("https://codeberg.org/o/r/src/branch/feat/x/f.py#L1-L2")
    assert p2.remainder == "feat/x/f.py"
    p3 = parse_any_url("https://gitlab.com/g/r/-/blob/feat/x/f.py#L1-L2")
    assert p3.remainder == "feat/x/f.py"


def test_selfhosted_gitlab_uses_custom_host(monkeypatch):
    calls = _stub(monkeypatch, lambda url, kw: FakeResp(text="hi\n"))
    client = RemoteClient(tokens={"gitlab_token": "T"})
    p = parse_any_url("https://somerandomdomain.com/group/sub/repo/-/blob/main/f.py#L1-L2")
    assert p.provider == "gitlab" and p.host == "somerandomdomain.com"
    assert p.owner == "group/sub"
    assert client.fetch(p).content == "hi\n"
    url, kw = calls[0]
    assert url.startswith("https://somerandomdomain.com/api/v4/projects/group%2Fsub%2Frepo/")
    assert kw["headers"].get("PRIVATE-TOKEN") == "T"


def test_selfhosted_gitea_uses_custom_host(monkeypatch):
    b64 = base64.b64encode(b"hi\n").decode()
    calls = _stub(monkeypatch, lambda url, kw: FakeResp(json_data={"content": b64}))
    client = RemoteClient()
    p = parse_any_url("https://git.example.org/o/r/src/branch/main/f.py#L1-L1")
    assert p.provider == "gitea" and p.host == "git.example.org"
    assert client.fetch(p).content == "hi\n"
    url, _ = calls[0]
    assert url.startswith("https://git.example.org/api/v1/repos/o/r/contents/f.py")


def test_ghe_uses_host_api_v3(monkeypatch):
    b64 = base64.b64encode(b"hi\n").decode()
    calls = _stub(monkeypatch, lambda url, kw: FakeResp(json_data={"content": b64}))
    p = parse_any_url("https://ghe.corp.example/o/r/blob/main/f.py#L1-L1")
    assert RemoteClient().fetch(p).content == "hi\n"
    url, _ = calls[0]
    assert url.startswith("https://ghe.corp.example/api/v3/repos/o/r/contents/f.py")
