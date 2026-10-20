"""Synthetic test data. No real personal information is used anywhere in the tests."""
import io
import re

from conftest import csrf

BASE_EMP = {
    "first_name": "Test", "last_name": "Person", "race": "African", "gender": "Female", "disability": "No",
    "is_foreign_national": 0, "occupational_level": "Skilled Technical", "employment_type": "Permanent",
    "department": "Ops", "job_title": "Agent", "salary": 20000.0, "employment_date": "2020-01-01",
    "termination_date": None, "status": "ACTIVE", "coida_class_code": "A", "coida_subclass": "",
}


def add_employee(app_module, emp_number, **over):
    e = dict(BASE_EMP, emp_number=emp_number, **over)
    if e["termination_date"] and "status" not in over:
        e["status"] = "TERMINATED"
    cols = list(e)
    db = app_module.connect_db()
    cur = db.execute("INSERT INTO employees (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                     [e[c] for c in cols])
    db.commit()
    eid = cur.lastrowid
    db.close()
    return eid


def form_post(client, path, data, token_from):
    data = dict(data)
    data["_csrf"] = csrf(client, token_from)
    return client.post(path, data=data)


def open_record(client, module, year, **extra):
    data = {"module": module, "period_year": str(year)}
    data.update(extra)
    r = form_post(client, "/compliance/new", data, "/compliance")
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    m = re.search(r"/compliance/(\d+)", r.headers["Location"])
    assert m, r.headers["Location"]
    return int(m.group(1))


def set_details(client, rid, app_module, **fields):
    row = app_module.connect_db().execute("SELECT row_version FROM compliance_records WHERE id=?", (rid,)).fetchone()
    data = dict(fields, row_version=row[0])
    return form_post(client, "/compliance/%d/details" % rid, data, "/compliance/%d" % rid)


def generate(client, rid, report_type, reason=""):
    return form_post(client, "/compliance/%d/generate" % rid, {"report_type": report_type, "reason": reason},
                     "/compliance/%d" % rid)


def act(client, app_module, rid, action, **data):
    db = app_module.connect_db()
    rv = db.execute("SELECT row_version FROM compliance_records WHERE id=?", (rid,)).fetchone()[0]
    db.close()
    payload = dict(data, action=action, row_version=rv)
    return form_post(client, "/compliance/%d/transition" % rid, payload, "/compliance/%d" % rid)


def status(app_module, rid):
    db = app_module.connect_db()
    s = db.execute("SELECT status FROM compliance_records WHERE id=?", (rid,)).fetchone()[0]
    db.close()
    return s


def versions(app_module, rid, report_type=None):
    db = app_module.connect_db()
    q = "SELECT * FROM report_versions WHERE record_id=?"
    args = [rid]
    if report_type:
        q += " AND report_type=?"
        args.append(report_type)
    rows = [dict(r) for r in db.execute(q + " ORDER BY id", args)]
    db.close()
    return rows


def xlsx_values(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def find_row(sheet_rows, first_cell):
    for row in sheet_rows:
        if row and row[0] == first_cell:
            return row
    raise AssertionError("row %r not found" % first_cell)
