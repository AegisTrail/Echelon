"""Email (SMTP) and generic-JSON-webhook notifiers. Stdlib only for SMTP."""

from __future__ import annotations

import json
import smtplib
import urllib.request
from email.message import EmailMessage

from _version import __version__
from models import SnippetConfig


def _safe_header(value: str) -> str:
    """Strip CR/LF to prevent SMTP header injection."""
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


class EmailNotifier:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        email_from: str = "",
        email_to: str = "",
        use_tls: bool = True,
        timeout: int = 15,
    ):
        self.smtp_host = (smtp_host or "").strip()
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user or ""
        self.smtp_password = smtp_password or ""
        self.email_from = _safe_header(email_from)
        self.email_to = [
            _safe_header(a) for a in (email_to or "").replace(";", ",").split(",") if a.strip()
        ]
        self.use_tls = use_tls
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.smtp_host and self.email_from and self.email_to)

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
        if not self.configured:
            print("Email not configured; skipping email notification.")
            return
        subject = _safe_header(
            f"[Echelon] Change in {snippet.owner}/{snippet.repo}: {snippet.file_path}"
        )[:200]
        body_lines = [
            f"File: {snippet.file_url}",
            f"Lines: L{snippet.start_line}-L{snippet.end_line}",
            f"Note: {snippet.note or '-'}",
        ]
        if commit_sha or commit_url:
            body_lines.append(f"Commit: {commit_sha[:12]} {commit_url}".strip())
            if commit_message:
                body_lines.append(f"Message: {commit_message[:300]}")
        if diff_summary:
            label = f"Summary ({diff_source})" if diff_source else "Summary"
            body_lines += ["", f"{label}:", diff_summary.strip()[:3000]]
        body_lines += ["", "Diff:", diff_text or "(empty)"]
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.email_from
        msg["To"] = ", ".join(self.email_to)
        msg.set_content("\n".join(body_lines)[:20000])
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.smtp_user:
                    smtp.login(self.smtp_user, self.smtp_password)
                smtp.send_message(msg)
        except Exception as e:
            print(f"Error sending email notification: {e}")


class GenericWebhookNotifier:
    """POST a JSON event to any URL (automation / SOAR friendly)."""

    def __init__(self, webhook_url: str, timeout: int = 10):
        url = (webhook_url or "").strip()
        if url and not (
            url.startswith("https://")
            or url.startswith("http://localhost")
            or url.startswith("http://127.0.0.1")
        ):
            raise ValueError("webhook_url must use https:// (http allowed only for localhost)")
        self.webhook_url = url
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
            return
        event = {
            "event": "snippet_changed",
            "owner": snippet.owner,
            "repo": snippet.repo,
            "branch": snippet.branch,
            "file_path": snippet.file_path,
            "file_url": snippet.file_url,
            "start_line": snippet.start_line,
            "end_line": snippet.end_line,
            "note": snippet.note,
            "diff": diff_text,
            "summary": diff_summary,
            "summary_source": diff_source,
            "commit_sha": commit_sha,
            "commit_url": commit_url,
            "commit_message": commit_message,
        }
        data = json.dumps(event).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 - URL validated https-only in __init__
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": f"Echelon/{__version__}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - URL validated https-only in __init__
                if resp.status >= 400:
                    print(f"Failed to send generic webhook: HTTP {resp.status}")
        except Exception as e:
            print(f"Error sending generic webhook notification: {e}")
