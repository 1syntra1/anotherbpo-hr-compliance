"""Send e-mail via Microsoft Graph using an app registration (application permission).

No mailbox password is stored on the server: the app authenticates as itself with a
client secret (OAuth2 client-credentials) and sends from a single mailbox (MAIL_FROM).
The app registration needs the Graph *application* permission Mail.Send with admin
consent, ideally scoped to the sender mailbox with an Application Access Policy.

Configure via environment:
    GRAPH_TENANT_ID      directory (tenant) ID
    GRAPH_CLIENT_ID      application (client) ID
    GRAPH_CLIENT_SECRET  client secret value
    MAIL_FROM            the sender mailbox, e.g. noreplygrc@dashbpo.com

Standard library only (urllib) — no extra dependency.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

_AUTHORITY = "https://login.microsoftonline.com"
_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"

_FIELDS = (("tenant", "GRAPH_TENANT_ID"), ("client", "GRAPH_CLIENT_ID"),
           ("secret", "GRAPH_CLIENT_SECRET"), ("sender", "MAIL_FROM"))


class MailError(RuntimeError):
    pass


def _cfg():
    return {key: os.environ.get(env, "").strip() for key, env in _FIELDS}


def missing_config():
    c = _cfg()
    return [key for key, _env in _FIELDS if not c[key]]


def mailer_configured():
    return not missing_config()


def _token(cfg, timeout):
    data = urllib.parse.urlencode({
        "client_id": cfg["client"],
        "client_secret": cfg["secret"],
        "scope": _SCOPE,
        "grant_type": "client_credentials",
    }).encode()
    url = "%s/%s/oauth2/v2.0/token" % (_AUTHORITY, urllib.parse.quote(cfg["tenant"]))
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            tok = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise MailError("Token request failed (HTTP %s): %s" % (e.code, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        raise MailError("Cannot reach Microsoft login: %s" % e.reason)
    if "access_token" not in tok:
        raise MailError("No access token returned: %s" % tok)
    return tok["access_token"]


def _recipients(to):
    if isinstance(to, str):
        parts = to.replace(";", ",").split(",")
    else:
        parts = list(to)
    return [a.strip() for a in parts if a and a.strip()]


def send_mail(to, subject, text, html=None, timeout=20):
    """Send one e-mail from MAIL_FROM to `to` (list, or comma/semicolon string).
    Returns the Graph HTTP status (202 on success). Raises MailError on any problem."""
    cfg = _cfg()
    miss = missing_config()
    if miss:
        raise MailError("Mailer not configured; missing env: %s"
                        % ", ".join(env for key, env in _FIELDS if key in miss))
    recips = _recipients(to)
    if not recips:
        raise MailError("No recipients.")
    body = {"contentType": "HTML", "content": html} if html else {"contentType": "Text", "content": text}
    payload = {
        "message": {
            "subject": subject,
            "body": body,
            "toRecipients": [{"emailAddress": {"address": a}} for a in recips],
        },
        "saveToSentItems": False,
    }
    token = _token(cfg, timeout)
    url = "%s/users/%s/sendMail" % (_GRAPH, urllib.parse.quote(cfg["sender"]))
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status  # 202 Accepted, empty body
    except urllib.error.HTTPError as e:
        raise MailError("sendMail failed (HTTP %s): %s" % (e.code, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        raise MailError("Cannot reach Microsoft Graph: %s" % e.reason)
