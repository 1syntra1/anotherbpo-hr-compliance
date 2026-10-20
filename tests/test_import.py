"""Employee validation and transactional import."""
import io
import time
from datetime import date

import pytest

from conftest import csrf, query
from modules.employees import sa_id_valid, validate

HDR = "emp_number,first_name,last_name,race,gender,occupational_level,salary,employment_date\n"
ROW = "E{n},Thandi,Mokoena,African,Female,Unskilled,9000,2021-03-01\n"


def upload(client, name, data):
    tok = csrf(client, "/employees")
    return client.post("/employees/upload", data={"_csrf": tok, "file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", follow_redirects=True)


def count(app_module):
    return query(app_module, "SELECT COUNT(*) FROM employees")[0][0]


def test_valid_csv(app_module, admin_client):
    r = upload(admin_client, "e.csv", (HDR + ROW.format(n=1) + ROW.format(n=2)).encode())
    assert count(app_module) == 2 and "2 employee(s) imported" in r.get_data(as_text=True)


def test_valid_xlsx(app_module, admin_client):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(HDR.strip().split(","))
    ws.append(["X1", "Zoë", "Nkosi-Ndlovu", "Coloured", "Female", "Semi-Skilled", 15000.5, date(2019, 5, 1)])
    ws.append([None] * 8)  # blank row
    ws.append(["X2", "Seán", "O'Connor", "White", "Male", "Top Management", "85,000.00", "01/02/2018"])
    buf = io.BytesIO(); wb.save(buf)
    upload(admin_client, "e.xlsx", buf.getvalue())
    rows = query(app_module, "SELECT first_name, last_name, salary, employment_date FROM employees ORDER BY id")
    assert [tuple(r) for r in rows] == [("Zoë", "Nkosi-Ndlovu", 15000.5, "2019-05-01"),
                                       ("Seán", "O'Connor", 85000.0, "2018-02-01")]


@pytest.mark.parametrize("name,data,msg", [
    ("e.csv", b"", "empty"),
    ("e.csv", HDR.encode(), "no employees"),
    ("e.xlsx", b"PK\x03\x04garbage", "could not be opened"),
    ("e.xlsx", b"not a zip", "not a valid .xlsx"),
    ("e.xls", b"\xd0\xcf\x11\xe0", "Only .csv and .xlsx"),
    ("e.exe", b"MZ", "Only .csv and .xlsx"),
    ("e.csv", b"PK\x03\x04", "not a text CSV"),
    ("e.csv", b"first_name,last_name\nA,B\n", "Missing required column"),
])
def test_bad_files_refused(app_module, admin_client, name, data, msg):
    r = upload(admin_client, name, data)
    assert msg in r.get_data(as_text=True) and count(app_module) == 0


def test_extra_columns_ignored_and_reported(app_module, admin_client):
    r = upload(admin_client, "e.csv", (HDR.strip() + ",shoe_size\n" + ROW.format(n=1).strip() + ",9\n").encode())
    assert count(app_module) == 1 and "shoe_size" in r.get_data(as_text=True)


def test_one_bad_row_imports_nothing(app_module, admin_client):
    data = HDR + ROW.format(n=1) + "E2,Bad,Date,African,Female,Unskilled,9000,2021-02-30\n" + ROW.format(n=3)
    r = upload(admin_client, "e.csv", data.encode())
    html = r.get_data(as_text=True)
    assert count(app_module) == 0 and "Nothing was imported" in html and "Row 3" in html
    assert "import-report" in html


def test_error_report_download(app_module, admin_client):
    r = upload(admin_client, "e.csv", (HDR + "E1,,X,African,Female,Unskilled,1,2020-01-01\n").encode())
    import re
    link = re.search(r'href="(/employees/import-report/[a-f0-9]+)"', r.get_data(as_text=True)).group(1)
    rep = admin_client.get(link)
    assert rep.status_code == 200 and b"First name: is required" in rep.data
    other = app_module.app.test_client()
    assert other.get(link).status_code == 302  # not signed in
    assert admin_client.get("/employees/import-report/" + "0" * 32).status_code == 404


@pytest.mark.parametrize("row,problem", [
    ("E1,A,B,African,Female,Unskilled,-5,2020-01-01", "cannot be negative"),
    ("E1,A,B,African,Female,Unskilled,abc,2020-01-01", "is not a number"),
    ("E1,A,B,African,Female,Unskilled,1,2099-01-01", "cannot be in the future"),
    ("E1,A,B,African,Female,Unskilled,1,31/31/2020", "not a valid date"),
    ("E1,A,B,Martian,Female,Unskilled,1,2020-01-01", "is not one of"),
    ("E1,A,B,African,Other,Unskilled,1,2020-01-01", "is not one of"),
    ("E1,A,B,,Female,Unskilled,1,2020-01-01", "required for South African"),
    ("E1,=cmd|' /C calc'!A0,B,African,Female,Unskilled,1,2020-01-01", "formula"),
    ("E1,@SUM(1+1),B,African,Female,Unskilled,1,2020-01-01", "formula"),
    ("E1,<script>alert(1)</script>,B,African,Female,Unskilled,1,2020-01-01", "not allowed in a name"),
    ("bad number!,A,B,African,Female,Unskilled,1,2020-01-01", "letters, digits"),
    ("E1,A,B,African,Female,Unskilled,99999999,2020-01-01", "implausibly large"),
])
def test_row_validation(app_module, admin_client, row, problem):
    r = upload(admin_client, "e.csv", (HDR + row + "\n").encode())
    assert problem in r.get_data(as_text=True) and count(app_module) == 0


def test_duplicates_in_file_and_register(app_module, admin_client):
    upload(admin_client, "e.csv", (HDR + ROW.format(n=1)).encode())
    r = upload(admin_client, "e.csv", (HDR + ROW.format(n=1) + ROW.format(n=2) + ROW.format(n=2)).encode())
    html = r.get_data(as_text=True)
    assert "already exists in the register" in html and "is repeated" in html and count(app_module) == 1


def test_quotes_commas_and_unicode_in_csv(app_module, admin_client):
    data = HDR + 'E9,"Nomvula, Jr",Dlamini-Žižek,Indian,Female,Unskilled,"12,500.00",2020-01-01\n'
    upload(admin_client, "e.csv", data.encode("utf-8"))
    r = query(app_module, "SELECT first_name, last_name, salary FROM employees")[0]
    assert tuple(r) == ("Nomvula, Jr", "Dlamini-Žižek", 12500.0)


def test_large_import_performance(app_module, admin_client):
    body = HDR + "".join(ROW.format(n=i) for i in range(5000))
    t = time.perf_counter()
    upload(admin_client, "e.csv", body.encode())
    elapsed = time.perf_counter() - t
    assert count(app_module) == 5000
    assert elapsed < 30, elapsed
    t = time.perf_counter()
    assert admin_client.get("/employees?q=Mokoena&page=50").status_code == 200
    assert time.perf_counter() - t < 2


def test_too_many_rows_refused(app_module, admin_client, monkeypatch):
    import modules.employees as me
    monkeypatch.setattr(me, "MAX_IMPORT_ROWS", 3)
    r = upload(admin_client, "e.csv", (HDR + "".join(ROW.format(n=i) for i in range(5))).encode())
    assert "Too many rows" in r.get_data(as_text=True) and count(app_module) == 0


# ── Field validation ───────────────────────────────────────────

@pytest.mark.parametrize("n,ok", [("8001015009087", True), ("8001015009086", False), ("800101500908", False),
                                  ("8013015009087", False), ("abcdefghijklm", False)])
def test_sa_id_check(n, ok):
    assert sa_id_valid(n) is ok


def test_foreign_national_legacy_race_value_and_passport():
    clean, errors, _ = validate({"emp_number": "F1", "first_name": "A", "last_name": "B", "race": "Foreign National",
                                 "gender": "Male", "occupational_level": "Unskilled", "employment_date": "2020-01-01",
                                 "id_number": "A1234567"})
    assert not errors and clean["is_foreign_national"] == 1 and clean["race"] is None


def test_termination_before_start_rejected():
    _, errors, _ = validate({"emp_number": "T1", "first_name": "A", "last_name": "B", "race": "White", "gender": "Male",
                             "occupational_level": "Unskilled", "employment_date": "2020-01-01",
                             "termination_date": "2019-01-01"})
    assert any("before the employment date" in e for e in errors)


def test_long_names_truncated_not_crashing():
    clean, errors, _ = validate({"emp_number": "L1", "first_name": "A" * 500, "last_name": "B", "race": "White",
                                 "gender": "Male", "occupational_level": "Unskilled", "employment_date": "2020-01-01"})
    assert not errors and len(clean["first_name"]) == 100


def test_employee_form_validation(app_module, admin_client):
    tok = csrf(admin_client, "/employees")
    r = admin_client.post("/employees", data={"_csrf": tok, "emp_number": "Q1", "first_name": "A", "last_name": "B",
                                              "gender": "Female", "occupational_level": "Unskilled", "race": "African",
                                              "employment_date": "2020-01-01", "salary": "-100"})
    assert r.status_code == 400 and count(app_module) == 0


def test_xss_payload_escaped_in_register(app_module, admin_client):
    db = app_module.connect_db()
    db.execute("INSERT INTO employees (emp_number, first_name, last_name, status) VALUES ('X1', '<img src=x onerror=alert(1)>', 'Z', 'ACTIVE')")
    db.commit(); db.close()
    html = admin_client.get("/employees").get_data(as_text=True)
    assert "<img src=x onerror" not in html and "&lt;img src=x onerror" in html


def test_sql_injection_in_search_is_inert(app_module, admin_client):
    for q in ("' OR 1=1 --", "%' UNION SELECT password_hash FROM users --", "_%"):
        r = admin_client.get("/employees", query_string={"q": q, "sort": "name; DROP TABLE users"})
        assert r.status_code == 200
    assert query(app_module, "SELECT COUNT(*) FROM users")[0][0] == 1
