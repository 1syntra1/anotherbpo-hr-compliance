"""Best-effort e-mail notifications for account and workflow events.

Sends via the Graph mailer using the same branded shell as the reminder digest.
Every call swallows errors (logs them) so a notification can never break the user
action that triggered it, and it silently no-ops when the mailer isn't configured.
"""
import logging
import os
from html import escape

from modules import mailer

log = logging.getLogger("hrcs.notify")
_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "static", "images", "email-logo.png")


def _shell(heading, lines, app_url=""):
    body = "".join(
        '<p style="font-size:14px;color:#33465c;line-height:1.5;margin:0 0 12px">' + escape(line) + '</p>'
        for line in lines)
    button = ""
    if app_url:
        button = ('<tr><td align="center" style="padding:6px 24px 26px">'
                  '<a href="' + escape(app_url) + '" style="display:inline-block;background:#6aab1f;'
                  'color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;padding:13px 30px;'
                  'border-radius:6px">Open the HR Compliance Suite &rarr;</a></td></tr>')
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#eceff3">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eceff3">'
        '<tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;'
        'background:#ffffff;border-radius:10px;overflow:hidden;font-family:Segoe UI,Arial,sans-serif">'
        '<tr><td style="background:#0e2244;padding:18px 24px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td valign="middle"><img src="cid:logo" width="150" alt="Dash BPO" '
        'style="display:block;border:0;height:auto"></td>'
        '<td valign="middle" align="right" style="color:#ffffff;font-size:17px;font-weight:600">'
        'HR Compliance Notifications</td></tr></table></td></tr>'
        '<tr><td style="background:#8cc63f;height:4px;line-height:4px;font-size:0">&nbsp;</td></tr>'
        '<tr><td style="padding:24px 24px 6px"><div style="font-size:22px;font-weight:700;color:#15497e;'
        'border-left:3px solid #8cc63f;padding-left:14px">' + escape(heading) + '</div></td></tr>'
        '<tr><td style="padding:14px 24px 8px 38px">' + body + '</td></tr>'
        + button +
        '<tr><td style="background:#0e2244;padding:14px 24px;color:#ffffff;font-size:13px">'
        'Automated notification from the <b>HR Compliance Suite</b>.</td></tr>'
        '</table></td></tr></table></body></html>')


def send(to, subject, heading, lines, text=None):
    """Send one branded notification. Returns True if sent. Never raises; no-ops when
    the mailer is unconfigured or there are no recipients."""
    recips = [a for a in ([to] if isinstance(to, str) else list(to)) if a]
    if not recips:
        return False
    if not mailer.mailer_configured():
        log.info("notification skipped (mailer not configured): %s", subject)
        return False
    app_url = os.environ.get("APP_URL", "").strip()
    html = _shell(heading, lines, app_url)
    text = text or (heading + "\n\n" + "\n".join(lines))
    inline = {"logo": _LOGO} if os.path.exists(_LOGO) else None
    try:
        mailer.send_mail(recips, subject, text, html=html, inline_images=inline)
        return True
    except Exception as e:  # a failed notification must never break the user's action
        log.warning("notification send failed (%s): %s", subject, e)
        return False
