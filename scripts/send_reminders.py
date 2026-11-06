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
LEVEL_PILL = {  # (pill background, uppercase label)
    "danger": ("#c0392b", "OVERDUE"),
    "warning": ("#5f9e12", "DUE SOON"),
    "info": ("#2f7aa8", "FOR YOUR ATTENTION"),
}
ROW_BG = {"danger": "#fdecea", "warning": "#eef6e7", "info": "#eef5fb"}
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "static", "images", "email-logo.png")


def _icon(title):
    t = (title or "").lower()
    if "training" in t:
        return "\U0001F393"  # graduation cap
    if "emp201" in t or "sdl" in t:
        return "\U0001F9FE"  # receipt
    if "grant" in t:
        return "\U0001F91D"  # handshake
    if "bursar" in t:
        return "\U0001F4DA"  # books
    if "learnership" in t or "internship" in t or "recruit" in t:
        return "\U0001F465"  # people
    if "wsp" in t or "atr" in t or "submission" in t or "due" in t:
        return "\U0001F4C5"  # calendar
    return "\U0001F514"      # bell


def _db_path():
    data_dir = os.environ.get("HR_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return os.path.join(os.path.abspath(data_dir), "hr_audit.db")


def render(reminders, today, logo_cid=None):
    reminders = sorted(reminders, key=lambda r: LEVEL_ORDER.get(r["level"], 9))
    n = len(reminders)
    subject = "HR Compliance reminders - %s (%d item%s)" % (
        today.strftime("%d %b %Y"), n, "" if n == 1 else "s")

    # Plain-text version
    text = ["HR Compliance Suite - reminders for %s" % today.strftime("%A, %d %B %Y"), ""]
    current = None
    for r in reminders:
        label = LEVEL_LABEL.get(r["level"], r["level"])
        if label != current:
            text += ["", "== %s ==" % label]
            current = label
        text.append(" - %s: %s" % (r["title"], r["detail"]))
    text += ["", "Open the HR Compliance Suite to action these."]

    # Branded HTML version (table-based + inline styles for Outlook compatibility)
    date_long = today.strftime("%A, %d %B %Y")
    app_url = os.environ.get("APP_URL", "").strip()
    if logo_cid:
        brand = ('<img src="cid:%s" width="150" alt="Dash BPO" '
                 'style="display:block;border:0;height:auto">' % logo_cid)
    else:
        brand = '<span style="font-size:22px;font-weight:700;color:#ffffff">Dash BPO</span>'

    cards = []
    for r in reminders:
        bg = ROW_BG.get(r["level"], "#eef5fb")
        pill_bg, pill_label = LEVEL_PILL.get(r["level"], ("#2f7aa8", "FOR YOUR ATTENTION"))
        cards.append(
            '<tr><td style="padding:6px 24px">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:'
            + bg + ';border-radius:8px"><tr>'
            '<td width="56" align="center" valign="top" style="padding:16px 0 16px 10px;font-size:26px">'
            + _icon(r["title"]) + '</td>'
            '<td valign="top" style="padding:16px 18px 16px 14px;border-left:3px solid #8cc63f">'
            '<span style="display:inline-block;background:' + pill_bg + ';color:#ffffff;font-size:11px;'
            'font-weight:700;letter-spacing:.5px;padding:3px 10px;border-radius:11px">' + pill_label + '</span>'
            '<div style="font-size:16px;font-weight:700;color:#15497e;margin:8px 0 2px">'
            + escape(r["title"]) + '</div>'
            '<div style="font-size:14px;color:#5b6b7b;line-height:1.45">' + escape(r["detail"]) + '</div>'
            '</td></tr></table></td></tr>')

    if app_url:
        action = ('<a href="' + escape(app_url) + '" style="display:inline-block;background:#6aab1f;'
                  'color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:13px 30px;'
                  'border-radius:6px">Open the HR Compliance Suite &rarr;</a>')
    else:
        action = ('<div style="display:inline-block;background:#6aab1f;color:#ffffff;font-size:15px;'
                  'font-weight:700;padding:13px 30px;border-radius:6px">Action required &rarr;</div>')

    html = (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#eceff3">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eceff3">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;'
        'width:100%;background:#ffffff;border-radius:10px;overflow:hidden;font-family:Segoe UI,Arial,sans-serif">'
        # header
        '<tr><td style="background:#0e2244;padding:18px 24px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td valign="middle">' + brand + '</td>'
        '<td valign="middle" align="right" style="color:#ffffff;font-size:17px;font-weight:600">'
        'HR Compliance Notifications</td></tr></table></td></tr>'
        '<tr><td style="background:#8cc63f;height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>'
        # title
        '<tr><td style="padding:24px 24px 8px">'
        '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        '<td valign="middle" style="font-size:30px;padding-right:12px">\U0001F4C5</td>'
        '<td valign="middle" style="border-left:3px solid #8cc63f;padding-left:14px">'
        '<div style="font-size:24px;font-weight:700;color:#15497e">HR Compliance Reminders</div>'
        '<div style="font-size:14px;color:#6b7280;margin-top:2px">' + date_long + '</div>'
        '</td></tr></table></td></tr>'
        # cards
        + "".join(cards) +
        # action
        '<tr><td align="center" style="padding:18px 24px 26px">' + action + '</td></tr>'
        # footer
        '<tr><td style="background:#0e2244;padding:14px 24px;color:#ffffff;font-size:13px">'
        '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        '<td valign="middle" style="font-size:16px;padding-right:10px">ℹ️</td>'
        '<td valign="middle">Automated reminder from the <b>HR Compliance Suite</b>.</td>'
        '</tr></table></td></tr>'
        '</table></td></tr></table></body></html>')

    return subject, "\n".join(text), html


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

    have_logo = os.path.exists(LOGO_PATH)
    subject, text, html = render(reminders, today, logo_cid="logo" if have_logo else None)
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
        status = mailer.send_mail(recipients, subject, text, html=html,
                                  inline_images={"logo": LOGO_PATH} if have_logo else None)
    except mailer.MailError as e:
        sys.exit("Send failed: %s" % e)
    print("Sent '%s' to %s (HTTP %s)." % (subject, recipients, status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
