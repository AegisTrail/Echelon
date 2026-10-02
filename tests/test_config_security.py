import json
import os

from app_secrets import mask_secret, redact_dict, scan_text
from config_manager import ConfigManager
from models import AppConfig


def test_mask_never_leaks_full_value():
    sample_token = "ghp_abcdefghij1234567890"  # noqa: S105, echelon-allow-secret: synthetic fixture
    masked = mask_secret(sample_token)
    assert sample_token not in masked and len(masked) < len(sample_token)
    assert mask_secret("") == "(not set)"


def test_redact_masks_webhooks_and_keys():
    data = {"webhook_url": "https://discord.com/api/webhooks/1/abc", "interval_seconds": 30}
    red = redact_dict(data)
    assert "discord.com" not in red["webhook_url"]
    assert red["interval_seconds"] == 30


def test_scan_text_masks_preview():
    hook = "https://discord.com/api/webhooks/9/abcdefghij"  # echelon-allow-secret
    findings = scan_text(f"key {hook} end")
    assert findings and findings[0].pattern_name == "discord-webhook"
    assert "abcdefghij" not in findings[0].preview


def test_env_overlay_wins_and_not_persisted(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    mgr = ConfigManager(path=str(cfg_path))
    mgr.save(AppConfig(snippets=[]))
    monkeypatch.setenv("ECHELON_DISCORD_WEBHOOK", "https://discord.com/api/webhooks/9/zzz")
    loaded = mgr.load()
    assert loaded.webhook_url.endswith("/zzz")
    # File on disk unchanged (env never persisted).
    on_disk = json.loads(cfg_path.read_text())
    assert on_disk["webhook_url"] == ""


def test_save_uses_0600(tmp_path):
    cfg_path = tmp_path / "sub" / "config.json"
    mgr = ConfigManager(path=str(cfg_path))
    mgr.save(AppConfig(webhook_url="https://discord.com/api/webhooks/1/abc", snippets=[]))
    mode = oct(os.stat(cfg_path).st_mode & 0o777)
    assert mode == "0o600"


def test_malformed_json_rejected(tmp_path):
    from config_manager import ConfigManager
    from models import ConfigError

    bad = tmp_path / "config.json"
    bad.write_text("{not json")
    try:
        ConfigManager(path=str(bad)).load()
    except ConfigError as exc:
        assert "Invalid JSON" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_backup_written_on_save(tmp_path):
    import json as _json

    from config_manager import ConfigManager
    from models import AppConfig

    path = tmp_path / "config.json"
    mgr = ConfigManager(path=str(path))
    mgr.save(AppConfig(snippets=[]))
    assert not (tmp_path / "config.json.bak").exists()  # first save: nothing to back up
    cfg = mgr.load(validate=False)
    cfg.interval_seconds = 600
    mgr.save(cfg)
    bak = tmp_path / "config.json.bak"
    assert bak.exists()
    assert _json.loads(bak.read_text())["interval_seconds"] == 300
    import os as _os

    assert oct(_os.stat(bak).st_mode & 0o777) == "0o600"


def test_new_token_env_overlay(tmp_path, monkeypatch):
    from config_manager import ConfigManager

    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("ECHELON_GITLAB_TOKEN", "glpat-abc")
    monkeypatch.setenv("ECHELON_GITEA_TOKEN", "gitea-xyz")
    monkeypatch.setenv("ECHELON_BITBUCKET_USER", "u")
    monkeypatch.setenv("ECHELON_BITBUCKET_APP_PASSWORD", "p")
    cfg = ConfigManager().load()
    assert (cfg.gitlab_token, cfg.gitea_token) == ("glpat-abc", "gitea-xyz")
    assert (cfg.bitbucket_username, cfg.bitbucket_app_password) == ("u", "p")


def test_invalid_env_int_rejected(tmp_path, monkeypatch):
    from config_manager import ConfigManager
    from models import ConfigError

    monkeypatch.setenv("ECHELON_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("ECHELON_INTERVAL", "notanint")
    try:
        ConfigManager().load()
    except ConfigError:
        pass
    else:
        raise AssertionError("expected ConfigError")


def test_gitlab_token_scanned():
    from app_secrets import scan_text

    sample = "glpat-abcdefghij1234567890"  # echelon-allow-secret
    findings = scan_text(f"token {sample} here")
    assert any(f.pattern_name == "gitlab-token" for f in findings)
    assert "abcdefghij" not in findings[0].preview


def test_sourcehut_tree_without_item_rejected():
    from models import ConfigError
    from providers import parse_any_url

    try:
        parse_any_url("https://git.sr.ht/~u/r/tree/main/README.md#L1-L2")
    except ConfigError as exc:
        assert "item" in str(exc)
    else:
        raise AssertionError("expected ConfigError")
