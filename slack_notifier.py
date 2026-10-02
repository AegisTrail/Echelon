"""Slack incoming-webhook notifier."""

from __future__ import annotations

import requests

from models import SnippetConfig


class SlackNotifier:
    def __init__(self, webhook_url: str, timeout: int = 10):
        self.webhook_url = (webhook_url or "").strip()
        self.timeout = timeout

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
        if not self.webhook_url:
            print("No Slack webhook configured; skipping Slack notification.")
            return
        header = f"*Code change detected in {snippet.owner}/{snippet.repo}*"
        lines = [
            header,
            f"File: {snippet.file_url}",
            f"Lines: L{snippet.start_line}-L{snippet.end_line}",
            f"Note: {snippet.note or '-'}",
        ]
        if commit_sha or commit_url:
            short = commit_sha[:7] if commit_sha else "commit"
            lines.append(f"Commit: {short} {commit_url} {commit_message[:200]}".strip())
        if diff_summary:
            label = f"Summary ({diff_source})" if diff_source else "Summary"
            lines.append(f"{label}: {diff_summary.strip()[:1500]}")
        code = (diff_text or "No diff text available")[:1800]
        lines.append(f"```diff\n{code}\n```")
        try:
            resp = requests.post(
                self.webhook_url, json={"text": "\n".join(lines)}, timeout=self.timeout
            )
            if resp.status_code >= 400:
                print(f"Failed to send Slack notification: {resp.status_code} {resp.text[:300]}")
        except Exception as e:
            print(f"Error sending Slack notification: {e}")
