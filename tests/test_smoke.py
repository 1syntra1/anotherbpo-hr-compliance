"""Every page renders for the roles that may see it, with no server error."""
import pytest

from conftest import post

PAGES = ["/", "/employees", "/ee", "/gbs", "/wsp", "/bbbee", "/coida", "/account/password",
         "/employees/template", "/compliance", "/company", "/rules", "/compliance/1",
         "/employees?status=ALL", "/employees?status=ARCHIVED&q=x&sort=start&level=Unskilled"]
ADMIN_PAGES = ["/users", "/audit", "/trash"]
PUBLIC = ["/home", "/login", "/healthz"]


@pytest.mark.parametrize("path", PAGES + ADMIN_PAGES)
def test_admin_pages_render(admin_client, path):
    r = admin_client.get(path)
    assert r.status_code == 200, path


@pytest.mark.parametrize("path", PAGES)
def test_user_pages_render(user_client, path):
    assert user_client.get(path).status_code == 200


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_render(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", PAGES + ADMIN_PAGES)
def test_anonymous_redirected_to_login(client, path):
    r = client.get(path)
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_no_cdn_assets(client):
    for path in PUBLIC[:2]:
        assert "cdn.jsdelivr" not in client.get(path).get_data(as_text=True)


def test_gbs_project_page_renders(admin_client):
    r = post(admin_client, "/gbs/new", {"quarter": "Q1", "year": "2026"}, token_from="/gbs")
    assert r.status_code == 302
    assert admin_client.get(r.headers["Location"]).status_code == 200


def test_legacy_report_urls_redirect_to_records(admin_client):
    for path in ("/ee/generate", "/wsp/generate", "/bbbee/generate", "/coida/generate"):
        r = post(admin_client, path, {"report_type": "x"})
        assert r.status_code == 302 and "/compliance" in r.headers["Location"]
