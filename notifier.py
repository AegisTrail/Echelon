from typing import Optional
import requests
from models import SnippetConfig


class DiscordNotifier:
    def __init__(self):
        pass

    def notify_change(
        self,
        webhook_url: str,
        snippet: SnippetConfig,
        diff_text: str,
        diff_summary: Optional[str] = None,
        diff_source: Optional[str] = None,
    ) -> None:
        if not webhook_url:
            print("No webhook URL configured; skipping Discord notification.")
            return

        title = f"Code change detected in {snippet.owner}/{snippet.repo}"
        description = (
            f"**File:** {snippet.file_url}\n"
            f"**Lines monitored:** L{snippet.start_line}-L{snippet.end_line}\n"
            f"**Note:** {snippet.note or '—'}"
        )

        fields = []
        if diff_summary:
            label = f"Diff summary ({diff_source})" if diff_source else "Diff summary"
            trimmed = diff_summary.strip()
            if len(trimmed) > 1900:
                trimmed = trimmed[:1900] + "…"
            fields.append({"name": label, "value": trimmed, "inline": False})

        code_block = diff_text or "No diff text available"
        if len(code_block) > 1800:
            code_block = code_block[:1800] + "\n…"

        fields.append({"name": "Code change diff", "value": f"```diff\n{code_block}\n```", "inline": False})

        payload = {
            "content": None,
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "fields": fields,
                }
            ],
        }

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code >= 400:
                print(f"Failed to send Discord notification: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Error sending Discord notification: {e}")
