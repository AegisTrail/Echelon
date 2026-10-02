"""Data models with validation (config schema hardening)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["ConfigError", "SnippetConfig", "AppConfig", "ChangeContext"]

_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_.\-~/]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_./\-]+$")
_MAX_SNIPPET_LINES = 500
_MIN_INTERVAL = 5
_MAX_INTERVAL = 30 * 24 * 3600  # 30 days
PROVIDERS = ("github", "gitlab", "gitea", "bitbucket", "sourcehut", "raw")


class ConfigError(ValueError):
    """Raised when config.json (or env overlay) fails validation."""


def _validate_owner_repo(label: str, value: str) -> str:
    value = (value or "").strip()
    if not value or not _OWNER_REPO_RE.match(value):
        raise ConfigError(f"Invalid {label}: {value!r}. Must match [A-Za-z0-9_.-]+")
    return value


def _validate_namespace(value: str) -> str:
    """Owner/namespace; may contain / subgroups (GitLab) or ~user (SourceHut)."""
    value = (value or "").strip().strip("/")
    if not value or not _NAMESPACE_RE.match(value):
        raise ConfigError(f"Invalid owner/namespace: {value!r}")
    if ".." in value.split("/") or any(not seg for seg in value.split("/")):
        raise ConfigError(f"Invalid owner/namespace: {value!r}")
    return value


def _validate_branch(value: str, *, allow_empty: bool = False) -> str:
    value = (value or "").strip()
    if not value:
        if allow_empty:
            return ""
        value = "main"
    if not _BRANCH_RE.match(value):
        raise ConfigError(f"Invalid branch: {value!r}")
    if ".." in value:
        raise ConfigError(f"Invalid branch (path traversal): {value!r}")
    return value


def _validate_file_path(value: str) -> str:
    value = (value or "").strip().lstrip("/")
    if not value or ".." in value.split("/") or value.startswith(".git/"):
        raise ConfigError(f"Invalid file_path: {value!r}")
    if len(value) > 500:
        raise ConfigError("file_path too long (max 500 chars)")
    return value


def _validate_https_url(value: str, *, field_name: str, allow_empty: bool = True) -> str:
    value = (value or "").strip()
    if not value:
        if allow_empty:
            return ""
        raise ConfigError(f"{field_name} is required but empty")
    if not (
        value.startswith("https://")
        or value.startswith("http://localhost")
        or value.startswith("http://127.0.0.1")
    ):
        raise ConfigError(
            f"{field_name} must use https:// (http allowed only for localhost). Got: {value[:40]!r}..."
        )
    if len(value) > 2000:
        raise ConfigError(f"{field_name} too long")
    return value


@dataclass
class SnippetConfig:
    id: str
    owner: str
    repo: str
    branch: str
    file_path: str
    start_line: int
    end_line: int
    file_url: str
    note: str = ""
    original_code: str = ""
    last_seen_code: str = ""
    # --- provider routing ---
    provider: str = "github"
    host: str = "github.com"
    # --- line-drift resistance ---
    anchor: str = ""
    anchor_regex: str = ""
    context_lines: int = 3
    # --- scheduling / state ---
    enabled: bool = True
    interval_seconds: int = 0  # 0 = inherit global
    last_commit_sha: str = ""
    etag: str = ""

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ConfigError(
                f"Invalid provider: {self.provider!r}. Must be one of {', '.join(PROVIDERS)}"
            )
        self.host = (self.host or "").strip().lower()
        if not self.host or len(self.host) > 255 or ".." in self.host or "/" in self.host:
            raise ConfigError(f"Invalid host: {self.host!r}")
        self.owner = _validate_namespace(self.owner)
        self.repo = _validate_owner_repo("repo", self.repo)
        if not self.branch and self.provider != "raw":
            raise ConfigError("branch is required for provider snippets (empty only for raw URLs)")
        self.branch = _validate_branch(self.branch, allow_empty=self.provider == "raw")
        if self.provider == "raw" and self.branch:
            raise ConfigError("raw snippets must not set a branch")
        self.file_path = _validate_file_path(self.file_path)
        if not isinstance(self.start_line, int) or not isinstance(self.end_line, int):
            raise ConfigError("start_line/end_line must be integers")
        if self.start_line < 1:
            raise ConfigError("start_line must be >= 1")
        if self.end_line < self.start_line:
            raise ConfigError("end_line must be >= start_line")
        if self.end_line - self.start_line + 1 > _MAX_SNIPPET_LINES:
            raise ConfigError(f"Snippet too large (max {_MAX_SNIPPET_LINES} lines)")
        if self.file_url:
            if not self.file_url.startswith("https://"):
                raise ConfigError(f"file_url must be an https:// URL, got {self.file_url!r}")
            if len(self.file_url) > 2000:
                raise ConfigError("file_url too long")
        if len(self.note) > 500:
            raise ConfigError("note too long (max 500 chars)")
        if self.anchor and len(self.anchor) > 300:
            raise ConfigError("anchor too long (max 300 chars)")
        if self.anchor_regex:
            if len(self.anchor_regex) > 500:
                raise ConfigError("anchor_regex too long (max 500 chars)")
            try:
                re.compile(self.anchor_regex)
            except re.error as exc:
                raise ConfigError(f"Invalid anchor_regex: {exc}") from exc
        if not isinstance(self.context_lines, int) or not 0 <= self.context_lines <= 20:
            raise ConfigError("context_lines must be an int in 0..20")
        if self.interval_seconds:
            if (
                not isinstance(self.interval_seconds, int)
                or not _MIN_INTERVAL <= self.interval_seconds <= _MAX_INTERVAL
            ):
                raise ConfigError(
                    f"snippet interval_seconds must be {_MIN_INTERVAL}..{_MAX_INTERVAL}"
                )
        if self.last_commit_sha and not re.fullmatch(r"[0-9a-f]{4,40}", self.last_commit_sha):
            raise ConfigError("last_commit_sha must be a hex sha")

    @property
    def length(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass
class AppConfig:
    webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    interval_seconds: int = 300
    ollama_endpoint: str = ""
    ollama_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""
    openai_key: str = ""
    openai_model: str = ""
    github_token: str = ""
    gitlab_token: str = ""
    gitea_token: str = ""
    bitbucket_username: str = ""
    bitbucket_app_password: str = ""
    slack_webhook_url: str = ""
    generic_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""
    email_use_tls: bool = True
    notify_cooldown_seconds: int = 0
    history_db_path: str = ""
    snippets: list[SnippetConfig] = field(default_factory=list)

    def validate(self) -> None:
        if (
            not isinstance(self.interval_seconds, int)
            or not _MIN_INTERVAL <= self.interval_seconds <= _MAX_INTERVAL
        ):
            raise ConfigError(f"interval_seconds must be {_MIN_INTERVAL}..{_MAX_INTERVAL}")
        _validate_https_url(self.webhook_url, field_name="webhook_url")
        _validate_https_url(self.slack_webhook_url, field_name="slack_webhook_url")
        _validate_https_url(self.generic_webhook_url, field_name="generic_webhook_url")
        if self.telegram_bot_token and not re.fullmatch(
            r"\d{6,12}:[A-Za-z0-9_\-]{20,}", self.telegram_bot_token.strip()
        ):
            raise ConfigError("telegram_bot_token looks malformed")
        if self.telegram_chat_id and len(str(self.telegram_chat_id)) > 64:
            raise ConfigError("telegram_chat_id too long")
        if self.github_token and len(self.github_token.strip()) > 500:
            raise ConfigError("github_token too long")
        if self.gitlab_token and len(self.gitlab_token.strip()) > 500:
            raise ConfigError("gitlab_token too long")
        if self.gitea_token and len(self.gitea_token.strip()) > 500:
            raise ConfigError("gitea_token too long")
        if self.bitbucket_username and len(self.bitbucket_username.strip()) > 255:
            raise ConfigError("bitbucket_username too long")
        if self.bitbucket_app_password and len(self.bitbucket_app_password) > 500:
            raise ConfigError("bitbucket_app_password too long")
        if self.ollama_endpoint:
            ep = self.ollama_endpoint.strip()
            if not (ep.startswith("http://") or ep.startswith("https://")):
                raise ConfigError("ollama_endpoint must start with http(s)://")
        for field_name in ("ollama_model", "gemini_model", "openai_model"):
            if len(getattr(self, field_name) or "") > 200:
                raise ConfigError(f"{field_name} too long")
        if self.smtp_host and len(self.smtp_host) > 255:
            raise ConfigError("smtp_host too long")
        if self.smtp_port and (
            not isinstance(self.smtp_port, int) or not 1 <= self.smtp_port <= 65535
        ):
            raise ConfigError("smtp_port must be 1..65535")
        if self.email_from and ("<" in self.email_from and ">" not in self.email_from):
            raise ConfigError("email_from looks malformed")
        for addr in (self.email_from, self.email_to):
            if addr and ("\n" in addr or "\r" in addr):
                raise ConfigError("email address must not contain newlines (header injection)")
        if self.email_to and len(self.email_to) > 1000:
            raise ConfigError("email_to too long")
        if (
            not isinstance(self.notify_cooldown_seconds, int)
            or not 0 <= self.notify_cooldown_seconds <= 86400
        ):
            raise ConfigError("notify_cooldown_seconds must be 0..86400")
        for snippet in self.snippets or []:
            snippet.validate()

    def to_dict(self, *, include_secrets: bool = True) -> dict[str, Any]:
        from app_secrets import is_secret_field  # local import: avoid cycle

        data = {
            "webhook_url": self.webhook_url,
            "telegram_bot_token": self.telegram_bot_token,
            "telegram_chat_id": self.telegram_chat_id,
            "interval_seconds": self.interval_seconds,
            "ollama_endpoint": self.ollama_endpoint,
            "ollama_model": self.ollama_model,
            "gemini_api_key": self.gemini_api_key,
            "gemini_model": self.gemini_model,
            "openai_key": self.openai_key,
            "openai_model": self.openai_model,
            "github_token": self.github_token,
            "gitlab_token": self.gitlab_token,
            "gitea_token": self.gitea_token,
            "bitbucket_username": self.bitbucket_username,
            "bitbucket_app_password": self.bitbucket_app_password,
            "slack_webhook_url": self.slack_webhook_url,
            "generic_webhook_url": self.generic_webhook_url,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_user": self.smtp_user,
            "smtp_password": self.smtp_password,
            "email_from": self.email_from,
            "email_to": self.email_to,
            "email_use_tls": self.email_use_tls,
            "notify_cooldown_seconds": self.notify_cooldown_seconds,
            "history_db_path": self.history_db_path,
            "snippets": [asdict(s) for s in (self.snippets or [])],
        }
        if not include_secrets:
            for key in list(data.keys()):
                if is_secret_field(key):
                    data[key] = ""
        return data

    @staticmethod
    def from_dict(data: dict[str, Any]) -> AppConfig:
        if not isinstance(data, dict):
            raise ConfigError("config.json root must be a JSON object")
        snippets_data = data.get("snippets", [])
        if not isinstance(snippets_data, list):
            raise ConfigError("'snippets' must be a list")
        snippets: list[SnippetConfig] = []
        for i, s in enumerate(snippets_data):
            if not isinstance(s, dict):
                raise ConfigError(f"snippets[{i}] must be an object")
            try:
                snippets.append(
                    SnippetConfig(
                        id=str(s.get("id", f"snippet-{i}")),
                        owner=str(s.get("owner", "")),
                        repo=str(s.get("repo", "")),
                        branch=str(s.get("branch", "main")),
                        file_path=str(s.get("file_path", "")),
                        start_line=int(s.get("start_line", 0)),
                        end_line=int(s.get("end_line", 0)),
                        file_url=str(s.get("file_url", "")),
                        note=str(s.get("note", "") or ""),
                        original_code=str(s.get("original_code", "") or ""),
                        last_seen_code=str(s.get("last_seen_code", "") or ""),
                        anchor=str(s.get("anchor", "") or ""),
                        anchor_regex=str(s.get("anchor_regex", "") or ""),
                        context_lines=int(s.get("context_lines", 3) or 0),
                        enabled=bool(s.get("enabled", True)),
                        interval_seconds=int(s.get("interval_seconds", 0) or 0),
                        last_commit_sha=str(s.get("last_commit_sha", "") or ""),
                        etag=str(s.get("etag", "") or ""),
                        provider=str(s.get("provider", "github") or "github"),
                        host=str(s.get("host", "github.com") or "github.com"),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"snippets[{i}] has invalid types: {exc}") from exc
        try:
            smtp_port = int(data.get("smtp_port", 587) or 587)
        except (TypeError, ValueError) as exc:
            raise ConfigError("smtp_port must be an integer") from exc
        try:
            interval = int(data.get("interval_seconds", 300) or 300)
        except (TypeError, ValueError) as exc:
            raise ConfigError("interval_seconds must be an integer") from exc
        try:
            cooldown = int(data.get("notify_cooldown_seconds", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ConfigError("notify_cooldown_seconds must be an integer") from exc
        return AppConfig(
            webhook_url=str(data.get("webhook_url", "") or ""),
            telegram_bot_token=str(data.get("telegram_bot_token", "") or ""),
            telegram_chat_id=str(data.get("telegram_chat_id", "") or ""),
            interval_seconds=interval,
            ollama_endpoint=str(data.get("ollama_endpoint", "") or ""),
            ollama_model=str(data.get("ollama_model", "") or ""),
            gemini_api_key=str(data.get("gemini_api_key", "") or ""),
            gemini_model=str(data.get("gemini_model", "") or ""),
            openai_key=str(data.get("openai_key", "") or ""),
            openai_model=str(data.get("openai_model", "") or ""),
            github_token=str(data.get("github_token", "") or ""),
            gitlab_token=str(data.get("gitlab_token", "") or ""),
            gitea_token=str(data.get("gitea_token", "") or ""),
            bitbucket_username=str(data.get("bitbucket_username", "") or ""),
            bitbucket_app_password=str(data.get("bitbucket_app_password", "") or ""),
            slack_webhook_url=str(data.get("slack_webhook_url", "") or ""),
            generic_webhook_url=str(data.get("generic_webhook_url", "") or ""),
            smtp_host=str(data.get("smtp_host", "") or ""),
            smtp_port=smtp_port,
            smtp_user=str(data.get("smtp_user", "") or ""),
            smtp_password=str(data.get("smtp_password", "") or ""),
            email_from=str(data.get("email_from", "") or ""),
            email_to=str(data.get("email_to", "") or ""),
            email_use_tls=bool(data.get("email_use_tls", True)),
            notify_cooldown_seconds=cooldown,
            history_db_path=str(data.get("history_db_path", "") or ""),
            snippets=snippets,
        )

    def find_snippet_by_url(self, url: str) -> SnippetConfig | None:
        for s in self.snippets or []:
            if s.file_url == url:
                return s
        return None

    def remove_snippet_by_url(self, url: str) -> bool:
        if not self.snippets:
            return False
        original_len = len(self.snippets)
        self.snippets = [s for s in self.snippets if s.file_url != url]
        return len(self.snippets) != original_len


@dataclass
class ChangeContext:
    """Enrichment attached to a detected change (commit, severity)."""

    commit_sha: str = ""
    commit_url: str = ""
    commit_message: str = ""
    commit_author: str = ""
