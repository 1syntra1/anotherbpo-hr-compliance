"""Compliance records, workflow enforcement, report versioning and historical integrity."""
import io
import sqlite3

import pytest

from conftest import USER_PW, csrf, login, query
from factories import (act, add_employee, find_row, form_post, generate, open_record, set_details, status,
                       versions, xlsx_values)


@pytest.fixture
def two_users(app_module, admin_client, make_user):
    """admin (id 1) plus preparer 'prep' and reviewer 'rev' (standard users)."""
    prep = make_user("prep")
    rev = make_user("rev")
    cp = app_module.app.test_client(); login(cp, "prep", USER_PW)
    cr = app_module.app.test_client(); login(cr, "rev", USER_PW)
    return {"admin": admin_client, "prep": cp, "rev": cr, "prep_id": prep, "rev_id": rev}


def _ee_ready(app_module, client, year=2025):
    add_employee(app_module, "E1")
    add_employee(app_module, "E2", gender="Male", race="White", occupational_level="Top Management", salary=90000)
    return open_record(client, "ee", year)


# ── Records ────────────────────────────────────────────────────

def test_record_created_with_suggested_period(app_module, admin_client):
    rid = open_record(admin_client, "ee", 2025)
    r = query(app_module, "SELECT * FROM compliance_records WHERE id=?", (rid,))[0]
    assert (r["period_label"], r["period_start"], r["period_end"], r["due_date"]) == \
        ("2024/2025", "2024-10-01", "2025-09-30", "2026-01-15")
    assert r["created_by"] == 1 and r["status"] == "DRAFT" and r["rule_set_id"]


def test_duplicate_period_refused(app_module, admin_client):
    open_record(admin_client, "coida", 2033)
    r = form_post(admin_client, "/compliance/new", {"module": "coida", "period_year": "2033"}, "/compliance")
    assert r.status_code == 302 and r.headers["Location"].endswith("/compliance")
    assert query(app_module, "SELECT COUNT(*) FROM compliance_records WHERE module='coida' AND period_label='2033'")[0][0] == 1


def test_duplicate_period_refused_at_database_level(app_module):
    db = app_module.connect_db()
    db.execute("INSERT INTO compliance_records (module, period_label) VALUES ('wsp', 'X')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO compliance_records (module, period_label) VALUES ('wsp', 'X')")
    db.close()


def test_invalid_period_dates_refused(app_module, admin_client):
    r = form_post(admin_client, "/compliance/new", {"module": "wsp", "period_year": "2032", "period_start": "2026-05-01",
                                                     "period_end": "2026-01-01"}, "/compliance")
    assert not query(app_module, "SELECT 1 FROM compliance_records WHERE module='wsp' AND period_label='2032'")


# ── Workflow enforcement ───────────────────────────────────────

