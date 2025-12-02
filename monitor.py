import time
import hashlib
import difflib
from typing import Optional

from config_manager import ConfigManager
from github_client import GitHubClient
from notifier import DiscordNotifier
from ollama_client import OllamaClient
from gemini_client import GeminiClient
from openai_client import OpenAIClient


def hash_str(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:10]


class SnippetMonitor:
    def __init__(
        self,
        config_manager: ConfigManager,
        github_client: GitHubClient,
        notifier: DiscordNotifier,
        debug: bool = False,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.config_manager = config_manager
        self.github_client = github_client
        self.notifier = notifier
        self.debug = debug
        self.provider = provider
        self.model = model

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                print("Monitoring interrupted by user.")
                break
            except Exception as e:
                print(f"Unexpected error during monitoring loop: {e}")
            config = self.config_manager.load()
            interval = max(5, config.interval_seconds or 300)
            print(f"Sleeping for {interval} seconds...")
            time.sleep(interval)

    def run_once(self) -> None:
        config = self.config_manager.load()
        if not config.snippets:
            print("No snippets configured; nothing to monitor.")
            return

        webhook = config.webhook_url
        if not webhook:
            print("No webhook configured in config.json; cannot send alerts.")
            return

        provider = (self.provider or "").lower()
        model = self.model

        ollama_client = None
        gemini_client = None
        openai_client = None

        if provider == "ollama":
            endpoint = config.ollama_endpoint
            model = model or config.ollama_model
            if endpoint and model:
                ollama_client = OllamaClient(endpoint=endpoint, model=model)
        elif provider == "gemini":
            key = config.gemini_api_key
            model = model or config.gemini_model
            if key and model:
                gemini_client = GeminiClient(api_key=key, model=model, endpoint=None)
        elif provider == "openai":
            key = config.openai_key
            model = model or config.openai_model
            if key and model:
                openai_client = OpenAIClient(api_key=key, model=model, endpoint=None)

        if self.debug:
            print(f"[DEBUG] provider={provider} model={model} webhook={bool(webhook)}")

        updated = False
        for snippet in config.snippets:
            try:
                parsed = self.github_client.parse_github_url(snippet.file_url)
                content = self.github_client.fetch_file_content(parsed)
                new_code = self.github_client.extract_lines(content, parsed.start_line, parsed.end_line)

                if self.debug:
                    print(
                        f"[DEBUG] Checking {snippet.file_url}\n"
                        f"        last_seen hash = {hash_str(snippet.last_seen_code)}\n"
                        f"        new hash       = {hash_str(new_code)}"
                    )

                if not snippet.last_seen_code:
                    snippet.original_code = new_code
                    snippet.last_seen_code = new_code
                    updated = True
                    print(f"Initialized snippet baseline for {snippet.file_url}")
                    continue

                if new_code != snippet.last_seen_code:
                    print(f"Change detected in {snippet.file_url}")

                    last_code = snippet.last_seen_code

                    raw_diff_lines = list(
                        difflib.unified_diff(
                            last_code.splitlines(),
                            new_code.splitlines(),
                            fromfile="last_snippet",
                            tofile="new_snippet",
                            lineterm="",
                        )
                    )

                    filtered_lines = []
                    for line in raw_diff_lines:
                        if line.startswith('--- ') or line.startswith('+++ '):
                            continue
                        if line.startswith('@@'):
                            filtered_lines.append(line)
                        elif line.startswith('+') or line.startswith('-'):
                            filtered_lines.append(line)
                    diff_text = "\n".join(filtered_lines).strip()

                    diff_summary = None
                    diff_source = None

                    if openai_client:
                        diff_summary = openai_client.summarize_diff(diff_text)
                        diff_source = "OpenAI"
                    elif gemini_client:
                        diff_summary = gemini_client.summarize_diff(diff_text)
                        diff_source = "Gemini"
                    elif ollama_client:
                        diff_summary = ollama_client.summarize_diff(diff_text)
                        diff_source = "Ollama"

                    self.notifier.notify_change(
                        webhook,
                        snippet,
                        diff_text,
                        diff_summary=diff_summary,
                        diff_source=diff_source,
                    )

                    snippet.last_seen_code = new_code
                    updated = True
                else:
                    print(f"No change in {snippet.file_url}")

            except Exception as e:
                print(f"Error while checking snippet {snippet.file_url}: {e}")

        if updated:
            self.config_manager.save(config)
