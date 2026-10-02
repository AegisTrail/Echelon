# Changelog

All notable changes to Echelon. Versions follow SemVer; `_version.py` is the
single source of truth (pyproject reads it, `echelon --version` reports it).

## [0.3.0] - 2026-10-02

Added (multi-provider upgrade):

- Any git provider, auto-detected from the URL: GitHub (+Enterprise),
  GitLab (incl. self-hosted and subgroups), Gitea/Codeberg (incl. self-hosted),
  Bitbucket Cloud, SourceHut, plus plain-text raw URLs.
- Slash-safe branches (`feature/x`) resolved at `--add` time, longest-first.
- Per-provider commit enrichment (GitHub/GHE, GitLab, Gitea, Bitbucket).
- Provider tokens: `gitlab_token`, `gitea_token`, `bitbucket_username` +
  `bitbucket_app_password` (config + `ECHELON_*` env + `--init` + `--set-token`).
- Hardening: HTTP retry with backoff and `Retry-After`, daemon SIGTERM
  handling, failure backoff, PID lock against duplicate daemons, config `.bak`
  on save, `--check --json`, `--version`.
- Release pipeline: PyInstaller binaries (Linux/macOS/Windows) attached to
  GitHub Releases on `v*` tags, plus CI (ruff + pytest).

Changed:

- `SnippetConfig` gains `provider`/`host`; snippet IDs now include them.
- `--list` shows `[provider]`; `--add` prints provider + resolved branch/path.
- Repo is pure ASCII (no em dashes or other fancy Unicode).

## [0.2.0] - earlier

- Drift-resistant monitoring (anchors + fuzzy re-alignment).
- Fan-out notifications (Discord, Telegram, Slack, Email, generic webhook).
- SQLite audit history, validated config schema, secrets handling.
