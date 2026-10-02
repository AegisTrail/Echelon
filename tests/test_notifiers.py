"""Notifier safety: escaping, injection guards, size bounds."""

from email_notifier import EmailNotifier, GenericWebhookNotifier
from models import SnippetConfig
from telegram import TelegramNotifier


def _snippet():
    return SnippetConfig(
        id="s1",
        owner="o",
        repo="r",
        branch="main",
        file_path="f.py",
        start_line=1,
        end_line=2,
        file_url="https://github.com/o/r/blob/main/f.py#L1-L2",
        note='<script>alert("x")</script>',
    )


def test_telegram_escapes_html(monkeypatch):
    sent = {}

    class Resp:
        status_code = 200
        text = "ok"

    def fake_post(url, **kw):
        sent.update(kw["json"])
        return Resp()

    import telegram as tg

    monkeypatch.setattr(tg.requests, "post", fake_post)
    TelegramNotifier(bot_token="123456:" + "A" * 30, chat_id="1").notify_change(
        _snippet(), "<b>evil</b>\n+rm -rf /\n", diff_summary="<i>s</i>"
    )
    assert "<b>evil</b>" not in sent["text"]
    assert "&lt;b&gt;evil&lt;/b&gt;" in sent["text"]
    assert "<script>" not in sent["text"]


def test_email_strips_header_injection():
    n = EmailNotifier(
        smtp_host="smtp.test",
        email_from="a@test\nBcc: evil@test",
        email_to="b@test\r\nCc: evil@test",
    )
    assert "\n" not in n.email_from and "\r" not in n.email_to
    assert n.configured  # still usable after sanitizing


def test_email_unconfigured_is_safe(capsys):
    EmailNotifier(smtp_host="", email_from="", email_to="").notify_change(_snippet(), "d")
    assert "not configured" in capsys.readouterr().out


def test_generic_webhook_rejects_non_https():
    try:
        GenericWebhookNotifier(webhook_url="http://evil.test/hook")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    GenericWebhookNotifier(webhook_url="http://localhost:9/hook")  # localhost allowed


def test_discord_truncation_bounds(monkeypatch):
    import discord as dc

    payloads = []

    class Resp:
        status_code = 200
        text = "ok"

    def fake_post(url, **kw):
        payloads.append(kw["json"])
        return Resp()

    monkeypatch.setattr(dc.requests, "post", fake_post)
    dc.DiscordNotifier(webhook_url="https://discord.com/api/webhooks/1/abc").notify_change(
        _snippet(),
        "x" * 5000,
        diff_summary="y" * 5000,
        diff_source="T",
        commit_sha="abc123",
        commit_url="https://example.com/c",
        commit_message="m" * 500,
    )
    fields = payloads[0]["embeds"][0]["fields"]
    assert all(len(f["value"]) <= 2000 for f in fields)
    assert len(payloads[0]["embeds"][0]["description"]) <= 2000
