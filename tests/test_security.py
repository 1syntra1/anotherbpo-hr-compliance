"""Authentication, session, CSRF and authorisation tests (Stage 6)."""
import os

import pytest

from conftest import ADMIN_PW, USER_PW, csrf, login, post, query


# ── Configuration ──────────────────────────────────────────────

def test_secret_key_not_hardcoded(app_module):
    assert app_module.app.secret_key != "anotherbpo-hr-compliance-dev-key"
    assert len(app_module.app.secret_key) >= 32
    assert os.path.exists(os.path.join(app_module.DATA_DIR, ".secret_key"))


def test_short_env_secret_rejected(tmp_path, monkeypatch):
    import importlib, sys
    monkeypatch.setenv("HR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HR_AUDIT_SECRET", "short")
    with pytest.raises(RuntimeError):
        importlib.reload(sys.modules["app"])
    monkeypatch.delenv("HR_AUDIT_SECRET")


def test_only_one_seed_account_and_no_known_password(app_module):
    rows = query(app_module, "SELECT username, role FROM users")
    assert [(r["username"], r["role"]) for r in rows] == [("admin", "admin")]
    h = query(app_module, "SELECT password_hash FROM users")[0][0]
    assert not app_module.check_password_hash(h, "anotherbpo2024")


def test_generated_admin_password_when_env_missing(tmp_path, monkeypatch):
    import importlib, sys
    monkeypatch.setenv("HR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HR_ADMIN_PASSWORD", raising=False)
    mod = importlib.reload(sys.modules["app"])
    f = tmp_path / "initial_admin_password.txt"
    assert f.exists()
    pw = f.read_text().split("Password: ")[1].splitlines()[0]
    row = query(mod, "SELECT password_hash, must_change_password FROM users")[0]
    assert mod.check_password_hash(row[0], pw) and row[1] == 1


def test_debug_not_enabled(app_module):
    assert app_module.app.debug is False


def test_cookie_flags(app_module):
    cfg = app_module.app.config
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_authenticated_pages_not_cached(admin_client):
    assert admin_client.get("/").headers["Cache-Control"] == "no-store"


# ── Login / lockout / enumeration ──────────────────────────────

def test_login_success_and_audit(client, app_module):
    r = login(client)
    assert r.status_code == 302
    actions = [r["action"] for r in query(app_module, "SELECT action FROM audit_log")]
    assert "login" in actions


def test_wrong_password_same_message_as_unknown_user(client):
    a = login(client, "admin", "wrong-password-xyz").get_data(as_text=True)
    b = login(client, "nobody", "wrong-password-xyz").get_data(as_text=True)
    msg = "Invalid username or password"
    assert msg in a and msg in b


def test_lockout_after_five_failures(client, app_module):
    for _ in range(5):
        login(client, "admin", "nope-nope-nope")
    r = login(client, "admin", ADMIN_PW)  # correct password, but locked
    assert r.status_code == 401
    assert query(app_module, "SELECT locked_until FROM users WHERE username='admin'")[0][0]
    assert "account_locked" in [x[0] for x in query(app_module, "SELECT action FROM audit_log")]


def test_disabled_user_cannot_login(app_module, make_user):
    make_user("gone")
    db = app_module.connect_db(); db.execute("UPDATE users SET active=0 WHERE username='gone'"); db.commit(); db.close()
    c = app_module.app.test_client()
    assert login(c, "gone", USER_PW).status_code == 401


def test_forced_password_change(app_module, client):
    db = app_module.connect_db(); db.execute("UPDATE users SET must_change_password=1"); db.commit(); db.close()
    login(client)
    r = client.get("/employees")
    assert r.status_code == 302 and "/account/password" in r.headers["Location"]
    tok = csrf(client, "/account/password")
    r = client.post("/account/password", data={"_csrf": tok, "current_password": ADMIN_PW,
                                                 "new_password": "short", "confirm_password": "short"})
    assert "at least 12" in r.get_data(as_text=True)
    new = "A-much-better-Passphrase-42"
    r = client.post("/account/password", data={"_csrf": tok, "current_password": ADMIN_PW,
                                                 "new_password": new, "confirm_password": new})
    assert r.status_code == 302
    assert client.get("/employees").status_code == 200


