"""Echelon CLI: monitor code line ranges on any git provider + fan-out alerts."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys

from _version import __version__
from app_secrets import mask_secret, scan_file
from config_manager import ConfigManager
from discord import DiscordNotifier
from email_notifier import EmailNotifier, GenericWebhookNotifier
from fanout import FanoutNotifier
from history import default_db_path, list_changes
from models import AppConfig, ConfigError, SnippetConfig
from monitor import SnippetMonitor
from providers import parse_any_url
from remote_client import RemoteClient, extract_lines
from slack_notifier import SlackNotifier
from telegram import TelegramNotifier
from utils import snippet_id_from_parsed

VALID_NOTIFY = ("discord", "telegram", "slack", "email", "webhook")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Echelon - Monitor code file line ranges on any git provider and notify with AI summaries.",
    )
    parser.add_argument(
        "--config",
        help="Path to config.json (default: ./config.json or ~/.config/echelon/config.json). Env: ECHELON_CONFIG",
    )

    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--add",
        help='Add snippet: GitHub / GitLab / Gitea / Bitbucket / SourceHut file URL, or any raw-text URL, with a #Lx-Ly fragment. Example: "https://github.com/owner/repo/blob/main/path/file.py#L26-L31"',
    )
    action.add_argument("--remove", help="Remove a snippet by its URL.")
    action.add_argument(
        "--list",
        dest="list_snippets",
        action="store_true",
        help="List monitored snippets (secrets masked).",
    )
    action.add_argument(
        "--check",
        "--once",
        dest="check",
        action="store_true",
        help="Run a single check pass and exit (cron-friendly).",
    )
    action.add_argument("--run", action="store_true", help="Start monitoring daemon.")
    action.add_argument(
        "--init", action="store_true", help="Interactively configure API keys / webhooks."
    )
    action.add_argument(
        "--log",
        nargs="?",
        const="",
        default=None,
        help="Show audit history. Optionally filter by snippet URL: --log <url>",
    )
    action.add_argument(
        "--export",
        metavar="PATH",
        help="Export config to PATH (secrets stripped unless --include-secrets).",
    )
    action.add_argument(
        "--import", dest="import_path", metavar="PATH", help="Import snippets/config from PATH."
    )
    action.add_argument("--enable", metavar="URL", help="Enable monitoring for a snippet URL.")
    action.add_argument(
        "--disable", metavar="URL", help="Disable monitoring for a snippet URL (kept, not deleted)."
    )
    action.add_argument(
        "--set-note", metavar="URL", help="Update the note for a snippet URL (use with --note)."
    )
    action.add_argument(
        "--set-github-token",
        dest="set_github_token",
        action="store_true",
        help="Securely store a GitHub token (prompted, never via shell arg).",
    )
    action.add_argument(
        "--set-token",
        dest="set_auth",
        choices=("github", "gitlab", "gitea", "bitbucket"),
        metavar="PROVIDER",
        help="Securely store a provider token (prompted): github | gitlab | gitea | bitbucket.",
    )
    action.add_argument(
        "--check-secrets",
        dest="check_secrets",
        action="store_true",
        help="Scan tracked files for accidentally committed secrets.",
    )

    parser.add_argument("--note", help="Note for --add / --set-note.")
    parser.add_argument(
        "--anchor",
        default="",
        help="Literal anchor text to track (drift-resistant). Used with --add.",
    )
    parser.add_argument(
        "--anchor-regex",
        default="",
        dest="anchor_regex",
        help="Regex anchor to track. Used with --add.",
    )
    parser.add_argument(
        "--context", type=int, default=3, help="Context lines kept around anchor (0-20, default 3)."
    )
    parser.add_argument(
        "--snippet-interval",
        type=int,
        default=0,
        dest="snippet_interval",
        help="Per-snippet poll interval in seconds (0 = inherit global).",
    )
    parser.add_argument("--time", type=int, help="Global polling interval (seconds).")
    parser.add_argument("--ai", help="AI provider for diff summaries: gemini | openai | ollama")
    parser.add_argument("--model", help="Model name for the selected provider.")
    parser.add_argument(
        "--notify",
        action="append",
        choices=VALID_NOTIFY,
        default=None,
        help="Channel(s) to notify; repeatable. Default: all configured.",
    )
    parser.add_argument(
        "--discord",
        action="store_true",
        help="Send notifications via Discord webhook (legacy; prefer --notify discord).",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send notifications via Telegram bot (legacy; prefer --notify telegram).",
    )
    parser.add_argument(
        "--no-notify",
        dest="no_notify",
        action="store_true",
        help="Check without sending notifications (dry run).",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max rows for --log (default 20).")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Machine-readable output for --list / --log / --check.",
    )
    parser.add_argument(
        "--include-secrets",
        dest="include_secrets",
        action="store_true",
        help="Include secrets in --export (chmod 0600, handle with care).",
    )
    parser.add_argument("--debug", action="store_true", help="Verbose debug output.")
    parser.add_argument(
        "--no-lock",
        dest="no_lock",
        action="store_true",
        help="Skip the PID lock (allows parallel runs; risks duplicate alerts).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# ---------- init ----------


def _prompt_secret(prompt: str) -> str:
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def prompt_if_missing(config_manager: ConfigManager) -> None:
    config = config_manager.load(validate=False)
    changed = False

    print(
        "Interactive configuration. Press Enter to skip. Secrets are hidden and stored with mode 0600.\n"
    )

    def set_if_missing(attr: str, prompt: str, secret: bool = True) -> None:
        nonlocal changed
        current = getattr(config, attr) or ""
        if current:
            print(f"{attr} already set ({mask_secret(current) if secret else current})")
            return
        val = (
            _prompt_secret(f"{prompt} [skip]: ") if secret else input(f"{prompt} [skip]: ").strip()
        )
        if val:
            setattr(config, attr, val)
            changed = True
            print(f"Saved {attr}")

    set_if_missing("webhook_url", "Discord webhook URL")
    set_if_missing("telegram_bot_token", "Telegram bot token")
    if not config.telegram_chat_id:
        val = input("Telegram chat id (e.g. -100123...) [skip]: ").strip()
        if val:
            config.telegram_chat_id = val
            changed = True
    else:
        print("telegram_chat_id already set")
    set_if_missing(
        "github_token", "GitHub token (github.com + Enterprise; private repos / rate limits)"
    )
    set_if_missing("gitlab_token", "GitLab token (gitlab.com / self-hosted; private projects)")
    set_if_missing("gitea_token", "Gitea/Codeberg token (private repos)")
    set_if_missing(
        "bitbucket_username", "Bitbucket username (private repos; pairs with app password)"
    )
    set_if_missing("bitbucket_app_password", "Bitbucket app password")
    set_if_missing("slack_webhook_url", "Slack webhook URL")
    set_if_missing("openai_key", "OpenAI API key")
    if not config.openai_model:
        val = input("Default OpenAI model (e.g. gpt-4o-mini) [skip]: ").strip()
        if val:
            config.openai_model = val
            changed = True
    else:
        print("openai_model already set")
    set_if_missing("gemini_api_key", "Gemini API key")
    if not config.gemini_model:
        val = input("Default Gemini model (e.g. gemini-2.5-flash) [skip]: ").strip()
        if val:
            config.gemini_model = val
            changed = True
    else:
        print("gemini_model already set")
    if not config.ollama_endpoint:
        val = input("Ollama endpoint (e.g. http://localhost:11434) [skip]: ").strip()
        if val:
            config.ollama_endpoint = val
            changed = True
    else:
        print("ollama_endpoint already set")
    if not config.ollama_model:
        val = input("Default Ollama model (e.g. llama3.1) [skip]: ").strip()
        if val:
            config.ollama_model = val
            changed = True
    else:
        print("ollama_model already set")

    if changed:
        try:
            config.validate()
        except ConfigError as exc:
            print(f"Validation error: {exc}")
            return
        config_manager.save(config)
        print(f"\nConfiguration saved to {config_manager.path} (mode 0600).")
    else:
        print("\nNo changes made.")


# ---------- snippet ops ----------


def handle_add(args, config_manager: ConfigManager, remote_client: RemoteClient) -> int:
    config = config_manager.load(validate=False)
    try:
        parsed = parse_any_url(args.add)
    except ConfigError as exc:
        print(f"Invalid URL: {exc}")
        return 2
    if config.find_snippet_by_url(parsed.file_url):
        print(f"Snippet already configured: {parsed.file_url}")
        return 1
    if args.anchor_regex:
        import re as _re

        try:
            _re.compile(args.anchor_regex)
        except _re.error as exc:
            print(f"Invalid --anchor-regex: {exc}")
            return 2
    if not 0 <= args.context <= 20:
        print("--context must be 0..20")
        return 2
    try:
        # Resolve branch/path (slash-branches probed longest-first), fetch content.
        branch, file_path, content = remote_client.resolve(parsed)
        snippet_text = extract_lines(content, parsed.start_line, parsed.end_line)
    except ConfigError as exc:
        print(f"Invalid URL: {exc}")
        return 2
    except Exception as exc:
        print(f"Failed to fetch file: {exc}")
        return 1
    snippet_id = snippet_id_from_parsed(parsed)
    new_snippet = SnippetConfig(
        id=snippet_id,
        owner=parsed.owner,
        repo=parsed.repo,
        branch=branch,
        file_path=file_path,
        start_line=parsed.start_line,
        end_line=parsed.end_line,
        file_url=parsed.file_url,
        note=args.note or "",
        original_code=snippet_text,
        last_seen_code=snippet_text,
        provider=parsed.provider,
        host=parsed.host,
        anchor=(args.anchor or "").strip(),
        anchor_regex=(args.anchor_regex or "").strip(),
        context_lines=args.context,
        enabled=True,
        interval_seconds=args.snippet_interval or 0,
    )
    try:
        new_snippet.validate()
    except ConfigError as exc:
        print(f"Invalid snippet: {exc}")
        return 2
    if config.snippets is None:
        config.snippets = []
    config.snippets.append(new_snippet)
    try:
        config_manager.save(config)
    except ConfigError as exc:
        print(f"Cannot save: {exc}")
        return 2
    print("Added snippet (monitoring starts on --run/--check):")
    print(f"  {parsed.file_url}")
    print(f"  Provider: {parsed.provider} ({parsed.host})")
    if branch != parsed.branch or file_path != parsed.file_path:
        print(f"  Resolved: branch={branch} path={file_path}")
    if new_snippet.note:
        print(f"  Note: {new_snippet.note}")
    if new_snippet.anchor or new_snippet.anchor_regex:
        print(f"  Anchor: {new_snippet.anchor or new_snippet.anchor_regex} (drift-resistant)")
    return 0


def handle_remove(args, config_manager: ConfigManager) -> int:
    config = config_manager.load(validate=False)
    if config.remove_snippet_by_url(args.remove):
        config_manager.save(config)
        print(f"Removed snippet: {args.remove}")
        return 0
    print(f"No matching snippet found for: {args.remove}")
    return 1


def handle_list(args, config_manager: ConfigManager) -> int:
    config = config_manager.load()
    snippets = config.snippets or []
    if args.as_json:
        print(json.dumps(config.to_dict(include_secrets=False)["snippets"], indent=2))
        return 0
    if not snippets:
        print("No snippets configured. Add one with --add.")
        return 0
    for i, s in enumerate(snippets, 1):
        state = "enabled" if s.enabled else "disabled"
        print(f"{i}. [{state}] [{s.provider}] {s.file_url}")
        detail = f"lines L{s.start_line}-L{s.end_line}"
        if s.branch:
            detail += f" branch={s.branch}"
        detail += f" note={s.note or '-'}"
        print(f"   {detail}")
        if s.anchor or s.anchor_regex:
            print(f"   anchor={s.anchor or s.anchor_regex} context={s.context_lines}")
    print(
        f"\nConfig: {config_manager.path}  interval={config.interval_seconds}s  snippets={len(snippets)}"
    )
    return 0


def handle_enable_disable(args, config_manager: ConfigManager, enable: bool) -> int:
    url = args.enable if enable else args.disable
    config = config_manager.load(validate=False)
    snippet = config.find_snippet_by_url(url)
    if not snippet:
        print(f"No matching snippet found for: {url}")
        return 1
    snippet.enabled = enable
    config_manager.save(config)
    print(f"{'Enabled' if enable else 'Disabled'}: {url}")
    return 0


def handle_set_note(args, config_manager: ConfigManager) -> int:
    if args.note is None:
        print('--set-note requires --note "text"')
        return 2
    if len(args.note) > 500:
        print("--note too long (max 500 chars)")
        return 2
    config = config_manager.load(validate=False)
    snippet = config.find_snippet_by_url(args.set_note)
    if not snippet:
        print(f"No matching snippet found for: {args.set_note}")
        return 1
    snippet.note = args.note
    config_manager.save(config)
    print(f"Updated note for: {args.set_note}")
    return 0


def handle_export(args, config_manager: ConfigManager) -> int:
    config = config_manager.load()
    data = config.to_dict(include_secrets=bool(args.include_secrets))
    dest = os.path.abspath(os.path.expanduser(args.export))
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    if args.include_secrets:
        os.chmod(dest, 0o600)
        print(f"Exported WITH secrets to {dest} (mode 0600; handle with care).")
    else:
        print(f"Exported (secrets stripped) to {dest}.")
    return 0


def handle_import(args, config_manager: ConfigManager) -> int:
    src = os.path.abspath(os.path.expanduser(args.import_path))
    try:
        with open(src, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot import {src}: {exc}")
        return 1
    try:
        incoming = AppConfig.from_dict(data)
        incoming.validate()
    except ConfigError as exc:
        print(f"Import validation failed: {exc}")
        return 2
    config = config_manager.load(validate=False)
    added = 0
    for s in incoming.snippets or []:
        if not config.find_snippet_by_url(s.file_url):
            config.snippets.append(s)
            added += 1
    # Import global settings only for empty fields (never overwrite secrets silently).
    for attr in (
        "interval_seconds",
        "ollama_endpoint",
        "ollama_model",
        "gemini_model",
        "openai_model",
    ):
        if not getattr(config, attr) and getattr(incoming, attr):
            setattr(config, attr, getattr(incoming, attr))
    try:
        config_manager.save(config)
    except ConfigError as exc:
        print(f"Cannot save: {exc}")
        return 2
    print(f"Imported {added} new snippet(s) from {src}.")
    return 0


def handle_log(args, config_manager: ConfigManager) -> int:
    config = config_manager.load()
    db_path = default_db_path(config_manager.path, config.history_db_path)
    limit = max(1, min(args.limit or 20, 200))
    url = (args.log or "").strip()
    records = list_changes(db_path, file_url=url, limit=limit)
    if args.as_json:
        print(json.dumps([r.__dict__ for r in records], indent=2))
        return 0
    if not records:
        print("No history yet. Changes will appear here after --run/--check detects them.")
        return 0
    for r in records:
        print(f"#{r.id} {r.checked_at} {r.file_url}")
        if r.commit_sha:
            print(f"  commit {r.commit_sha[:12]} {r.commit_url}")
        if r.summary:
            print(f"  summary: {r.summary[:300]}")
    return 0


def handle_check_secrets(args) -> int:
    _ = args
    # Scan tracked files (git) or all source files as fallback.
    files: list[str] = []
    try:
        git_bin = shutil.which("git")
        if git_bin:
            out = subprocess.run([git_bin, "ls-files"], capture_output=True, text=True, timeout=10)  # noqa: S603 - fixed argv, no shell, binary resolved via shutil.which
            if out.returncode == 0:
                files = [f for f in out.stdout.splitlines() if f.strip()]
    except (OSError, subprocess.SubprocessError):
        files = []
    if not files:
        import glob as _glob

        files = (
            _glob.glob("*.py") + _glob.glob("*.md") + _glob.glob("*.json") + _glob.glob("*.example")
        )
    findings = []
    for f in files:
        if f == "config.json" or f.endswith(".db") or "/.venv/" in f:
            continue
        if os.path.isdir(f):
            continue
        findings.extend(scan_file(f))
    if not findings:
        print(f"Scanned {len(files)} file(s): no committed-secret patterns found.")
        return 0
    print(f"WARNING: {len(findings)} potential secret pattern(s) in tracked files (values masked):")
    for fd in findings[:50]:
        print(f"  [{fd.pattern_name}] {fd.location} -> {fd.preview}")
    print("Rotate any real credentials and remove them from git history.")
    return 1


# ---------- notifiers ----------


def build_notifiers(config: AppConfig, args) -> FanoutNotifier | None:
    selected: list[str] | None = None
    if args.notify:
        selected = list(args.notify)
    elif args.discord:
        selected = ["discord"]
    elif args.telegram:
        selected = ["telegram"]
    else:
        selected = []
        if config.webhook_url:
            selected.append("discord")
        if config.telegram_bot_token and config.telegram_chat_id:
            selected.append("telegram")
        if config.slack_webhook_url:
            selected.append("slack")
        if config.smtp_host and config.email_from and config.email_to:
            selected.append("email")
        if config.generic_webhook_url:
            selected.append("webhook")

    notifiers = []
    for name in selected:
        if name == "discord":
            if not config.webhook_url:
                print(
                    "Channel 'discord' selected but webhook_url is not configured. Run --init or set ECHELON_DISCORD_WEBHOOK."
                )
                return None
            notifiers.append(DiscordNotifier(webhook_url=config.webhook_url))
        elif name == "telegram":
            if not config.telegram_bot_token or not config.telegram_chat_id:
                print(
                    "Channel 'telegram' selected but bot token/chat id missing. Run --init or set ECHELON_TELEGRAM_* vars."
                )
                return None
            notifiers.append(
                TelegramNotifier(
                    bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id
                )
            )
        elif name == "slack":
            if not config.slack_webhook_url:
                print("Channel 'slack' selected but slack_webhook_url is not configured.")
                return None
            notifiers.append(SlackNotifier(webhook_url=config.slack_webhook_url))
        elif name == "email":
            n = EmailNotifier(
                smtp_host=config.smtp_host,
                smtp_port=config.smtp_port,
                smtp_user=config.smtp_user,
                smtp_password=config.smtp_password,
                email_from=config.email_from,
                email_to=config.email_to,
                use_tls=config.email_use_tls,
            )
            if not n.configured:
                print(
                    "Channel 'email' selected but SMTP settings incomplete (host/from/to required)."
                )
                return None
            notifiers.append(n)
        elif name == "webhook":
            if not config.generic_webhook_url:
                print("Channel 'webhook' selected but generic_webhook_url is not configured.")
                return None
            notifiers.append(GenericWebhookNotifier(webhook_url=config.generic_webhook_url))
    return FanoutNotifier(notifiers)


def validate_provider(args, config: AppConfig) -> str | None:
    provider = args.ai.lower() if args.ai else None
    if not provider:
        return None
    if provider == "openai" and not config.openai_key:
        print("OpenAI selected but openai_key missing. Run --init or set ECHELON_OPENAI_KEY.")
        sys.exit(1)
    if provider == "gemini" and not config.gemini_api_key:
        print("Gemini selected but gemini_api_key missing. Run --init or set ECHELON_GEMINI_KEY.")
        sys.exit(1)
    if provider == "ollama" and not config.ollama_endpoint:
        print(
            "Ollama selected but ollama_endpoint missing. Run --init or set ECHELON_OLLAMA_ENDPOINT."
        )
        sys.exit(1)
    if provider not in ("gemini", "openai", "ollama"):
        print("--ai must be one of: gemini | openai | ollama")
        sys.exit(1)
    return provider


def _build_remote_client(
    config_manager: ConfigManager, config: AppConfig | None = None
) -> RemoteClient:
    """RemoteClient wired with tokens from config file + ECHELON_* env."""
    config = config or config_manager.load(validate=False)
    return RemoteClient(
        tokens={
            "github_token": config.github_token,
            "gitlab_token": config.gitlab_token,
            "gitea_token": config.gitea_token,
            "bitbucket_username": config.bitbucket_username,
            "bitbucket_app_password": config.bitbucket_app_password,
        }
    )


def handle_set_auth(provider: str, config_manager: ConfigManager) -> int:
    if provider == "bitbucket":
        username = input("Bitbucket username [skip]: ").strip()
        password = _prompt_secret("Bitbucket app password (input hidden) [skip]: ")
        if not username or not password:
            print("Both username and app password are required; nothing saved.")
            return 0
        try:
            config = config_manager.load(validate=False)
            config.bitbucket_username = username
            config.bitbucket_app_password = password
            config.validate()
            config_manager.save(config)
            print(f"Saved bitbucket credentials to {config_manager.path} (mode 0600).")
        except ConfigError as exc:
            print(f"Validation error: {exc}")
            return 2
        return 0
    attr = {"github": "github_token", "gitlab": "gitlab_token", "gitea": "gitea_token"}[provider]
    token = _prompt_secret(f"{provider.capitalize()} token (input hidden) [skip]: ")
    if not token:
        print("No token entered; nothing saved.")
        return 0
    try:
        config = config_manager.load(validate=False)
        setattr(config, attr, token)
        config.validate()
        config_manager.save(config)
        print(f"Saved {attr} to {config_manager.path} (mode 0600).")
    except ConfigError as exc:
        print(f"Validation error: {exc}")
        return 2
    return 0


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config_manager = ConfigManager(path=args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    if args.init:
        try:
            prompt_if_missing(config_manager)
        except ConfigError as exc:
            print(f"Config error: {exc}")
            return 2
        return 0

    if args.check_secrets:
        return handle_check_secrets(args)

    if args.set_github_token:
        args.set_auth = "github"

    if args.set_auth:
        return handle_set_auth(args.set_auth, config_manager)

    if args.time is not None:
        if args.time <= 0:
            print("--time must be a positive integer")
            return 2
        try:
            config = config_manager.load(validate=False)
            config.interval_seconds = args.time
            config_manager.save(config)
            print(f"Saved interval_seconds={args.time} to {config_manager.path}.")
        except ConfigError as exc:
            print(f"Validation error: {exc}")
            return 2
        if not any([args.add, args.check, args.run, args.list_snippets]):
            return 0

    remote_client = _build_remote_client(config_manager)

    if args.add:
        return handle_add(args, config_manager, remote_client)
    if args.remove:
        return handle_remove(args, config_manager)
    if args.list_snippets:
        try:
            return handle_list(args, config_manager)
        except ConfigError as exc:
            print(f"Config error: {exc}")
            return 2
    if args.enable:
        return handle_enable_disable(args, config_manager, True)
    if args.disable:
        return handle_enable_disable(args, config_manager, False)
    if args.set_note:
        return handle_set_note(args, config_manager)
    if args.export:
        try:
            return handle_export(args, config_manager)
        except ConfigError as exc:
            print(f"Config error: {exc}")
            return 2
    if args.import_path:
        return handle_import(args, config_manager)
    if args.log is not None:
        try:
            return handle_log(args, config_manager)
        except ConfigError as exc:
            print(f"Config error: {exc}")
            return 2

    if not args.run and not args.check:
        parser.print_help()
        return 0

    # --- run / check ---
    try:
        config = config_manager.load()
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 2

    if not config.snippets:
        print("No snippets configured. Add at least one with --add before running.")
        return 1

    provider = validate_provider(args, config)
    do_notify = not args.no_notify
    notifier: FanoutNotifier | None = None
    if do_notify:
        notifier = build_notifiers(config, args)
        if notifier is None:
            return 1
        if len(notifier) == 0:
            print(
                "No notification channels configured. Run --init, set env vars, or use --no-notify for a dry run."
            )
            return 1
    else:
        notifier = FanoutNotifier([])

    # Tokens: config file + ECHELON_* env (env wins via ConfigManager overlay).
    monitor = SnippetMonitor(
        config_manager=config_manager,
        remote_client=_build_remote_client(config_manager, config=config),
        notifier=notifier,
        debug=bool(args.debug),
        provider=provider,
        model=args.model or None,
        notify=do_notify,
    )

    print(f"Echelon v{__version__} ({config_manager.path})")

    # PID lock: never run two notifying passes against the same config, or
    # every change would alert twice. Dry runs (--no-notify) skip the lock.
    lock = None
    if not args.no_lock and (args.run or (args.check and do_notify)):
        from pidlock import LockHeldError, PidLock, default_lock_path

        lock = PidLock(default_lock_path(config_manager.path))
        try:
            lock.acquire()
        except LockHeldError as exc:
            print(f"{exc} Use --no-lock to override (risks duplicate alerts).")
            return 1
        except OSError as exc:
            print(f"Warning: cannot create PID lock ({exc}); continuing unlocked.")

    try:
        if args.check:
            stats = monitor.run_once()
            if args.as_json:
                print(json.dumps({"version": __version__, **stats}, indent=2))
            else:
                print(
                    f"Check complete: {stats['checked']} checked, {stats['changed']} changed, "
                    f"{stats['drifted']} re-anchored, {stats['skipped']} skipped, {stats['errors']} errors."
                )
            return 0 if stats["errors"] == 0 else 1

        print("Starting monitoring daemon... Press Ctrl+C to stop.")
        monitor.run_forever()
        return 0
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    sys.exit(main())
