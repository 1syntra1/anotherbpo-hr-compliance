"""Shared fixtures. Every test gets a fresh app with its own empty data directory."""
import importlib
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ADMIN_PW = "Adm1n-Test-Passw0rd!"
USER_PW = "Us3r-Test-Passw0rd!"

_csrf_re = re.compile(r'name="_csrf" value="([^"]+)"')


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HR_COOKIE_SECURE", "0")
    monkeypatch.setenv("HR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.delenv("HR_AUDIT_SECRET", raising=False)
    if "app" in sys.modules:
        mod = importlib.reload(sys.modules["app"])
    else:
        mod = importlib.import_module("app")
    mod.app.config["TESTING"] = True
    # First-login password change is tested separately; clear it for convenience.
    db = mod.connect_db()
    db.execute("UPDATE users SET must_change_password=0")
    db.commit()
    db.close()
    return mod


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


def csrf(client, path="/login"):
    html = client.get(path).get_data(as_text=True)
    m = _csrf_re.search(html)
    assert m, "no CSRF field on %s" % path
    return m.group(1)


def login(client, username="admin", password=ADMIN_PW):
    tok = csrf(client)
    return client.post("/login", data={"username": username, "password": password, "_csrf": tok})


def post(client, path, data=None, token_from="/"):
    data = dict(data or {})
    data["_csrf"] = csrf(client, token_from)
    return client.post(path, data=data)


@pytest.fixture
def make_user(app_module):
    def _make(username="user1", password=USER_PW, role="user"):
        db = app_module.connect_db()
        db.execute(
            "INSERT INTO users (username, password_hash, role, full_name) VALUES (?,?,?,?)",
            (username, app_module.generate_password_hash(password), role, username),
        )
        db.commit()
        uid = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]
        db.close()
        return uid
    return _make


@pytest.fixture
def admin_client(client):
    r = login(client)
    assert r.status_code == 302
    return client


@pytest.fixture
def user_client(app_module, make_user):
    make_user()
    c = app_module.app.test_client()
    r = login(c, "user1", USER_PW)
    assert r.status_code == 302
    return c


def query(app_module, sql, params=()):
    db = app_module.connect_db()
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()
