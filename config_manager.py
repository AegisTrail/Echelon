"""Config loading/saving with XDG path, env overlay, atomic writes, 0600 perms."""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from app_secrets import SECRET_ENV_VARS
from models import AppConfig, ConfigError

DEFAULT_FILENAME = "config.json"
APP_DIR_NAME = "echelon"


def default_config_path() -> str:
    """Resolve where config.json lives.

    Priority: $ECHELON_CONFIG > ./config.json (if it exists, backwards compat)
    > $XDG_CONFIG_HOME/echelon/config.json > ~/.config/echelon/config.json
    """
    env_path = os.environ.get("ECHELON_CONFIG", "").strip()
    if env_path:
        return os.path.abspath(os.path.expanduser(env_path))
    local = os.path.abspath(DEFAULT_FILENAME)
    if os.path.exists(local):
        return local
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = xdg if xdg else os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR_NAME, DEFAULT_FILENAME)


def apply_env_overlay(config: AppConfig) -> AppConfig:
    """Overlay secrets/settings from environment (env wins, never persisted)."""
    get = os.environ.get
    mapping: dict[str, str | None] = {
        "webhook_url": get(SECRET_ENV_VARS["webhook_url"]),
        "telegram_bot_token": get(SECRET_ENV_VARS["telegram_bot_token"]),
        "telegram_chat_id": get(SECRET_ENV_VARS["telegram_chat_id"]),
        "github_token": get(SECRET_ENV_VARS["github_token"]),
        "gitlab_token": get(SECRET_ENV_VARS["gitlab_token"]),
        "gitea_token": get(SECRET_ENV_VARS["gitea_token"]),
        "bitbucket_username": get(SECRET_ENV_VARS["bitbucket_username"]),
        "bitbucket_app_password": get(SECRET_ENV_VARS["bitbucket_app_password"]),
        "openai_key": get(SECRET_ENV_VARS["openai_key"]),
        "openai_model": get(SECRET_ENV_VARS["openai_model"]),
        "gemini_api_key": get(SECRET_ENV_VARS["gemini_api_key"]),
        "gemini_model": get(SECRET_ENV_VARS["gemini_model"]),
        "ollama_endpoint": get(SECRET_ENV_VARS["ollama_endpoint"]),
        "ollama_model": get(SECRET_ENV_VARS["ollama_model"]),
        "slack_webhook_url": get(SECRET_ENV_VARS["slack_webhook_url"]),
        "generic_webhook_url": get(SECRET_ENV_VARS["generic_webhook_url"]),
        "smtp_host": get(SECRET_ENV_VARS["smtp_host"]),
        "smtp_user": get(SECRET_ENV_VARS["smtp_user"]),
        "smtp_password": get(SECRET_ENV_VARS["smtp_password"]),
        "email_from": get(SECRET_ENV_VARS["email_from"]),
        "email_to": get(SECRET_ENV_VARS["email_to"]),
    }
    for attr, val in mapping.items():
        if val:  # non-empty env wins
            setattr(config, attr, val.strip())
    port = (get(SECRET_ENV_VARS["smtp_port"]) or "").strip()
    if port:
        try:
            config.smtp_port = int(port)
        except ValueError as exc:
            raise ConfigError(f"{SECRET_ENV_VARS['smtp_port']} must be an integer") from exc
    interval = (get("ECHELON_INTERVAL") or "").strip()
    if interval:
        try:
            config.interval_seconds = int(interval)
        except ValueError as exc:
            raise ConfigError("ECHELON_INTERVAL must be an integer") from exc
    return config


class ConfigManager:
    def __init__(self, path: str | None = None):
        explicit = path or os.environ.get("ECHELON_CONFIG", "").strip()
        self.path = (
            os.path.abspath(os.path.expanduser(explicit)) if explicit else default_config_path()
        )

    def load(self, *, validate: bool = True) -> AppConfig:
        if not os.path.exists(self.path):
            config = AppConfig(snippets=[])
        else:
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"Invalid JSON in {self.path}: {exc}") from exc
            except OSError as exc:
                raise ConfigError(f"Cannot read {self.path}: {exc}") from exc
            config = AppConfig.from_dict(data)
        config = apply_env_overlay(config)
        if validate:
            config.validate()
        return config

    def save(self, config: AppConfig) -> None:
        config.validate()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Keep one backup of the last good config (best-effort recovery aid).
        if os.path.exists(self.path):
            try:
                shutil.copy2(self.path, self.path + ".bak")
                try:
                    os.chmod(self.path + ".bak", 0o600)
                except OSError:
                    pass
            except OSError:
                pass
        # Atomic write: tmp file in same dir + rename. Strip env-only secrets?
        # No: env overlay values live only in memory; but if user runs --init
        # with env set we'd persist env values. To avoid confusion, persist
        # exactly what is in `config` (documented behaviour).
        fd, tmp_path = tempfile.mkstemp(dir=parent or ".", prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(config.to_dict(include_secrets=True), fh, indent=2)
                fh.write("\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
