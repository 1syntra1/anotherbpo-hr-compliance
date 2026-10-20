"""WSP operational trackers (SDL/EMP201, grants, learnerships, bursaries)."""
from tests.conftest import csrf, post, query


def test_sdl_page_loads(admin_client):
    r = admin_client.get("/wsp/sdl?year=2026")
    assert r.status_code == 200
    assert b"SDL" in r.data and b"January" in r.data


def test_sdl_save_and_reload(admin_client, app_module):
    r = post(admin_client, "/wsp/sdl?year=2026",
             {"year": "2026", "recv_3": "1", "payable_3": "1500.00", "paid_3": "1500.00",
              "rdate_3": "2026-04-07", "notes_3": "March levy"}, token_from="/wsp/sdl")
    assert r.status_code in (302, 303)
    rows = query(app_module, "SELECT emp201_received, sdl_payable, sdl_paid, received_date FROM sdl_tracker WHERE period_month='2026-03'")
    assert [tuple(r) for r in rows] == [(1, 1500.0, 1500.0, "2026-04-07")]
    # reloads with the saved value
    html = admin_client.get("/wsp/sdl?year=2026").get_data(as_text=True)
    assert "1500.00" in html


def test_sdl_rejects_bad_amount(admin_client, app_module):
    post(admin_client, "/wsp/sdl?year=2026",
         {"year": "2026", "payable_5": "-10"}, token_from="/wsp/sdl")
    rows = query(app_module, "SELECT COUNT(*) FROM sdl_tracker WHERE period_month='2026-05'")
    assert rows[0][0] == 0  # nothing saved when validation fails


def test_grants_page_loads(admin_client):
    r = admin_client.get("/wsp/grants?year=2026")
    assert r.status_code == 200 and b"Q1" in r.data


def test_grants_save(admin_client, app_module):
    post(admin_client, "/wsp/grants?year=2026",
         {"year": "2026", "amount_Q2": "25000", "date_Q2": "2026-08-01", "status_Q2": "Received"},
         token_from="/wsp/grants")
    rows = query(app_module, "SELECT amount_received, status FROM grants WHERE year=2026 AND quarter='Q2' AND grant_type='Mandatory'")
    assert [tuple(r) for r in rows] == [(25000.0, "Received")]
