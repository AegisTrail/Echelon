from __future__ import annotations

import requests

from models import SnippetConfig


class DiscordNotifier:
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
            print("No webhook URL configured; skipping Discord notification.")
            return

        title = f"Code change detected in {snippet.owner}/{snippet.repo}"
        description = (
            f"**File:** {snippet.file_url}\n"
            f"**Lines monitored:** L{snippet.start_line}-L{snippet.end_line}\n"
            f"**Note:** {snippet.note or '-'}"
        )
        if commit_sha or commit_url:
            short = commit_sha[:7] if commit_sha else "commit"
            link = f"[{short}]({commit_url})" if commit_url else short
            extra = f"\n**Commit:** {link}"
            if commit_message:
                extra += f": {commit_message[:200]}"
            description += extra

        fields = []
        if diff_summary:
            label = f"Diff summary ({diff_source})" if diff_source else "Diff summary"
            trimmed = diff_summary.strip()
            if len(trimmed) > 1900:
                trimmed = trimmed[:1900] + "..."
            fields.append({"name": label, "value": trimmed, "inline": False})

        code_block = diff_text or "No diff text available"
        if len(code_block) > 1800:
            code_block = code_block[:1800] + "\n..."

        fields.append(
            {"name": "Code change diff", "value": f"```diff\n{code_block}\n```", "inline": False}
        )

        payload = {
            "content": None,
            "embeds": [{"title": title, "description": description, "fields": fields}],
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                print(f"Failed to send Discord notification: {resp.status_code} {resp.text[:300]}")
        except Exception as e:
            print(f"Error sending Discord notification: {e}")
