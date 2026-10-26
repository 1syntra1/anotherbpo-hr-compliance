"""Tests for the Graph mailer and the reminder-digest renderer (no network)."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import mailer
import scripts.send_reminders as sr

ENV = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "MAIL_FROM")


def test_mailer_unconfigured_reports_missing(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    assert not mailer.mailer_configured()
    assert set(mailer.missing_config()) == {"tenant", "client", "secret", "sender"}


def test_send_mail_refuses_when_unconfigured(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(mailer.MailError) as e:
        mailer.send_mail("a@b.com", "subject", "body")
    assert "not configured" in str(e.value)


def test_configured_when_all_present(monkeypatch):
    for k in ENV:
        monkeypatch.setenv(k, "x")
    assert mailer.mailer_configured()
    assert mailer.missing_config() == []


def test_recipient_parsing():
    assert mailer._recipients("a@x.com, b@x.com ; c@x.com") == ["a@x.com", "b@x.com", "c@x.com"]
    assert mailer._recipients(["a@x.com", "", "  b@x.com "]) == ["a@x.com", "b@x.com"]


def test_digest_render_orders_and_includes_items():
    reminders = [
        {"level": "info", "title": "Bursary applications", "detail": "none yet"},
        {"level": "danger", "title": "WSP/ATR due 2026-04-30", "detail": "5 days left"},
        {"level": "warning", "title": "Log training", "detail": "last month empty"},
    ]
    subject, text, html = sr.render(reminders, date(2026, 4, 25))
    assert "3 items" in subject
    # danger sorts before warning before info
    assert text.index("WSP/ATR") < text.index("Log training") < text.index("Bursary")
    assert "WSP/ATR due 2026-04-30" in html and "Overdue" in html


def test_digest_render_escapes_html():
    reminders = [{"level": "info", "title": "A & <b>B</b>", "detail": "x < y"}]
    _subject, _text, html = sr.render(reminders, date(2026, 1, 1))
    assert "&amp;" in html and "&lt;b&gt;" in html
