"""Centralised secret handling: masking, env mapping, leak scanning.

Security rules enforced here:
- Secrets are never printed in full (see :func:`mask_secret`).
- Config files are written with mode 0600 (see config_manager).
- Shell history-safe: tokens are read from env vars or getpass prompts,
  never required as CLI flags.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: Config attribute name -> environment variable name.
SECRET_ENV_VARS: dict[str, str] = {
    "webhook_url": "ECHELON_DISCORD_WEBHOOK",
    "telegram_bot_token": "ECHELON_TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "ECHELON_TELEGRAM_CHAT_ID",
    "github_token": "ECHELON_GITHUB_TOKEN",
    "gitlab_token": "ECHELON_GITLAB_TOKEN",
    "gitea_token": "ECHELON_GITEA_TOKEN",
    "bitbucket_username": "ECHELON_BITBUCKET_USER",
    "bitbucket_app_password": "ECHELON_BITBUCKET_APP_PASSWORD",
    "openai_key": "ECHELON_OPENAI_KEY",
    "openai_model": "ECHELON_OPENAI_MODEL",
    "gemini_api_key": "ECHELON_GEMINI_KEY",
    "gemini_model": "ECHELON_GEMINI_MODEL",
    "ollama_endpoint": "ECHELON_OLLAMA_ENDPOINT",
    "ollama_model": "ECHELON_OLLAMA_MODEL",
    "slack_webhook_url": "ECHELON_SLACK_WEBHOOK",
    "generic_webhook_url": "ECHELON_GENERIC_WEBHOOK",
    "smtp_host": "ECHELON_SMTP_HOST",
    "smtp_port": "ECHELON_SMTP_PORT",
    "smtp_user": "ECHELON_SMTP_USER",
    "smtp_password": "ECHELON_SMTP_PASSWORD",
    "email_from": "ECHELON_EMAIL_FROM",
    "email_to": "ECHELON_EMAIL_TO",
}

#: Substrings marking a config field as secret (for redaction).
SECRET_FIELD_SUBSTRINGS: tuple[str, ...] = (
    "token",
    "api_key",
    "_key",
    "password",
    "secret",
    "webhook",
)


def is_secret_field(name: str) -> bool:
    lowered = name.lower()
    return any(s in lowered for s in SECRET_FIELD_SUBSTRINGS)


def mask_secret(value: str | None) -> str:
    """Return a safely displayable version of a secret.

    Empty -> "(not set)". Short values -> "***". Otherwise first 3 chars
    + "***" (never the full value).
    """
    if not value:
        return "(not set)"
    text = str(value)
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***"


def redact_dict(data: dict, *, keep_empty: bool = True) -> dict:
    """Return a copy of *data* with secret fields masked."""
    redacted: dict = {}
    for key, value in data.items():
        if isinstance(value, dict):
            redacted[key] = redact_dict(value)
        elif isinstance(value, list):
            redacted[key] = [redact_dict(v) if isinstance(v, dict) else v for v in value]
        elif is_secret_field(key):
            redacted[key] = mask_secret(value) if value else ("" if keep_empty else "(not set)")
        else:
            redacted[key] = value
    return redacted


@dataclass
class SecretFinding:
    pattern_name: str
    location: str
    preview: str


# Patterns used by `--check-secrets` to catch accidentally committed keys.
# These are deliberately narrow to avoid false positives.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("discord-webhook", re.compile(r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{10,}")),
    ("telegram-token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9]{20,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("generic-bearer", re.compile(r"(?i)\b(api[_-]?key|secret)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]")),
]


def scan_text(text: str, *, location: str = "<text>") -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for name, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            preview = raw[:4] + "***" + (raw[-2:] if len(raw) > 6 else "")
            findings.append(SecretFinding(pattern_name=name, location=location, preview=preview))
    return findings


def scan_file(path: str) -> list[SecretFinding]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return []
    # Skip scanning the active local config itself: it is *expected* to hold
    # secrets (it is git-ignored). The point of the scanner is to catch
    # secrets in *tracked* files. Callers filter accordingly, but be safe:
    # only report if the file looks like source/docs, not config.json.
    basename = os.path.basename(path)
    if basename == "config.json":
        return []
    return scan_text(content, location=path)