# ── Sessions ───────────────────────────────────────────────────

def test_logout_requires_post_and_clears_session(admin_client):
    assert admin_client.get("/logout").status_code == 405
    r = post(admin_client, "/logout")
    assert r.status_code == 302
    r = admin_client.get("/employees")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_back_button_after_logout_does_not_show_data(admin_client):
    post(admin_client, "/logout")
    r = admin_client.get("/employees", follow_redirects=True)
    assert "Employee Register" not in r.get_data(as_text=True) or "Sign In" in r.get_data(as_text=True)


def test_demoted_admin_loses_access_immediately(app_module, admin_client, make_user):
    uid = make_user("admin2", role="admin")
    c2 = app_module.app.test_client()
    login(c2, "admin2", USER_PW)
    assert c2.get("/users").status_code == 200
    post(admin_client, "/users/%d/toggle-role" % uid, token_from="/users")
    r = c2.get("/users")
    assert r.status_code == 302  # session invalidated → back to login


def test_deleted_user_session_dies(app_module, admin_client, make_user):
    uid = make_user("temp")
    c2 = app_module.app.test_client()
    login(c2, "temp", USER_PW)
    assert c2.get("/employees").status_code == 200
    post(admin_client, "/users/%d/delete" % uid, token_from="/users")
    assert c2.get("/employees").status_code == 302


def test_forged_role_in_cookie_is_ignored(app_module, user_client):
    with user_client.session_transaction() as s:
        s["role"] = "admin"
    r = user_client.get("/users")
    assert r.status_code == 302  # admin_required reads the DB, not the cookie


def test_inactivity_timeout(app_module, admin_client):
    with admin_client.session_transaction() as s:
        s["last_activity"] = s["last_activity"] - app_module.INACTIVITY_TIMEOUT - 5
    r = admin_client.get("/employees")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


# ── CSRF ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/users/add", "/employees", "/checklist/toggle", "/logout", "/login"])
def test_post_without_csrf_rejected(admin_client, path):
    r = admin_client.post(path, data={"username": "x", "password": "y" * 20})
    assert r.status_code == 400


def test_post_with_wrong_csrf_rejected(admin_client):
    r = admin_client.post("/users/add", data={"_csrf": "forged", "username": "evil", "password": "x" * 20})
    assert r.status_code == 400


