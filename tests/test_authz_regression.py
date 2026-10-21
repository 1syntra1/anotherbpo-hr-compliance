"""Security regression tests (from the 2026-10-21 pre-production assessment).

The build should fail if any of these boundaries ever regress:
  - F1  non-admin cannot edit the company profile (/company is admin-only)
  - non-admin is blocked from every admin route, server-side
  - CSRF is required on state-changing requests
  - error pages never leak stack traces / SQL / paths
  - hardening response headers are present
"""
from conftest import ADMIN_PW, USER_PW, csrf, login, post, query


def _count_profiles(app_module):
    return query(app_module, "SELECT COUNT(*) FROM company_profiles")[0][0]


# ── F1: company-profile authorization ────────────────────────────────────────

def test_nonadmin_cannot_edit_company_profile(app_module, user_client):
    before = _count_profiles(app_module)
    r = user_client.post("/company",
                         data={"ownership_notes": "HACKED-BY-NONADMIN",
                               "_csrf": csrf(user_client, "/")})
    assert r.status_code == 302
    assert "/login" not in r.headers["Location"]      # blocked, not logged out
    assert _count_profiles(app_module) == before       # no new version written
    rows = query(app_module,
                 "SELECT COUNT(*) FROM company_profiles WHERE ownership_notes='HACKED-BY-NONADMIN'")
    assert rows[0][0] == 0


def test_nonadmin_cannot_view_company_page(user_client):
    r = user_client.get("/company")
    assert r.status_code == 302                         # governance page is admin-only


def test_admin_can_still_edit_company_profile(app_module, admin_client):
    before = _count_profiles(app_module)
    r = admin_client.post("/company",
                          data={"ownership_notes": "legit-admin-edit",
                                "_csrf": csrf(admin_client, "/company")})
    assert r.status_code == 302
    assert _count_profiles(app_module) == before + 1    # new version created


# ── vertical privilege escalation: admin routes blocked for a normal user ─────

def test_nonadmin_blocked_from_admin_get_routes(user_client):
    for path in ("/users", "/audit", "/trash", "/company"):
        r = user_client.get(path)
        assert r.status_code == 302, "%s should redirect a non-admin" % path
        assert "/login" not in r.headers["Location"], "%s logged the user out instead of blocking" % path


def test_nonadmin_cannot_create_user(app_module, user_client):
    r = user_client.post("/users/add",
                         data={"username": "evil_admin", "full_name": "x",
                               "password": "Zx9-kLmn2Pq!", "role": "admin",
                               "_csrf": csrf(user_client, "/")})
    assert r.status_code == 302
    assert query(app_module, "SELECT COUNT(*) FROM users WHERE username='evil_admin'")[0][0] == 0


# ── CSRF ──────────────────────────────────────────────────────────────────────

def test_csrf_required_on_state_change(admin_client):
    r = admin_client.post("/company", data={"ownership_notes": "x"})   # no _csrf
    assert r.status_code == 400


# ── error handling: no information disclosure ─────────────────────────────────

def test_error_page_leaks_no_internals(admin_client):
    r = admin_client.get("/compliance/99999999")
    assert r.status_code == 404
    body = r.get_data(as_text=True).lower()
    for marker in ("traceback", "sqlite", "sql syntax", ".py\", line", "c:\\"):
        assert marker not in body


# ── hardening headers ─────────────────────────────────────────────────────────

def test_security_headers_present(client):
    h = client.get("/login").headers
    assert h.get("X-Content-Type-Options") == "nosniff"
    assert h.get("X-Frame-Options") == "DENY"
    assert h.get("Referrer-Policy") == "same-origin"
    assert "Content-Security-Policy" in h
    assert "Permissions-Policy" in h