def test_cannot_prepare_without_reports(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    act(admin_client, app_module, rid, "prepare")
    assert status(app_module, rid) == "DRAFT"


def test_full_happy_path_with_separation_of_duties(app_module, two_users):
    a, p, rv = two_users["admin"], two_users["prep"], two_users["rev"]
    rid = _ee_ready(app_module, p)
    set_details(a, rid, app_module, owner_id=two_users["prep_id"], reviewer_id=two_users["rev_id"])
    assert generate(p, rid, "eea2").status_code == 302
    assert generate(p, rid, "eea4").status_code == 302
    assert all(v["checks_ok"] for v in versions(app_module, rid))
    act(p, app_module, rid, "prepare");        assert status(app_module, rid) == "PREPARED"
    act(p, app_module, rid, "request_review"); assert status(app_module, rid) == "AWAITING_REVIEW"
    act(p, app_module, rid, "approve");        assert status(app_module, rid) == "AWAITING_REVIEW"  # preparer
    act(rv, app_module, rid, "approve");       assert status(app_module, rid) == "APPROVED"
    act(p, app_module, rid, "submit", submitted_on="2025-12-01", reference_no="EE-123", confirmation_no="C9")
    assert status(app_module, rid) == "SUBMITTED"
    sub = query(app_module, "SELECT * FROM submissions WHERE record_id=?", (rid,))[0]
    assert sub["reference_no"] == "EE-123" and sub["report_version_ids"]
    events = [e["event"] for e in query(app_module, "SELECT event FROM workflow_events WHERE record_id=? ORDER BY id", (rid,))]
    for e in ("created", "report_generated", "prepare", "request_review", "approve", "submit"):
        assert e in events


def test_request_review_needs_reviewer(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    generate(admin_client, rid, "eea2"); generate(admin_client, rid, "eea4")
    act(admin_client, app_module, rid, "prepare")
    act(admin_client, app_module, rid, "request_review")
    assert status(app_module, rid) == "PREPARED"


def test_admin_self_approval_needs_override_reason(app_module, admin_client, make_user):
    rid = _ee_ready(app_module, admin_client)
    set_details(admin_client, rid, app_module, reviewer_id=1)
    generate(admin_client, rid, "eea2"); generate(admin_client, rid, "eea4")
    act(admin_client, app_module, rid, "prepare"); act(admin_client, app_module, rid, "request_review")
    act(admin_client, app_module, rid, "approve")
    assert status(app_module, rid) == "AWAITING_REVIEW"
    act(admin_client, app_module, rid, "approve", override_reason="Sole HR administrator this week")
    assert status(app_module, rid) == "APPROVED"
    ev = query(app_module, "SELECT comment FROM workflow_events WHERE record_id=? AND event='approve'", (rid,))[0]
    assert "Override" in ev["comment"]


def test_unassigned_user_cannot_approve(app_module, two_users, make_user):
    a = two_users["admin"]
    rid = _ee_ready(app_module, a)
    set_details(a, rid, app_module, reviewer_id=two_users["rev_id"])
    generate(a, rid, "eea2"); generate(a, rid, "eea4")
    act(a, app_module, rid, "prepare"); act(a, app_module, rid, "request_review")
    act(two_users["prep"], app_module, rid, "approve")  # not the reviewer
    assert status(app_module, rid) == "AWAITING_REVIEW"


@pytest.mark.parametrize("forged", [
    {"action": "submit"}, {"action": "approve"}, {"action": "archive"}, {"action": "reopen", "comment": "x"},
    {"action": "status", "status": "SUBMITTED"}, {"action": "submit", "status": "APPROVED"},
])
def test_cannot_bypass_workflow_by_post_manipulation(app_module, admin_client, forged):
    rid = _ee_ready(app_module, admin_client)
    data = dict(forged, submitted_on="2025-01-01", reference_no="X")
    form_post(admin_client, "/compliance/%d/transition" % rid, data, "/compliance/%d" % rid)
    assert status(app_module, rid) == "DRAFT"


def test_failed_checks_block_preparation(app_module, admin_client):
    add_employee(app_module, "E1")
    add_employee(app_module, "BAD", gender=None)  # missing gender → excluded → reconciliation fails
    rid = open_record(admin_client, "ee", 2025)
    generate(admin_client, rid, "eea2"); generate(admin_client, rid, "eea4")
    assert not versions(app_module, rid, "eea2")[0]["checks_ok"]
    act(admin_client, app_module, rid, "prepare")
    assert status(app_module, rid) == "DRAFT"


def test_regenerate_requires_reason_and_keeps_old_version(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    generate(admin_client, rid, "eea2")
    generate(admin_client, rid, "eea2")  # no reason → refused
    assert len(versions(app_module, rid, "eea2")) == 1
    generate(admin_client, rid, "eea2", reason="Corrected employee information")
    vs = versions(app_module, rid, "eea2")
    assert [v["version"] for v in vs] == [1, 2] and vs[1]["reason"] == "Corrected employee information"


def test_report_versions_are_immutable(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    generate(admin_client, rid, "eea2")
    db = app_module.connect_db()
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE report_versions SET snapshot_json='{}'")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM report_versions")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM workflow_events")
    db.close()


def _submit(app_module, client, rid):
    set_details(client, rid, app_module, reviewer_id=1)
    generate(client, rid, "eea2"); generate(client, rid, "eea4")
    act(client, app_module, rid, "prepare"); act(client, app_module, rid, "request_review")
    act(client, app_module, rid, "approve", override_reason="test")
    act(client, app_module, rid, "submit", submitted_on="2025-12-01", reference_no="R1")
    assert status(app_module, rid) == "SUBMITTED"


def test_submitted_record_is_locked(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    _submit(app_module, admin_client, rid)
    generate(admin_client, rid, "eea2", reason="sneaky")
    assert len(versions(app_module, rid, "eea2")) == 1
    tok = csrf(admin_client, "/compliance/%d" % rid)
    r = admin_client.post("/checklist/toggle", json={"module": "record:%d" % rid, "item_key": "e1a", "checked": True},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code == 409
    act(admin_client, app_module, rid, "submit", submitted_on="2025-12-02", reference_no="R2")  # replay
    assert query(app_module, "SELECT COUNT(*) FROM submissions WHERE record_id=?", (rid,))[0][0] == 1


def test_only_admin_reopens_with_reason(app_module, admin_client, user_client):
    rid = _ee_ready(app_module, admin_client)
    _submit(app_module, admin_client, rid)
    act(user_client, app_module, rid, "reopen", comment="please")
    assert status(app_module, rid) == "SUBMITTED"
    act(admin_client, app_module, rid, "reopen")  # no reason
    assert status(app_module, rid) == "SUBMITTED"
    act(admin_client, app_module, rid, "reopen", comment="Department queried figures")
    assert status(app_module, rid) == "REQUIRES_CHANGES"
    # previous submission stays on record
    assert query(app_module, "SELECT COUNT(*) FROM submissions WHERE record_id=?", (rid,))[0][0] == 1


def test_stale_form_cannot_overwrite(app_module, admin_client):
    rid = _ee_ready(app_module, admin_client)
    old_rv = query(app_module, "SELECT row_version FROM compliance_records WHERE id=?", (rid,))[0][0]
    set_details(admin_client, rid, app_module, notes="first")
    form_post(admin_client, "/compliance/%d/details" % rid, {"notes": "stale", "row_version": old_rv},
              "/compliance/%d" % rid)
    assert query(app_module, "SELECT notes FROM compliance_records WHERE id=?", (rid,))[0][0] == "first"


# ── Historical integrity: the John scenario ────────────────────

def test_john_scenario_history_is_preserved(app_module, admin_client):
    for i in range(3):
        add_employee(app_module, "C%d" % i)
    john = add_employee(app_module, "JOHN1", first_name="John", last_name="Tester", race="Indian", gender="Male",
                        occupational_level="Senior Management", department="Finance", salary=50000)
    rid = open_record(admin_client, "ee", 2025)
    generate(admin_client, rid, "eea2"); generate(admin_client, rid, "eea4")
    v1 = {v["report_type"]: v for v in versions(app_module, rid)}
    before2 = admin_client.get("/reports/%d/download" % v1["eea2"]["id"]).data
    before4 = admin_client.get("/reports/%d/download" % v1["eea4"]["id"]).data
    old2, old4 = xlsx_values(before2), xlsx_values(before4)
    assert find_row(old2["EEA2"], "Senior management")[-1] == 1

    # Change everything about John, then terminate and archive him
    db = app_module.connect_db()
    db.execute("""UPDATE employees SET salary=99000, department='IT', occupational_level='Top Management',
                  race='White', gender='Female' WHERE id=?""", (john,))
    db.commit(); db.close()
    form_post(admin_client, "/employees/%d/lifecycle" % john,
              {"action": "terminate", "termination_date": "2025-06-30",
               "row_version": query(app_module, "SELECT row_version FROM employees WHERE id=?", (john,))[0][0]},
              "/employees")
    form_post(admin_client, "/employees/%d/lifecycle" % john,
              {"action": "archive", "row_version": query(app_module, "SELECT row_version FROM employees WHERE id=?", (john,))[0][0]},
              "/employees?status=TERMINATED")
    assert query(app_module, "SELECT status FROM employees WHERE id=?", (john,))[0][0] == "ARCHIVED"
    db = app_module.connect_db(); db.execute("UPDATE company_profiles SET company_name='x'") if False else None; db.close()

    # New current report reflects the change
    generate(admin_client, rid, "eea2", reason="John left")
    v2 = versions(app_module, rid, "eea2")[-1]
    new2 = xlsx_values(admin_client.get("/reports/%d/download" % v2["id"]).data)
    assert find_row(new2["EEA2"], "Senior management")[-1] == 0      # gone from population at period end
    assert find_row(new2["EEA2"], "Top management")[-1] == 0         # terminated before period end
    term_rows = [r for r in new2["EEA2"] if r and r[0] == "Top management"]
    assert term_rows[-1][-1] == 1                                    # appears under terminations

    # The old reports are byte-for-byte unchanged and still reproduce from their snapshot
    assert admin_client.get("/reports/%d/download" % v1["eea2"]["id"]).data == before2
    assert admin_client.get("/reports/%d/download" % v1["eea4"]["id"]).data == before4
    r = form_post(admin_client, "/reports/%d/verify" % v1["eea2"]["id"], {}, "/compliance/%d" % rid)
    page = admin_client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Verified:" in page
    assert xlsx_values(app_module.reports_store.rerender(app_module.connect_db(), v1["eea2"])) == old2


def test_company_change_does_not_alter_old_report(app_module, admin_client):
    form_post(admin_client, "/company", {"company_name": "Alpha Pty Ltd", "reg_number": "2020/000001/07"}, "/company")
    rid = _ee_ready(app_module, admin_client)
    generate(admin_client, rid, "eea2")
    v1 = versions(app_module, rid, "eea2")[0]
    form_post(admin_client, "/company", {"company_name": "Beta Pty Ltd"}, "/company")
    assert query(app_module, "SELECT COUNT(*) FROM company_profiles")[0][0] == 2
    data = xlsx_values(admin_client.get("/reports/%d/download" % v1["id"]).data)
    flat = [c for row in data["EEA2"] for c in row]
    assert "Alpha Pty Ltd" in flat and "Beta Pty Ltd" not in flat
    db = app_module.connect_db()
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE company_profiles SET company_name='hacked'")
    db.close()


def test_tampered_report_file_detected(app_module, admin_client):
    import os
    rid = _ee_ready(app_module, admin_client)
    generate(admin_client, rid, "eea2")
    v = versions(app_module, rid, "eea2")[0]
    path = os.path.join(app_module.DATA_DIR, "reports", v["stored_name"])
    with open(path, "ab") as fh:
        fh.write(b"tamper")
    r = admin_client.get("/reports/%d/download" % v["id"])
    assert r.status_code == 302  # refused, with a message


# ── Evidence ───────────────────────────────────────────────────

def _upload(client, rid, name, data, kind="Supporting document"):
    tok = csrf(client, "/compliance/%d" % rid)
    return client.post("/compliance/%d/evidence" % rid, data={"_csrf": tok, "kind": kind,
                                                              "file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_evidence_upload_and_download(app_module, admin_client):
    rid = open_record(admin_client, "cipc_bo", 2031)
    _upload(admin_client, rid, "confirmation.pdf", b"%PDF-1.4 test")
    row = query(app_module, "SELECT * FROM evidence_files WHERE record_id=?", (rid,))[0]
    assert row["stored_name"] != "confirmation.pdf" and row["stored_name"].endswith(".pdf")
    r = admin_client.get("/evidence/%d" % row["id"])
    assert r.status_code == 200 and r.data == b"%PDF-1.4 test"
    assert "attachment" in r.headers["Content-Disposition"]


@pytest.mark.parametrize("name,data", [
    ("invoice.pdf", b"MZ\x90\x00 fake exe"),           # renamed executable
    ("script.pdf.exe", b"MZ"),                           # double extension
    ("shell.php", b"<?php system($_GET['c']); ?>"),
    ("page.html", b"<script>alert(1)</script>"),
    ("macro.xlsm", b"PK\x03\x04"),
    ("empty.pdf", b""),
    ("noext", b"%PDF"),
])
def test_dangerous_evidence_rejected(app_module, admin_client, name, data):
    rid = open_record(admin_client, "cipc_bo", 2031)
    _upload(admin_client, rid, name, data)
    assert not query(app_module, "SELECT 1 FROM evidence_files")


def test_evidence_path_traversal_name_is_neutralised(app_module, admin_client):
    import os
    rid = open_record(admin_client, "cipc_bo", 2031)
    _upload(admin_client, rid, "../../../../etc/passwd.pdf", b"%PDF-1.4")
    _upload(admin_client, rid, "..\\..\\windows\\win.ini.pdf", b"%PDF-1.4")
    rows = query(app_module, "SELECT * FROM evidence_files")
    assert len(rows) == 2
    for r in rows:
        assert "/" not in r["original_name"] and "\\" not in r["original_name"]
        assert os.path.exists(os.path.join(app_module.DATA_DIR, "evidence", r["stored_name"]))


def test_oversized_upload_rejected(app_module, admin_client):
    rid = open_record(admin_client, "cipc_bo", 2031)
    big = b"%PDF" + b"0" * (app_module.MAX_UPLOAD_MB * 1024 * 1024 + 10)
    r = _upload(admin_client, rid, "big.pdf", big)
    assert r.status_code == 413
    assert not query(app_module, "SELECT 1 FROM evidence_files")


def test_unknown_ids_return_404(admin_client):
    for path in ("/compliance/99999", "/reports/99999/download", "/evidence/99999"):
        assert admin_client.get(path).status_code == 404


def test_evidence_cannot_be_deleted(app_module, admin_client):
    rid = open_record(admin_client, "cipc_bo", 2031)
    _upload(admin_client, rid, "c.pdf", b"%PDF-1.4")
    db = app_module.connect_db()
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM evidence_files")
    db.close()


# ── Checklists ─────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["ee", "gbs:1", "record:9999", "record:abc", "../etc", ""])
def test_checklist_rejects_unknown_scopes(admin_client, scope):
    tok = csrf(admin_client, "/")
    r = admin_client.post("/checklist/toggle", json={"module": scope, "item_key": "e1a", "checked": True},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code in (400, 404)


def test_checklist_reset_keeps_history(app_module, admin_client):
    rid = open_record(admin_client, "cipc_bo", 2031)
    tok = csrf(admin_client, "/compliance/%d" % rid)
    r = admin_client.post("/checklist/toggle", json={"module": "record:%d" % rid, "item_key": "o1a", "checked": True},
                          headers={"X-CSRF-Token": tok})
    assert r.get_json()["done"] == 1
    form_post(admin_client, "/checklist/reset/record:%d" % rid, {}, "/compliance/%d" % rid)
    ev = query(app_module, "SELECT payload_json FROM workflow_events WHERE record_id=? AND event='checklist_reset'", (rid,))
    assert '"o1a": true' in ev[0][0]


# ── GBS ────────────────────────────────────────────────────────

def test_gbs_one_claim_per_quarter_and_year_rollover(app_module, admin_client):
    for q, y in (("Q4", 2025), ("Q1", 2026)):
        form_post(admin_client, "/gbs/new", {"quarter": q, "year": str(y)}, "/gbs")
    form_post(admin_client, "/gbs/new", {"quarter": "Q4", "year": "2025", "force": "on"}, "/gbs")
    rows = query(app_module, "SELECT period_label, period_start, period_end FROM compliance_records WHERE module='gbs' ORDER BY id")
    assert [tuple(r) for r in rows] == [("Q4 2025", "2025-10-01", "2025-12-31"), ("Q1 2026", "2026-01-01", "2026-03-31")]


def test_gbs_submit_route_cannot_bypass_approval(app_module, admin_client):
    form_post(admin_client, "/gbs/new", {"quarter": "Q2", "year": "2026"}, "/gbs")
    form_post(admin_client, "/gbs/1/submit", {}, "/gbs")
    assert query(app_module, "SELECT status FROM gbs_projects")[0][0] != "Submitted"
    assert query(app_module, "SELECT status FROM compliance_records WHERE module='gbs'")[0][0] == "DRAFT"


def test_gbs_trash_restore_and_purge_keep_history(app_module, admin_client, user_client):
    form_post(admin_client, "/gbs/new", {"quarter": "Q3", "year": "2026"}, "/gbs")
    pid = query(app_module, "SELECT id, record_id FROM gbs_projects")[0]
    form_post(user_client, "/gbs/%d/delete" % pid[0], {}, "/gbs")
    assert query(app_module, "SELECT deleted FROM compliance_records WHERE id=?", (pid[1],))[0][0] == 1
    form_post(user_client, "/trash/%d/restore" % pid[0], {}, "/gbs")  # standard user: blocked
    assert query(app_module, "SELECT trashed FROM gbs_projects")[0][0] == 1
    form_post(admin_client, "/trash/%d/approve" % pid[0], {}, "/trash")
    db = app_module.connect_db()
    db.execute("UPDATE gbs_projects SET purge_at='2000-01-01 00:00:00'"); db.commit(); db.close()
    admin_client.get("/gbs")
    assert not query(app_module, "SELECT 1 FROM gbs_projects")
    assert query(app_module, "SELECT COUNT(*) FROM workflow_events WHERE record_id=?", (pid[1],))[0][0] >= 3


def test_submitted_gbs_claim_cannot_be_trashed(app_module, admin_client):
    form_post(admin_client, "/gbs/new", {"quarter": "Q1", "year": "2027"}, "/gbs")
    pid, rid = query(app_module, "SELECT id, record_id FROM gbs_projects")[0]
    set_details(admin_client, rid, app_module, reviewer_id=1)
    act(admin_client, app_module, rid, "prepare"); act(admin_client, app_module, rid, "request_review")
    act(admin_client, app_module, rid, "approve", override_reason="t")
    act(admin_client, app_module, rid, "submit", submitted_on="2026-01-01", reference_no="GBS-1")
    assert query(app_module, "SELECT status FROM gbs_projects")[0][0] == "Submitted"
    form_post(admin_client, "/gbs/%d/delete" % pid, {}, "/gbs")
    assert query(app_module, "SELECT trashed FROM gbs_projects")[0][0] == 0


# ── Dashboard ──────────────────────────────────────────────────

def test_dashboard_health_and_notifications(app_module, admin_client):
    rid = open_record(admin_client, "cipc_bo", 2020, due_date="2020-01-01")
    html = admin_client.get("/").get_data(as_text=True)
    assert "Overdue by" in html and "is overdue" in html and "Compliance health" in html


def test_audit_has_before_after_for_employee_edit(app_module, admin_client):
    eid = add_employee(app_module, "AUD1", salary=32000)
    rv = query(app_module, "SELECT row_version FROM employees WHERE id=?", (eid,))[0][0]
    data = {k: v for k, v in dict(query(app_module, "SELECT * FROM employees WHERE id=?", (eid,))[0]).items()
            if v is not None and k not in ("id", "created_at", "updated_at", "updated_by", "row_version", "status")}
    data.update(salary="35000", row_version=rv, is_foreign_national="No")
    r = form_post(admin_client, "/employees/%d/edit" % eid, data, "/employees/%d/edit" % eid)
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]
    row = query(app_module, "SELECT before_json, after_json FROM audit_log WHERE action='employee_update'")[0]
    assert "32000" in row["before_json"] and "35000" in row["after_json"]
