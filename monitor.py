"""Monitoring loop: drift-aware, ETag-efficient, commit-enriched, history-backed."""

from __future__ import annotations

import difflib
import hashlib
import signal
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol

from config_manager import ConfigManager
from drift import resolve_snippet
from gemini_client import GeminiClient
from github_client import GitHubError
from history import default_db_path, record_change
from models import SnippetConfig
from ollama_client import OllamaClient
from openai_client import OpenAIClient
from providers import parse_any_url
from remote_client import RemoteClient


def hash_str(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:10]


class Notifier(Protocol):
    def notify_change(
        self,
        snippet: SnippetConfig,
        diff_text: str,
        diff_summary: str | None = None,
        diff_source: str | None = None,
        *,
        commit_sha: str = "",
        commit_url: str = "",
        commit_message: str = "",
    ) -> None: ...


def build_diff(old: str, new: str) -> str:
    raw = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile="last_snippet",
            tofile="new_snippet",
            lineterm="",
        )
    )
    filtered = [
        ln
        for ln in raw
        if not (ln.startswith("--- ") or ln.startswith("+++ "))
        and (ln.startswith("@@") or ln.startswith("+") or ln.startswith("-"))
    ]
    return "\n".join(filtered).strip()


class SnippetMonitor:
    def __init__(
        self,
        config_manager: ConfigManager,
        remote_client: RemoteClient,
        notifier: Notifier | None,
        debug: bool = False,
        provider: str | None = None,
        model: str | None = None,
        notify: bool = True,
    ):
        self.config_manager = config_manager
        self.remote_client = remote_client
        self.notifier = notifier
        self.debug = debug
        self.provider = (provider or "").lower() or None
        self.model = model
        self.notify = notify
        self._last_check: dict[str, float] = {}
        self._last_alert: dict[str, tuple[str, float]] = {}  # snippet_id -> (diff_hash, ts)
        self._stop = False

    # ---------- AI ----------

    def _ai_clients(self, config: Any) -> tuple[Any, Any, Any, str | None]:
        provider = (self.provider or "").lower()
        model = self.model
        ollama_client = gemini_client = openai_client = None
        if provider == "ollama":
            endpoint, model = config.ollama_endpoint, model or config.ollama_model
            if endpoint and model:
                ollama_client = OllamaClient(endpoint=endpoint, model=model)
        elif provider == "gemini":
            key, model = config.gemini_api_key, model or config.gemini_model
            if key and model:
                gemini_client = GeminiClient(api_key=key, model=model, endpoint=None)
        elif provider == "openai":
            key, model = config.openai_key, model or config.openai_model
            if key and model:
                openai_client = OpenAIClient(api_key=key, model=model, endpoint=None)
        return ollama_client, gemini_client, openai_client, model

    def _summarize(self, clients: tuple, diff_text: str) -> tuple[str | None, str | None]:
        ollama_client, gemini_client, openai_client, _ = clients
        # Priority: explicitly selected provider only (avoids surprise spend).
        if openai_client:
            return openai_client.summarize_diff(diff_text), "OpenAI"
        if gemini_client:
            return gemini_client.summarize_diff(diff_text), "Gemini"
        if ollama_client:
            return ollama_client.summarize_diff(diff_text), "Ollama"
        return None, None

    def _cooldown_ok(self, snippet_id: str, diff_hash: str, cooldown: int) -> bool:
        if not cooldown:
            return True
        prev = self._last_alert.get(snippet_id)
        if prev and prev[0] == diff_hash and (time.time() - prev[1]) < cooldown:
            return False
        return True

    # ---------- main pass ----------

    def request_stop(self) -> None:
        self._stop = True

    def _install_signal_handlers(self) -> None:
        def _handle(signum, _frame):
            print(f"Received signal {signum}; stopping after this pass.")
            self._stop = True

        for name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handle)
            except (OSError, ValueError):
                pass  # not the main thread or unsupported platform

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep in 1s slices; return False if a stop was requested."""
        deadline = time.time() + max(0.0, seconds)
        while time.time() < deadline:
            if self._stop:
                return False
            time.sleep(min(1.0, deadline - time.time()))
        return not self._stop

    def run_forever(self) -> None:
        self._install_signal_handlers()
        fail_streak = 0
        while not self._stop:
            try:
                stats = self.run_once()
            except KeyboardInterrupt:
                print("Monitoring interrupted by user.")
                break
            except Exception as e:
                print(f"Unexpected error during monitoring loop: {e}")
                stats = {"checked": 0, "changed": 0, "drifted": 0, "skipped": 0, "errors": 1}
            if self._stop:
                break
            config = self.config_manager.load()
            interval = max(5, config.interval_seconds or 300)
            # Total outage (nothing checked, only errors): back off to avoid
            # hammering a dead network, capped at 8x interval / 1 hour.
            if stats["checked"] == 0 and stats["errors"] > 0:
                fail_streak += 1
                interval = min(interval * min(2**fail_streak, 8), 3600)
                print(f"All checks failed ({fail_streak} in a row); backing off to {interval}s.")
            else:
                fail_streak = 0
            print(f"Sleeping for {interval} seconds...")
            try:
                if not self._sleep_interruptible(interval):
                    break
            except KeyboardInterrupt:
                print("Monitoring interrupted by user.")
                break
        print("Monitoring stopped.")

    def run_once(self, *, notify: bool | None = None) -> dict[str, int]:
        do_notify = self.notify if notify is None else notify
        config = self.config_manager.load()
        stats = {"checked": 0, "changed": 0, "drifted": 0, "skipped": 0, "errors": 0}
        if not config.snippets:
            print("No snippets configured; nothing to monitor.")
            return stats
        # Long-running daemon picks up tokens added mid-run (env/config).
        self.remote_client.update_tokens(
            {
                "github_token": config.github_token,
                "gitlab_token": config.gitlab_token,
                "gitea_token": config.gitea_token,
                "bitbucket_username": config.bitbucket_username,
                "bitbucket_app_password": config.bitbucket_app_password,
            }
        )

        clients = self._ai_clients(config)
        if self.debug:
            print(
                f"[DEBUG] provider={self.provider} model={clients[3]} snippets={len(config.snippets)}"
            )

        now = time.time()
        db_path = default_db_path(self.config_manager.path, config.history_db_path)
        updated = False

        # Parallel fetch (bounded) for speed; process results in config order.
        def _fetch(snippet: SnippetConfig) -> tuple[str, Any]:
            try:
                parsed = parse_any_url(snippet.file_url)
                res = self.remote_client.fetch(parsed, etag=snippet.etag or "")
                return ("ok", (parsed, res))
            except Exception as exc:  # noqa: BLE001 - per-snippet isolation
                return ("error", exc)

        order = [s for s in config.snippets if s.enabled]
        stats["skipped"] += len(config.snippets) - len(order)
        # Per-snippet custom intervals.
        due: list[SnippetConfig] = []
        for s in order:
            if s.interval_seconds:
                last = self._last_check.get(s.id, 0.0)
                if now - last < s.interval_seconds:
                    stats["skipped"] += 1
                    continue
            due.append(s)

        results: dict[str, tuple[str, Any]] = {}
        if due:
            with ThreadPoolExecutor(max_workers=min(8, len(due))) as pool:
                future_map = {pool.submit(_fetch, s): s for s in due}
                for fut in as_completed(future_map):
                    results[future_map[fut].id] = fut.result()

        for snippet in due:
            self._last_check[snippet.id] = now
            status, payload = results.get(snippet.id, ("error", RuntimeError("fetch missing")))
            if status == "error":
                stats["errors"] += 1
                print(f"Error while checking snippet {snippet.file_url}: {payload}")
                continue
            parsed, res = payload
            try:
                if res.not_modified:
                    if self.debug:
                        print(f"[DEBUG] 304 not modified: {snippet.file_url}")
                    stats["checked"] += 1
                    continue
                if res.etag:
                    snippet.etag = res.etag

                resolved = resolve_snippet(
                    res.content,
                    snippet.start_line,
                    snippet.end_line,
                    snippet.last_seen_code or "",
                    anchor=snippet.anchor or "",
                    anchor_regex=snippet.anchor_regex or "",
                    context_lines=snippet.context_lines,
                )
                new_code = resolved.code
                if resolved.drifted and self.debug:
                    print(
                        f"[DEBUG] drift {snippet.file_url}: L{snippet.start_line} -> L{resolved.start_line} ({resolved.method})"
                    )
                stats["checked"] += 1

                if self.debug:
                    print(
                        f"[DEBUG] Checking {snippet.file_url}\n"
                        f"        last_seen hash = {hash_str(snippet.last_seen_code)}\n"
                        f"        new hash       = {hash_str(new_code)}"
                    )

                if not snippet.last_seen_code:
                    snippet.original_code = new_code
                    snippet.last_seen_code = new_code
                    snippet.start_line, snippet.end_line = resolved.start_line, resolved.end_line
                    updated = True
                    print(f"Initialized snippet baseline for {snippet.file_url}")
                    continue

                # Drift with identical content: silently re-anchor, no alert.
                if new_code == snippet.last_seen_code:
                    if resolved.drifted:
                        snippet.start_line, snippet.end_line = (
                            resolved.start_line,
                            resolved.end_line,
                        )
                        updated = True
                        stats["drifted"] += 1
                        print(
                            f"Re-anchored (moved, content same): {snippet.file_url} -> L{resolved.start_line}-L{resolved.end_line}"
                        )
                    else:
                        print(f"No change in {snippet.file_url}")
                    continue

                # Real content change.
                print(f"Change detected in {snippet.file_url}")
                last_code = snippet.last_seen_code
                diff_text = build_diff(last_code, new_code)
                diff_summary, diff_source = self._summarize(clients, diff_text)

                # Commit enrichment (best-effort, never fatal).
                commit_sha = commit_url = commit_msg = ""
                try:
                    info = self.remote_client.get_latest_commit(parsed)
                    if info and info.sha:
                        commit_sha, commit_url, commit_msg = info.sha, info.url, info.message
                        snippet.last_commit_sha = info.sha
                except Exception as exc:  # noqa: BLE001
                    if self.debug:
                        print(f"[DEBUG] commit lookup failed: {exc}")

                diff_hash = hash_str(diff_text)
                if not self._cooldown_ok(
                    snippet.id, diff_hash, config.notify_cooldown_seconds or 0
                ):
                    print(f"Cooldown active, alert suppressed for {snippet.file_url}")
                elif do_notify and self.notifier is not None:
                    try:
                        self.notifier.notify_change(
                            snippet,
                            diff_text,
                            diff_summary=diff_summary,
                            diff_source=diff_source,
                            commit_sha=commit_sha,
                            commit_url=commit_url,
                            commit_message=commit_msg,
                        )
                        self._last_alert[snippet.id] = (diff_hash, time.time())
                    except Exception as exc:  # noqa: BLE001
                        print(f"Error sending notification: {exc}")

                try:
                    record_change(
                        db_path,
                        snippet_id=snippet.id,
                        file_url=snippet.file_url,
                        old_code=last_code,
                        new_code=new_code,
                        diff=diff_text,
                        summary=diff_summary or "",
                        commit_sha=commit_sha,
                        commit_url=commit_url,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"Warning: could not write history db: {exc}")

                snippet.last_seen_code = new_code
                snippet.start_line, snippet.end_line = resolved.start_line, resolved.end_line
                updated = True
                stats["changed"] += 1
            except (GitHubError, ValueError) as exc:
                stats["errors"] += 1
                print(f"Error while checking snippet {snippet.file_url}: {exc}")
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"Error while checking snippet {snippet.file_url}: {exc}")

        if updated:
            try:
                self.config_manager.save(config)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: could not save config: {exc}")
        return stats
