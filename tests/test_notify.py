"""Tests for the best-effort notification helper (no network)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import mailer, notify

ENV = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "MAIL_FROM")


def _configure(monkeypatch):
    for k in ENV:
        monkeypatch.setenv(k, "x")


def test_notify_skipped_when_unconfigured(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert notify.send("a@b.com", "s", "h", ["line"]) is False


def test_notify_skipped_without_recipients(monkeypatch):
    _configure(monkeypatch)
    assert notify.send([], "s", "h", ["line"]) is False
    assert notify.send("", "s", "h", ["line"]) is False


def test_notify_sends_and_builds_branded_html(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_send(to, subject, text, html=None, inline_images=None, timeout=20):
        captured.update(to=to, subject=subject, text=text, html=html, inline=inline_images)
        return 202

    monkeypatch.setattr(mailer, "send_mail", fake_send)
    ok = notify.send("user@dashbpo.com", "Subj", "A Heading", ["First line", "Tom & Jerry <x>"])
    assert ok is True
    assert captured["to"] == ["user@dashbpo.com"]
    assert captured["subject"] == "Subj"
    assert "A Heading" in captured["html"]
    assert "Tom &amp; Jerry &lt;x&gt;" in captured["html"]   # body lines are escaped
    assert 'src="cid:logo"' in captured["html"]


def test_notify_never_raises(monkeypatch):
    _configure(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(mailer, "send_mail", boom)
    assert notify.send("a@b.com", "s", "h", ["line"]) is False  # swallowed, not raised
