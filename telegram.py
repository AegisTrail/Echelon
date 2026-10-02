from __future__ import annotations

import html

import requests

from models import SnippetConfig


def _trim(s: str, max_len: int) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "..."


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str | int, timeout: int = 10):
        self.bot_token = (bot_token or "").strip()
        self.chat_id = str(chat_id).strip() if chat_id is not None else ""
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
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials missing; skipping Telegram notification.")
            return

        max_message = 3800

        title = f"Code change detected in {html.escape(snippet.owner)}/{html.escape(snippet.repo)}"
        meta_lines = [
            f"<b>File:</b> {html.escape(snippet.file_url)}",
            f"<b>Lines monitored:</b> L{snippet.start_line}-L{snippet.end_line}",
            f"<b>Note:</b> {html.escape(snippet.note or '-')}",
        ]
        if commit_sha or commit_url:
            short = html.escape(commit_sha[:7]) if commit_sha else "commit"
            if commit_url:
                meta_lines.append(f"<b>Commit:</b> {short} {html.escape(commit_url)}")
            else:
                meta_lines.append(f"<b>Commit:</b> {short}")
            if commit_message:
                meta_lines.append(f"<b>Message:</b> {html.escape(commit_message[:200])}")
        meta = "\n".join(meta_lines)

        parts: list[str] = [f"<b>{title}</b>", meta]

        if diff_summary:
            label = f"Diff summary ({diff_source})" if diff_source else "Diff summary"
            parts.append(f"<b>{html.escape(label)}:</b>\n{html.escape(_trim(diff_summary, 1200))}")

        code_block = html.escape(_trim(diff_text or "No diff text available", 1800))
        parts.append(f"<b>Code change diff:</b>\n<pre><code>{code_block}</code></pre>")

        message = _trim("\n\n".join(parts), max_message)

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                print(f"Failed to send Telegram notification: {resp.status_code} {resp.text[:300]}")
        except Exception as e:
            print(f"Error sending Telegram notification: {e}")
