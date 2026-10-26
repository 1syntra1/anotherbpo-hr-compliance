"""E-mail the WSP / skills-development reminder digest (via Microsoft Graph).

Run on a schedule (e.g. daily via cron). It computes what needs attention with
modules.reminders.compute_wsp_reminders — the same source of truth as the in-app
panels — and e-mails a single digest to the addresses in WSP_REMINDER_TO.

    python scripts/send_reminders.py             # send only if something is due
    python scripts/send_reminders.py --dry-run   # print the e-mail, send nothing
    python scripts/send_reminders.py --always     # send even when nothing is due

Env: GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, MAIL_FROM, WSP_REMINDER_TO
"""
import argparse
import os
import sqlite3
import sys
from datetime import date
from html import escape

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules import mailer
from modules.reminders import compute_wsp_reminders

LEVEL_LABEL = {"danger": "Overdue / urgent", "warning": "Due soon", "info": "For your attention"}
LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}


def _db_path():
    data_dir = os.environ.get("HR_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.join(os.path.abspath(data_dir), "hr_audit.db")


def render(reminders, today):
    reminders = sorted(reminders, key=lambda r: LEVEL_ORDER.get(r["level"], 9))
    n = len(reminders)
    subject = "HR Compliance reminders - %s (%d item%s)" % (
        today.strftime("%d %b %Y"), n, "" if n == 1 else "s")
    text = ["HR Compliance Suite - reminders for %s" % today.strftime("%A, %d %B %Y"), ""]
    parts = ['<div style="font-family:Segoe UI,Arial,sans-serif;color:#1b2a4a">',
             '<h2 style="color:#0e2244;margin-bottom:2px">HR Compliance reminders</h2>',
             '<p style="color:#666;margin-top:0">%s</p>' % today.strftime("%A, %d %B %Y")]
    current = None
    for r in reminders:
        label = LEVEL_LABEL.get(r["level"], r["level"])
        if label != current:
            text += ["", "== %s ==" % label]
            parts.append('<h3 style="margin:18px 0 6px;color:#0e2244">%s</h3>' % escape(label))
            current = label
        text.append(" - %s: %s" % (r["title"], r["detail"]))
        parts.append('<p style="margin:4px 0"><b>%s</b><br>'
                     '<span style="color:#555">%s</span></p>' % (escape(r["title"]), escape(r["detail"])))
    text += ["", "Open the HR Compliance Suite to action these."]
    parts.append('<p style="margin-top:22px;color:#999;font-size:12px">'
                 'Automated reminder from the HR Compliance Suite.</p></div>')
    return subject, "\n".join(text), "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the e-mail, send nothing")
    ap.add_argument("--always", action="store_true", help="send even when nothing is due")
    args = ap.parse_args()

    today = date.today()
    db = sqlite3.connect(_db_path(), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        reminders = compute_wsp_reminders(db, today)
    finally:
        db.close()

    if not reminders and not args.always:
        print("Nothing due as of %s - no e-mail sent." % today.isoformat())
        return 0

    subject, text, html = render(reminders, today)
    recipients = os.environ.get("WSP_REMINDER_TO", "")

    if args.dry_run:
        print("--- DRY RUN (no e-mail sent) ---")
        print("From:   ", os.environ.get("MAIL_FROM") or "(MAIL_FROM not set)")
        print("To:     ", recipients or "(WSP_REMINDER_TO not set)")
        print("Subject:", subject)
        print("\n" + text + "\n")
        print("--- mailer configured: %s ---" % mailer.mailer_configured())
        if not mailer.mailer_configured():
            print("Missing env:", ", ".join(mailer.missing_config()))
        return 0

    if not recipients.strip():
        sys.exit("WSP_REMINDER_TO is not set - nobody to send to.")
    try:
        status = mailer.send_mail(recipients, subject, text, html=html)
    except mailer.MailError as e:
        sys.exit("Send failed: %s" % e)
    print("Sent '%s' to %s (HTTP %s)." % (subject, recipients, status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
