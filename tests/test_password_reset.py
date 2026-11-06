"""Tests for the password set/reset one-time-link flow."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from conftest import csrf, login, USER_PW


def _issue(app_module, user_id):
    db = app_module.connect_db()
    try:
        tok = app_module._issue_reset_token(db, user_id)
        db.commit()
        return tok
    finally:
        db.close()


def _count_tokens(app_module, user_id=None):
    db = app_module.connect_db()
    try:
        if user_id is None:
            return db.execute("SELECT COUNT(*) c FROM password_reset_tokens").fetchone()["c"]
        return db.execute("SELECT COUNT(*) c FROM password_reset_tokens WHERE user_id=?",
                          (user_id,)).fetchone()["c"]
    finally:
        db.close()


# ── token helpers ─────────────────────────────────────────────────────────────

def test_issue_and_validate_token(app_module, make_user):
    uid = make_user(username="tok1")
    tok = _issue(app_module, uid)
    assert tok
    db = app_module.connect_db()
    try:
        assert app_module._validate_reset_token(db, tok)[0] == uid
        assert app_module._validate_reset_token(db, "not-a-token") is None
    finally:
        db.close()


def test_resend_is_throttled(app_module, make_user):
    uid = make_user(username="tok2")
    assert _issue(app_module, uid)          # first succeeds
    assert _issue(app_module, uid) is None  # second, immediately, is suppressed


# ── set-password route ────────────────────────────────────────────────────────

def test_set_password_via_link(app_module, make_user, client):
    uid = make_user(username="linkuser", password=USER_PW)
    tok = _issue(app_module, uid)
    path = "/set-password/%s" % tok
    assert client.get(path).status_code == 200                       # form shown
    newpw = "Fresh-Pass-9watch"
    r = client.post(path, data={"_csrf": csrf(client, path),
                                "new_password": newpw, "confirm_password": newpw})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get(path).status_code == 400                       # token consumed (single-use)
    r2 = login(client, "linkuser", newpw)                            # new password works
    assert r2.status_code == 302 and "/login" not in r2.headers["Location"]


def test_invalid_token_rejected(client):
    assert client.get("/set-password/nonsense").status_code == 400


def test_weak_password_rejected_and_token_kept(app_module, make_user, client):
    uid = make_user(username="weakpw")
    tok = _issue(app_module, uid)
    path = "/set-password/%s" % tok
    r = client.post(path, data={"_csrf": csrf(client, path),
                                "new_password": "short", "confirm_password": "short"})
    assert r.status_code == 200                      # re-rendered with the error
    assert client.get(path).status_code == 200       # token not consumed


# ── forgot-password route (enumeration-safe) ──────────────────────────────────

def test_forgot_unknown_user_no_token_same_response(app_module, client):
    r = client.post("/forgot", data={"_csrf": csrf(client, "/forgot"), "identifier": "ghost@x.com"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert _count_tokens(app_module) == 0


def test_forgot_known_user_creates_token(app_module, make_user, client):
    uid = make_user(username="known")
    db = app_module.connect_db()
    db.execute("UPDATE users SET email=? WHERE id=?", ("known@dashbpo.com", uid))
    db.commit(); db.close()
    r = client.post("/forgot", data={"_csrf": csrf(client, "/forgot"), "identifier": "known@dashbpo.com"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert _count_tokens(app_module, uid) == 1


# ── admin "send reset link" route ─────────────────────────────────────────────

def test_admin_send_reset_requires_email(app_module, admin_client, make_user):
    uid = make_user(username="noemail")
    admin_client.post("/users/%d/send-reset" % uid, data={"_csrf": csrf(admin_client, "/")})
    assert _count_tokens(app_module, uid) == 0


def test_admin_send_reset_creates_token(app_module, admin_client, make_user):
    uid = make_user(username="hasemail")
    db = app_module.connect_db()
    db.execute("UPDATE users SET email=? WHERE id=?", ("has@dashbpo.com", uid))
    db.commit(); db.close()
    admin_client.post("/users/%d/send-reset" % uid, data={"_csrf": csrf(admin_client, "/")})
    assert _count_tokens(app_module, uid) == 1


def test_nonadmin_cannot_send_reset(user_client):
    r = user_client.post("/users/1/send-reset", data={"_csrf": csrf(user_client, "/")})
    assert r.status_code == 302 and "/login" not in r.headers["Location"]  # blocked to dashboard
