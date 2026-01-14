from __future__ import annotations

from typing import Optional

import requests

from models import SnippetConfig


def _trim(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


class TelegramNotifier:

    def __init__(self, bot_token: str, chat_id: str | int):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = str(chat_id).strip() if chat_id is not None else ""

    def notify_change(
        self,
        snippet: SnippetConfig,
        diff_text: str,
        diff_summary: Optional[str] = None,
        diff_source: Optional[str] = None,
    ) -> None:
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials missing; skipping Telegram notification.")
            return

        # Telegram max message is 4096 chars. Keep a buffer for safety.
        max_message = 3800

        title = f"Code change detected in {snippet.owner}/{snippet.repo}"
        meta = (
            f"<b>File:</b> {snippet.file_url}\n"
            f"<b>Lines monitored:</b> L{snippet.start_line}-L{snippet.end_line}\n"
            f"<b>Note:</b> {snippet.note or '—'}"
        )

        parts: list[str] = [f"<b>{title}</b>", meta]

        if diff_summary:
            label = f"Diff summary ({diff_source})" if diff_source else "Diff summary"
            parts.append(f"<b>{label}:</b>\n{_trim(diff_summary, 1200)}")

        code_block = diff_text or "No diff text available"
        code_block = _trim(code_block, 1800)
        parts.append(f"<b>Code change diff:</b>\n<pre><code>{code_block}</code></pre>")

        message = "\n\n".join(parts)
        message = _trim(message, max_message)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code >= 400:
                print(f"Failed to send Telegram notification: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"Error sending Telegram notification: {e}")

