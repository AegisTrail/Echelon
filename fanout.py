"""Fan-out: deliver one change event to many notifiers, never failing fast."""

from __future__ import annotations

from typing import Any

from models import SnippetConfig


class FanoutNotifier:
    def __init__(self, notifiers: list[Any]):
        self.notifiers = [n for n in notifiers if n is not None]

    def __len__(self) -> int:
        return len(self.notifiers)

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
    ) -> None:
        if not self.notifiers:
            print("No notifiers configured; skipping notification.")
            return
        for notifier in self.notifiers:
            try:
                notifier.notify_change(
                    snippet,
                    diff_text,
                    diff_summary=diff_summary,
                    diff_source=diff_source,
                    commit_sha=commit_sha,
                    commit_url=commit_url,
                    commit_message=commit_message,
                )
            except Exception as e:
                print(f"Notifier {type(notifier).__name__} failed: {e}")
