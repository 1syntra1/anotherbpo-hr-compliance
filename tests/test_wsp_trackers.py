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


def test_learnerships_page_loads(admin_client):
    r = admin_client.get("/wsp/learnerships?year=2026")
    assert r.status_code == 200 and b"October" in r.data


def test_learnership_add_update_delete(admin_client, app_module):
    post(admin_client, "/wsp/learnerships/add?year=2026",
         {"year": "2026", "programme_type": "Learnership", "title": "NQF4 Contact Centre",
          "target_count": "15", "recruitment_start": "2026-10-01", "commence_date": "2027-01-15",
          "status": "Planned"}, token_from="/wsp/learnerships")
    rows = query(app_module, "SELECT id, title, target_count, status FROM learnership_plans WHERE year=2026")
    assert rows and rows[0][1] == "NQF4 Contact Centre" and rows[0][2] == 15
    pid = rows[0][0]
    post(admin_client, "/wsp/learnerships/%d/update" % pid,
         {"status": "Recruiting", "target_count": "20"}, token_from="/wsp/learnerships")
    assert query(app_module, "SELECT status, target_count FROM learnership_plans WHERE id=?", (pid,))[0]["status"] == "Recruiting"
    post(admin_client, "/wsp/learnerships/%d/delete" % pid, {}, token_from="/wsp/learnerships")
    assert query(app_module, "SELECT COUNT(*) FROM learnership_plans WHERE id=?", (pid,))[0][0] == 0


def test_training_log_page_loads(admin_client):
    r = admin_client.get("/wsp/training-log?year=2026&month=3")
    assert r.status_code == 200 and b"by intervention" in r.data


def test_training_event_add_and_month_summary(admin_client, app_module):
    post(admin_client, "/wsp/training-log/add",
         {"year": "2026", "month": "3", "training_date": "2026-03-10",
          "intervention_type": "First Aid Level 1", "funding_type": "External",
          "cost": "4500", "num_attendees": "8", "status": "Completed"},
         token_from="/wsp/training-log")
    rows = query(app_module, "SELECT intervention_type, funding_type, cost FROM training_events WHERE training_date LIKE '2026-03%'")
    assert [tuple(r) for r in rows] == [("First Aid Level 1", "External", 4500.0)]
    html = admin_client.get("/wsp/training-log?year=2026&month=3").get_data(as_text=True)
    assert "First Aid Level 1" in html and "4,500.00" in html


def test_training_external_filter(admin_client, app_module):
    post(admin_client, "/wsp/training-log/add",
         {"year": "2026", "month": "4", "training_date": "2026-04-02", "intervention_type": "Induction",
          "funding_type": "Internal", "cost": "0"}, token_from="/wsp/training-log")
    assert "Induction" in admin_client.get("/wsp/training-log?year=2026&month=4").get_data(as_text=True)
    # Filtering to External empties the event list (the only event is Internal)
    ext = admin_client.get("/wsp/training-log?year=2026&month=4&funding=External").get_data(as_text=True)
    assert "No training logged" in ext


def test_document_repository_lists_uploads(admin_client, app_module):
    # create an external training event with a document, then confirm it appears in the repository
    post(admin_client, "/wsp/training-log/add",
         {"year": "2026", "month": "5", "training_date": "2026-05-05", "intervention_type": "Excel Advanced",
          "funding_type": "External", "cost": "2000"}, token_from="/wsp/training-log")
    eid = query(app_module, "SELECT id FROM training_events WHERE intervention_type='Excel Advanced'")[0][0]
    admin_client.post("/wsp/training-log/%d/document" % eid,
                      data={"_csrf": csrf(admin_client, "/wsp/training-log"), "doc_type": "Invoice",
                            "file": (io.BytesIO(b"%PDF-1.4\n%%EOF"), "invoice.pdf")},
                      content_type="multipart/form-data")
    html = admin_client.get("/wsp/documents").get_data(as_text=True)
    assert "invoice.pdf" in html and "Excel Advanced" in html and "Invoice" in html
    # filter by a type with no documents → the invoice is hidden
    html2 = admin_client.get("/wsp/documents?doc_type=Certificate").get_data(as_text=True)
    assert "invoice.pdf" not in html2


def test_wsp_dashboard_aggregates(admin_client):
    # seed one completed external training event + a bursary, then load the dashboard
    post(admin_client, "/wsp/training-log/add",
         {"year": "2026", "month": "6", "training_date": "2026-06-01", "intervention_type": "SHE Rep",
          "funding_type": "External", "cost": "3000", "num_attendees": "5", "status": "Completed"},
         token_from="/wsp/training-log")
    post(admin_client, "/wsp/bursaries/award?year=2026",
         {"year": "2026", "student_name": "Dash Dashboard", "amount_awarded": "15000"}, token_from="/wsp/bursaries")
    r = admin_client.get("/wsp/dashboard?year=2026")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Interventions completed" in html and "Bursaries" in html
    assert "3,000" in html          # training spend
    assert "35,000.00" in html      # bursary remaining = 50000 - 15000


def test_reminders_engine_flags_missing_capture(app_module):
    """A fresh DB (no training/SDL/grants captured) yields reminders, and they render."""
    from datetime import date
    from modules.reminders import compute_wsp_reminders
    db = app_module.connect_db()
    try:
        rem = compute_wsp_reminders(db, today=date(2026, 7, 15))
        titles = " ".join(r["title"] for r in rem)
        assert "Log training for June 2026" in titles          # monthly capture
        assert "EMP201" in titles                               # SDL/EMP201
        assert any("mandatory grant" in r["title"].lower() for r in rem)
    finally:
        db.close()


def test_reminders_render_on_wsp_dashboard(admin_client):
    r = admin_client.get("/wsp/dashboard")
    assert r.status_code == 200
    assert b"Reminders" in r.data