def test_json_with_csrf_header_accepted(admin_client):
    tok = csrf(admin_client, "/ee")
    r = admin_client.post("/checklist/toggle", json={"module": "ee", "item_key": "x", "checked": True},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code in (200, 400)  # 400 = unknown item, but not a CSRF rejection
    assert "expired" not in r.get_data(as_text=True)


# ── Authorisation: standard user ───────────────────────────────

ADMIN_GETS = ["/users", "/audit", "/trash"]
ADMIN_POSTS = ["/users/add", "/users/1/reset", "/users/1/toggle-role", "/users/1/toggle-active",
               "/users/1/delete", "/trash/1/restore", "/trash/1/approve", "/trash/1/cancel"]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_user_blocked_from_admin_pages(user_client, path):
    r = user_client.get(path)
    assert r.status_code == 302 and r.headers["Location"].endswith("/")


@pytest.mark.parametrize("path", ADMIN_POSTS)
def test_user_blocked_from_admin_actions(app_module, user_client, path):
    before = query(app_module, "SELECT id, role, active, password_hash FROM users")
    r = post(user_client, path, {"username": "evil", "password": "Evil-Passw0rd-123", "role": "admin"})
    assert r.status_code == 302
    after = query(app_module, "SELECT id, role, active, password_hash FROM users")
    assert [tuple(x) for x in before] == [tuple(x) for x in after]


def test_user_cannot_hard_delete_employee(app_module, user_client):
    db = app_module.connect_db()
    db.execute("INSERT INTO employees (first_name, last_name) VALUES ('A','B')"); db.commit(); db.close()
    post(user_client, "/employees/1/delete")
    assert query(app_module, "SELECT COUNT(*) FROM employees")[0][0] == 1


# ── User management rules ──────────────────────────────────────

def test_cannot_demote_or_disable_or_delete_self(app_module, admin_client):
    for path in ["/users/1/toggle-role", "/users/1/toggle-active", "/users/1/delete"]:
        post(admin_client, path, token_from="/users")
    row = query(app_module, "SELECT role, active FROM users WHERE id=1")[0]
    assert tuple(row) == ("admin", 1)


def test_cannot_remove_last_active_admin(app_module, admin_client, make_user):
    uid = make_user("admin2", role="admin")
    db = app_module.connect_db(); db.execute("UPDATE users SET active=0 WHERE id=?", (uid,)); db.commit(); db.close()
    # admin2 is disabled → admin (id 1) is the last *active* admin; admin2 may be removed
    post(admin_client, "/users/%d/delete" % uid, token_from="/users")
    assert query(app_module, "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1")[0][0] == 1


@pytest.mark.parametrize("pw,ok", [("short", False), ("password123", False), ("aaaaaaaaaaaaaa", False),
                                   ("newuser-secret-99", False), ("Good-Enough-Pass-7", True)])
def test_password_policy_on_create(app_module, admin_client, pw, ok):
    post(admin_client, "/users/add", {"username": "newuser", "password": pw}, token_from="/users")
    exists = bool(query(app_module, "SELECT 1 FROM users WHERE username='newuser'"))
    assert exists == ok


def test_duplicate_username_case_insensitive(app_module, admin_client):
    post(admin_client, "/users/add", {"username": "ADMIN", "password": "Good-Enough-Pass-7"}, token_from="/users")
    assert query(app_module, "SELECT COUNT(*) FROM users")[0][0] == 1


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "a b@c.com"])
def test_invalid_email_rejected(app_module, admin_client, email):
    post(admin_client, "/users/add", {"username": "e1", "password": "Good-Enough-Pass-7", "email": email},
         token_from="/users")
    assert not query(app_module, "SELECT 1 FROM users WHERE username='e1'")


def test_username_with_script_rejected(app_module, admin_client):
    post(admin_client, "/users/add", {"username": "<script>x", "password": "Good-Enough-Pass-7"},
         token_from="/users")
    assert not query(app_module, "SELECT 1 FROM users WHERE username LIKE '<script>%'")


# ── Audit log immutability ─────────────────────────────────────

def test_audit_log_cannot_be_updated_or_deleted(app_module, admin_client):
    import sqlite3
    db = app_module.connect_db()
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE audit_log SET detail='x'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM audit_log")
    db.close()


def test_audit_records_ip_and_user_id(app_module, admin_client):
    row = query(app_module, "SELECT user_id, ip FROM audit_log WHERE action='login'")[0]
    assert row["user_id"] == 1 and row["ip"]


# ── Errors never leak internals ────────────────────────────────

def test_500_page_hides_internals(app_module, admin_client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SECRET_INTERNAL_DETAIL /app/data/hr_audit.db")
    monkeypatch.setattr(app_module.rec, "calendar", boom)
    r = admin_client.get("/")
    body = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "SECRET_INTERNAL_DETAIL" not in body and "Traceback" not in body and "hr_audit.db" not in body


def test_healthz(client):
    assert client.get("/healthz").get_json() == {"ok": True}


# ── Regression: open redirect via Referer, data export ─────────

def test_no_open_redirect_via_referer(app_module, admin_client):
    rid = app_module.connect_db().execute("SELECT id FROM compliance_records WHERE module='cipc_bo'").fetchone()[0]
    tok = csrf(admin_client, "/compliance/%d" % rid)
    r = admin_client.post("/checklist/reset/record:%d" % rid, data={"_csrf": tok},
                          headers={"Referer": "https://evil.example/phish"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_employee_export_admin_only_and_complete(app_module, admin_client, user_client):
    from factories import add_employee
    eid = add_employee(app_module, "EXP1")
    assert user_client.get("/employees/%d/export" % eid).status_code == 302
    r = admin_client.get("/employees/%d/export" % eid)
    assert r.status_code == 200 and r.get_json()["employee"]["emp_number"] == "EXP1"
    assert "employee_export" in [x[0] for x in query(app_module, "SELECT action FROM audit_log")]
