"""Concurrency and realistic internal load (small deployment: tens of users, up to
10,000 employees). Timings are generous ceilings, not benchmarks."""
import threading
import time

import pytest

from conftest import USER_PW, csrf, login, query
from factories import add_employee, form_post, open_record, versions


def _bulk_employees(app_module, n):
    db = app_module.connect_db()
    levels = ["Top Management", "Senior Management", "Professionally Qualified", "Skilled Technical",
              "Semi-Skilled", "Unskilled"]
    db.executemany(
        """INSERT INTO employees (emp_number, first_name, last_name, race, gender, occupational_level, employment_type,
               salary, employment_date, status, coida_class_code) VALUES (?,?,?,?,?,?,?,?,?, 'ACTIVE', 'A')""",
        [("P%06d" % i, "F%d" % i, "L%d" % i, ["African", "Coloured", "Indian", "White"][i % 4],
          "Male" if i % 2 else "Female", levels[i % 6], "Permanent", 10000 + i % 5000, "2018-01-01")
         for i in range(n)])
    db.commit()
    db.close()


@pytest.mark.parametrize("n", [1000, 5000, 10000])
def test_reports_and_pages_at_scale(app_module, admin_client, n):
    _bulk_employees(app_module, n)
    rid = open_record(admin_client, "ee", 2025)
    timings = {}
    for label, fn in [
        ("dashboard", lambda: admin_client.get("/")),
        ("employee page", lambda: admin_client.get("/employees?page=3")),
        ("employee search", lambda: admin_client.get("/employees?q=L99")),
        ("EE preview", lambda: admin_client.get("/ee")),
        ("EEA2 generate", lambda: form_post(admin_client, "/compliance/%d/generate" % rid, {"report_type": "eea2"},
                                            "/compliance/%d" % rid)),
    ]:
        t = time.perf_counter()
        r = fn()
        timings[label] = time.perf_counter() - t
        assert r.status_code in (200, 302), label
    print("\n%d employees: %s" % (n, ", ".join("%s %.2fs" % kv for kv in timings.items())))
    assert versions(app_module, rid, "eea2")[0]["checks_ok"]
    assert max(timings.values()) < 30


def test_simultaneous_report_generation_never_duplicates_versions(app_module, admin_client):
    for i in range(20):
        add_employee(app_module, "T%d" % i)
    rid = open_record(admin_client, "ee", 2025)
    form_post(admin_client, "/compliance/%d/generate" % rid, {"report_type": "eea2"}, "/compliance/%d" % rid)
    tok = csrf(admin_client, "/compliance/%d" % rid)
    errors = []

    def worker(k):
        try:
            r = admin_client.post("/compliance/%d/generate" % rid,
                                  data={"_csrf": tok, "report_type": "eea2", "reason": "concurrent %d" % k})
            if r.status_code >= 500:
                errors.append(r.status_code)
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    vs = [v["version"] for v in versions(app_module, rid, "eea2")]
    assert not errors
    assert vs == list(range(1, len(vs) + 1))  # contiguous, no duplicates, nothing overwritten


def test_two_users_editing_same_employee_no_silent_loss(app_module, admin_client, make_user):
    make_user("u2")
    c2 = app_module.app.test_client()
    login(c2, "u2", USER_PW)
    eid = add_employee(app_module, "SAME1")
    rv = query(app_module, "SELECT row_version FROM employees WHERE id=?", (eid,))[0][0]
    base = {"emp_number": "SAME1", "first_name": "A", "last_name": "B", "race": "African", "gender": "Female",
            "occupational_level": "Unskilled", "employment_date": "2020-01-01", "row_version": rv}
    r1 = form_post(admin_client, "/employees/%d/edit" % eid, dict(base, salary="11111"), "/employees")
    r2 = form_post(c2, "/employees/%d/edit" % eid, dict(base, salary="22222"), "/employees")  # stale row_version
    assert query(app_module, "SELECT salary FROM employees WHERE id=?", (eid,))[0][0] == 11111
    assert r2.status_code == 302


def test_simultaneous_overlapping_imports(app_module, admin_client, make_user):
    import io
    make_user("u3")
    c2 = app_module.app.test_client()
    login(c2, "u3", USER_PW)
    body = ("emp_number,first_name,last_name,race,gender,occupational_level,salary,employment_date\n" +
            "".join("D%d,A,B,African,Female,Unskilled,9000,2021-03-01\n" % i for i in range(300))).encode()
    results = []

    def run(client):
        tok = csrf(client, "/employees")
        r = client.post("/employees/upload", data={"_csrf": tok, "file": (io.BytesIO(body), "e.csv")},
                        content_type="multipart/form-data")
        results.append(r.status_code)

    ts = [threading.Thread(target=run, args=(c,)) for c in (admin_client, c2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert all(s < 500 for s in results)
    assert query(app_module, "SELECT COUNT(*) FROM employees")[0][0] == 300   # exactly one import won
