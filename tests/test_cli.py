"""CLI smoke tests (no network for list/enable/export paths)."""

import json

from config_manager import ConfigManager
from echelon import main
from models import AppConfig


def _mgr(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setenv("ECHELON_CONFIG", str(p))
    mgr = ConfigManager(path=str(p))
    mgr.save(AppConfig(interval_seconds=300, snippets=[]))
    return mgr


def test_list_empty_ok(tmp_path, monkeypatch, capsys):
    _mgr(tmp_path, monkeypatch)
    assert main(["--list"]) == 0
    assert "No snippets" in capsys.readouterr().out


def test_export_strips_secrets_by_default(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    cfg = mgr.load(validate=False)
    cfg.webhook_url = "https://discord.com/api/webhooks/1/abc"
    mgr.save(cfg)
    dest = tmp_path / "out.json"
    assert main(["--export", str(dest)]) == 0
    data = json.loads(dest.read_text())
    assert data["webhook_url"] == ""


def test_check_secrets_flags_fixture_honors_marker(tmp_path, monkeypatch, capsys):
    _mgr(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)  # not a git repo: falls back to scanning cwd
    hook = "https://discord.com/api/webhooks/123/abcdefghij"  # echelon-allow-secret
    (tmp_path / "leaky.py").write_text(f'TOKEN = "{hook}"\n')
    prefix, tail = "glpat-abcde", "fghij1234567890"
    (tmp_path / "marked.py").write_text(f'SAMPLE = "{prefix}{tail}"  # echelon-allow-secret\n')
    assert main(["--check-secrets"]) == 1
    out = capsys.readouterr().out
    assert "WARNING" in out and "leaky.py" in out and "marked.py" not in out


def test_check_secrets_repo_tree_clean(monkeypatch, capsys):
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("ECHELON_CONFIG", raising=False)
    assert main(["--check-secrets"]) == 0
    assert "no committed-secret patterns found" in capsys.readouterr().out


URL = "https://github.com/o/r/blob/main/f.py#L1-L2"


def _seed(mgr):
    from models import SnippetConfig

    cfg = mgr.load(validate=False)
    cfg.snippets.append(
        SnippetConfig(
            id="seed1",
            owner="o",
            repo="r",
            branch="main",
            file_path="f.py",
            start_line=1,
            end_line=2,
            file_url=URL,
            last_seen_code="x\n",
        )
    )
    mgr.save(cfg)


def test_add_duplicate_rejected_without_network(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _seed(mgr)
    assert main(["--add", URL]) == 1  # duplicate check precedes any fetch


def test_add_invalid_url_rc2(tmp_path, monkeypatch, capsys):
    _mgr(tmp_path, monkeypatch)
    assert main(["--add", "https://example.com/no-fragment"]) == 2
    assert "Invalid URL" in capsys.readouterr().out


def test_remove_enable_disable_missing(tmp_path, monkeypatch):
    _mgr(tmp_path, monkeypatch)
    assert main(["--remove", URL]) == 1
    assert main(["--enable", URL]) == 1
    assert main(["--disable", URL]) == 1


def test_enable_disable_roundtrip(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _seed(mgr)
    assert main(["--disable", URL]) == 0
    assert mgr.load(validate=False).snippets[0].enabled is False
    assert main(["--enable", URL]) == 0
    assert mgr.load(validate=False).snippets[0].enabled is True


def test_set_note_requires_note(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _seed(mgr)
    assert main(["--set-note", URL]) == 2
    assert main(["--set-note", URL, "--note", "why"]) == 0
    assert mgr.load(validate=False).snippets[0].note == "why"


def test_bad_time_rejected(tmp_path, monkeypatch):
    _mgr(tmp_path, monkeypatch)
    assert main(["--time", "0"]) == 2
    assert main(["--time", "-5"]) == 2


def test_export_import_roundtrip(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _seed(mgr)
    dest = tmp_path / "bak.json"
    assert main(["--export", str(dest)]) == 0
    assert main(["--remove", URL]) == 0
    assert main(["--import", str(dest)]) == 0
    assert len(mgr.load(validate=False).snippets) == 1


def test_import_invalid_file(tmp_path, monkeypatch):
    _mgr(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text('{"snippets": "nope"}')
    assert main(["--import", str(bad)]) == 2
    missing = tmp_path / "missing.json"
    assert main(["--import", str(missing)]) == 1


def test_check_json_output(tmp_path, monkeypatch, capsys):
    _mgr(tmp_path, monkeypatch)
    assert main(["--check", "--no-notify"]) == 1  # no snippets configured
    out = capsys.readouterr().out
    assert "No snippets" in out
    assert main(["--check", "--no-notify", "--json"]) == 1  # flag must not crash


def test_check_json_stats_shape(tmp_path, monkeypatch, capsys):
    import json as _json

    import echelon as _echelon
    from github_client import FetchResult

    mgr = _mgr(tmp_path, monkeypatch)
    _seed(mgr)

    class _FakeRemote:
        def update_tokens(self, tokens):
            pass

        def fetch(self, parsed, etag=""):
            return FetchResult(content="x\n", etag="")

        def get_latest_commit(self, parsed):
            return None

    monkeypatch.setattr(_echelon, "_build_remote_client", lambda *a, **k: _FakeRemote())
    assert main(["--check", "--no-notify", "--json"]) == 0
    out = capsys.readouterr().out
    payload = _json.loads(out[out.index("{") :])
    assert set(payload) >= {"checked", "changed", "drifted", "skipped", "errors", "version"}
