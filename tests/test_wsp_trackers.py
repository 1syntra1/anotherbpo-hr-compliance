"""WSP operational trackers (SDL/EMP201, grants, learnerships, bursaries)."""
import io

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


def test_bursaries_default_budget(admin_client):
    html = admin_client.get("/wsp/bursaries?year=2026").get_data(as_text=True)
    assert "50,000.00" in html and "/ 10" in html  # Dash default: R50k across 10


def test_bursary_award_and_remaining(admin_client, app_module):
    post(admin_client, "/wsp/bursaries/award?year=2026",
         {"year": "2026", "student_name": "Test Student", "institution": "UCT",
          "qualification": "BSc", "amount_awarded": "20000", "status": "Awarded"},
         token_from="/wsp/bursaries")
    rows = query(app_module, "SELECT student_name, amount_awarded FROM bursary_awards WHERE year=2026")
    assert [tuple(r) for r in rows] == [("Test Student", 20000.0)]
    html = admin_client.get("/wsp/bursaries?year=2026").get_data(as_text=True)
    assert "30,000.00" in html  # remaining = 50000 - 20000


def test_bursary_document_upload_download_delete(admin_client, app_module):
    post(admin_client, "/wsp/bursaries/award?year=2026",
         {"year": "2026", "student_name": "Doc Student", "amount_awarded": "1000"}, token_from="/wsp/bursaries")
    aid = query(app_module, "SELECT id FROM bursary_awards WHERE student_name='Doc Student'")[0][0]
    pdf = b"%PDF-1.4\ntest\n%%EOF\n"
    r = admin_client.post("/wsp/bursaries/award/%d/document" % aid,
                          data={"_csrf": csrf(admin_client, "/wsp/bursaries"), "doc_type": "Signed agreement",
                                "file": (io.BytesIO(pdf), "agreement.pdf")},
                          content_type="multipart/form-data")
    assert r.status_code in (302, 303)
    docs = query(app_module, "SELECT id, original_name FROM wsp_documents WHERE entity_type='bursary' AND entity_id=?", (aid,))
    assert docs and docs[0][1] == "agreement.pdf"
    did = docs[0][0]
    dl = admin_client.get("/wsp/document/%d" % did)
    assert dl.status_code == 200 and dl.data == pdf
    admin_client.post("/wsp/document/%d/delete" % did, data={"_csrf": csrf(admin_client, "/wsp/bursaries")})
    assert query(app_module, "SELECT COUNT(*) FROM wsp_documents WHERE id=?", (did,))[0][0] == 0


def test_bursary_rejects_disguised_executable(admin_client, app_module):
    post(admin_client, "/wsp/bursaries/award?year=2026",
         {"year": "2026", "student_name": "X", "amount_awarded": "1"}, token_from="/wsp/bursaries")
    aid = query(app_module, "SELECT id FROM bursary_awards WHERE student_name='X'")[0][0]
    admin_client.post("/wsp/bursaries/award/%d/document" % aid,
                      data={"_csrf": csrf(admin_client, "/wsp/bursaries"), "doc_type": "Invoice",
                            "file": (io.BytesIO(b"MZ\x90\x00 not a pdf"), "evil.pdf")},
                      content_type="multipart/form-data")
    assert query(app_module, "SELECT COUNT(*) FROM wsp_documents WHERE entity_id=?", (aid,))[0][0] == 0
