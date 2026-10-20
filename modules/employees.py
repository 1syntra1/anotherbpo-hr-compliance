"""Employee register: field validation and transactional bulk import."""
import csv
import io
import re
import unicodedata
from datetime import date, datetime

RACES = ["African", "Coloured", "Indian", "White"]
GENDERS = ["Male", "Female"]
OCC_LEVELS = ["Top Management", "Senior Management", "Professionally Qualified",
              "Skilled Technical", "Semi-Skilled", "Unskilled"]
EMPLOYMENT_TYPES = ["Permanent", "Temporary"]
PROVINCES = ["Gauteng", "Western Cape", "KwaZulu-Natal", "Eastern Cape", "Limpopo", "Mpumalanga",
             "North West", "Free State", "Northern Cape"]
STATUSES = ["ACTIVE", "TERMINATED", "ARCHIVED"]
MAX_MONTHLY_SALARY = 10_000_000
MAX_IMPORT_ROWS = 20_000

# column -> (label, accepted header aliases)
COLUMNS = {
    "emp_number": ("Employee number", ["emp_number", "emp number", "employee number", "employee no", "emp no"]),
    "first_name": ("First name", ["first_name", "first name", "firstname", "name"]),
    "last_name": ("Last name", ["last_name", "last name", "surname", "lastname"]),
    "id_number": ("ID / passport number", ["id_number", "id number", "id", "passport", "id/passport"]),
    "race": ("Race", ["race", "population group"]),
    "gender": ("Gender", ["gender", "sex"]),
    "disability": ("Disability", ["disability", "disabled"]),
    "is_foreign_national": ("Foreign national", ["is_foreign_national", "foreign national", "foreign"]),
    "nationality": ("Nationality", ["nationality"]),
    "occupational_level": ("Occupational level", ["occupational_level", "occupational level", "level"]),
    "employment_type": ("Employment type", ["employment_type", "employment type", "type"]),
    "department": ("Department", ["department", "dept"]),
    "job_title": ("Job title", ["job_title", "job title", "position"]),
    "salary": ("Monthly salary", ["salary", "monthly salary", "basic salary"]),
    "employment_date": ("Employment date", ["employment_date", "employment date", "start date", "start_date",
                                            "date engaged"]),
    "termination_date": ("Termination date", ["termination_date", "termination date", "end date"]),
    "province": ("Province", ["province"]),
    "coida_class_code": ("COIDA class", ["coida_class_code", "coida class", "coida_class"]),
    "coida_subclass": ("COIDA subclass", ["coida_subclass", "coida subclass"]),
}
REQUIRED = ["emp_number", "first_name", "last_name", "gender", "occupational_level", "employment_date"]
TEMPLATE_ORDER = list(COLUMNS)

_ctrl = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_empno = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,29}$")
_passport = re.compile(r"^[A-Za-z0-9]{5,20}$")


def _text(v, maxlen):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = unicodedata.normalize("NFC", str(v))
    s = _ctrl.sub("", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:maxlen] if s else None


def _date(v):
    if v in (None, ""):
        return None, None
    if isinstance(v, datetime):
        return v.date(), None
    if isinstance(v, date):
        return v, None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date(), None
        except ValueError:
            pass
    return None, "'%s' is not a valid date (use YYYY-MM-DD)" % s[:20]


def _yes_no(v, default="No"):
    if v in (None, ""):
        return default, None
    s = str(v).strip().lower()
    if s in ("yes", "y", "true", "1"):
        return "Yes", None
    if s in ("no", "n", "false", "0"):
        return "No", None
    return None, "must be Yes or No"


def _pick(v, options):
    if v in (None, ""):
        return None, None
    s = str(v).strip().lower().replace("_", " ").replace("-", " ")
    for o in options:
        if o.lower().replace("-", " ") == s:
            return o, None
    return None, "'%s' is not one of: %s" % (str(v)[:30], ", ".join(options))


def sa_id_valid(n):
    """13 digits, valid YYMMDD birth date and Luhn check digit."""
    if not re.fullmatch(r"\d{13}", n):
        return False
    yy, mm, dd = int(n[0:2]), int(n[2:4]), int(n[4:6])
    ok_date = False
    for century in (1900, 2000):
        try:
            date(century + yy, mm, dd)
            ok_date = True
        except ValueError:
            pass
    if not ok_date:
        return False
    total = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate(raw, today=None):
    """Validate one employee. `raw` maps column names to raw values.
    Returns (clean, errors, warnings)."""
    today = today or date.today()
    clean, errors, warnings = {}, [], []

    def err(col, msg):
        errors.append("%s: %s" % (COLUMNS[col][0], msg))

    for col in raw:
        v = raw[col]
        if isinstance(v, str) and v.lstrip().startswith(("=", "+", "@")) and col not in ("salary",):
            err(col, "looks like a spreadsheet formula, which is not allowed")
            raw = dict(raw, **{col: None})

    clean["emp_number"] = _text(raw.get("emp_number"), 30)
    if clean["emp_number"] and not _empno.match(clean["emp_number"]):
        err("emp_number", "use letters, digits and . _ / - only (max 30)")
    clean["first_name"] = _text(raw.get("first_name"), 100)
    clean["last_name"] = _text(raw.get("last_name"), 100)
    for col in ("first_name", "last_name"):
        if clean[col] and re.search(r"[<>{}\\]", clean[col]):
            err(col, "contains characters not allowed in a name")
    clean["department"] = _text(raw.get("department"), 100)
    clean["job_title"] = _text(raw.get("job_title"), 100)
    clean["nationality"] = _text(raw.get("nationality"), 60)
    clean["coida_class_code"] = _text(raw.get("coida_class_code"), 10)
    clean["coida_subclass"] = _text(raw.get("coida_subclass"), 10)

    for col, opts in (("gender", GENDERS), ("occupational_level", OCC_LEVELS), ("province", PROVINCES)):
        clean[col], e = _pick(raw.get(col), opts)
        if e:
            err(col, e)
    et, e = _pick(raw.get("employment_type") or "Permanent", EMPLOYMENT_TYPES)
    clean["employment_type"] = et
    if e:
        err("employment_type", e)
    clean["disability"], e = _yes_no(raw.get("disability"))
    if e:
        err("disability", e)
    fn, e = _yes_no(raw.get("is_foreign_national"))
    if e:
        err("is_foreign_national", e)
    clean["is_foreign_national"] = 1 if fn == "Yes" else 0

    race_raw = raw.get("race")
    if race_raw not in (None, "") and str(race_raw).strip().lower() == "foreign national":
        clean["is_foreign_national"] = 1  # legacy value: nationality, not race
        race_raw = None
    clean["race"], e = _pick(race_raw, RACES)
    if e:
        err("race", e)
    if not clean["race"] and not clean["is_foreign_national"]:
        err("race", "required for South African employees (EE reporting)")

    idn = _text(raw.get("id_number"), 20)
    if idn:
        idn = idn.replace(" ", "")
        if clean["is_foreign_national"]:
            if not (sa_id_valid(idn) or _passport.match(idn)):
                err("id_number", "not a valid SA ID or passport number")
        elif not sa_id_valid(idn):
            err("id_number", "not a valid 13-digit South African ID number")
    clean["id_number"] = idn

    sal = raw.get("salary")
    if sal in (None, ""):
        clean["salary"] = None
        warnings.append("Monthly salary is blank (needed for EEA4).")
    else:
        try:
            s = float(str(sal).replace(" ", "").replace(",", "").replace("R", ""))
            if s != s or s in (float("inf"), float("-inf")):
                raise ValueError
            if s < 0:
                err("salary", "cannot be negative")
            elif s > MAX_MONTHLY_SALARY:
                err("salary", "is implausibly large for a monthly salary")
            elif s == 0:
                warnings.append("Monthly salary is zero.")
            clean["salary"] = round(s, 2)
        except ValueError:
            err("salary", "'%s' is not a number" % str(sal)[:20])
            clean["salary"] = None

    start, e = _date(raw.get("employment_date"))
    if e:
        err("employment_date", e)
    elif start and start > today:
        err("employment_date", "cannot be in the future")
    elif start and start.year < 1940:
        err("employment_date", "is implausibly early")
    end, e = _date(raw.get("termination_date"))
    if e:
        err("termination_date", e)
    elif end and end > today:
        err("termination_date", "cannot be in the future")
    if start and end and end < start:
        err("termination_date", "is before the employment date")
    clean["employment_date"] = start.isoformat() if start else None
    clean["termination_date"] = end.isoformat() if end else None

    for col in REQUIRED:
        if not clean.get(col) and not any(x.startswith(COLUMNS[col][0] + ":") for x in errors):
            err(col, "is required")
    return clean, errors, warnings


# ─── Import ─────────────────────────────────────────────────────────────────

class ImportFileError(ValueError):
    pass


def _map_headers(headers):
    mapping, unknown = {}, []
    for i, h in enumerate(headers):
        key = (str(h).strip().lower() if h is not None else "")
        found = None
        for col, (_label, aliases) in COLUMNS.items():
            if key in aliases:
                found = col
                break
        if found and found not in mapping.values():
            mapping[i] = found
        elif key:
            unknown.append(str(h))
    missing = [COLUMNS[c][0] for c in REQUIRED if c not in mapping.values()]
    if missing:
        raise ImportFileError("Missing required column(s): %s." % ", ".join(missing))
    return mapping, unknown


def read_rows(filename, data):
    """Yield (row_number, {col: value}) from CSV or XLSX bytes. Raises ImportFileError."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv":
        if data[:2] in (b"PK", b"\xd0\xcf") or b"\x00" in data[:2048]:
            raise ImportFileError("The file is not a text CSV (it looks like a binary or Excel file).")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1252")
            except UnicodeDecodeError:
                raise ImportFileError("Could not read the CSV text encoding. Save it as UTF-8.")
        reader = csv.reader(io.StringIO(text, newline=""))
        try:
            headers = next(reader)
        except StopIteration:
            raise ImportFileError("The file is empty.")
        except csv.Error:
            raise ImportFileError("The CSV file is malformed.")
        mapping, unknown = _map_headers(headers)
        rows = []
        try:
            for n, row in enumerate(reader, start=2):
                rows.append((n, row))
        except csv.Error:
            raise ImportFileError("The CSV file is malformed near row %d." % (len(rows) + 2))
    elif ext == "xlsx":
        if data[:2] != b"PK":
            raise ImportFileError("The file is not a valid .xlsx workbook.")
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            headers = next(it, None)
            if headers is None:
                raise ImportFileError("The workbook is empty.")
            mapping, unknown = _map_headers(headers)
            rows = [(n, list(r)) for n, r in enumerate(it, start=2)]
        except ImportFileError:
            raise
        except Exception:
            raise ImportFileError("The workbook could not be opened (corrupt or not an Excel file).")
    else:
        raise ImportFileError("Only .csv and .xlsx files can be imported.")

    out = []
    for n, row in rows:
        if not any(v not in (None, "") and str(v).strip() for v in row):
            continue
        if len(out) >= MAX_IMPORT_ROWS:
            raise ImportFileError("Too many rows (maximum %d per import)." % MAX_IMPORT_ROWS)
        out.append((n, {mapping[i]: row[i] for i in mapping if i < len(row)}))
    if not out:
        raise ImportFileError("The file has a header row but no employees.")
    return out, unknown


def plan_import(db, filename, data, today=None):
    """Validate the whole file without writing. Returns a result dict; nothing is
    imported unless result['ok'] is True."""
    rows, unknown = read_rows(filename, data)
    existing = {r[0] for r in db.execute("SELECT emp_number FROM employees WHERE emp_number IS NOT NULL")}
    seen = {}
    good, problems, duplicates, warnings = [], [], 0, []
    for n, raw in rows:
        clean, errs, warns = validate(raw, today)
        num = clean.get("emp_number")
        if num:
            if num in existing:
                errs.append("Employee number %s already exists in the register" % num)
                duplicates += 1
            elif num in seen:
                errs.append("Employee number %s is repeated (also on row %d)" % (num, seen[num]))
                duplicates += 1
            else:
                seen[num] = n
        if errs:
            problems.append({"row": n, "emp_number": num or "", "errors": errs})
        else:
            good.append(clean)
        for w in warns:
            warnings.append({"row": n, "emp_number": num or "", "warning": w})
    return {"ok": not problems, "processed": len(rows), "valid": len(good), "failed": len(problems),
            "duplicates": duplicates, "problems": problems, "warnings": warnings, "rows": good,
            "unknown_columns": unknown}


INSERT_COLS = ["emp_number", "first_name", "last_name", "id_number", "race", "gender", "disability",
               "is_foreign_national", "nationality", "occupational_level", "employment_type", "department",
               "job_title", "salary", "employment_date", "termination_date", "province", "coida_class_code",
               "coida_subclass", "status", "updated_by"]


def status_for(clean):
    return "TERMINATED" if clean.get("termination_date") else "ACTIVE"


def insert_employee(db, clean, user_id):
    row = dict(clean, status=status_for(clean), updated_by=user_id)
    cur = db.execute("INSERT INTO employees (%s, updated_at) VALUES (%s, datetime('now'))"
                     % (",".join(INSERT_COLS), ",".join("?" * len(INSERT_COLS))),
                     [row.get(c) for c in INSERT_COLS])
    return cur.lastrowid


def error_report_csv(result):
    from modules.xlsx_safe import safe_csv_value
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Row", "Employee number", "Problem"])
    for p in result["problems"]:
        for e in p["errors"]:
            w.writerow([p["row"], safe_csv_value(p["emp_number"]), safe_csv_value(e)])
    for x in result["warnings"]:
        w.writerow([x["row"], safe_csv_value(x["emp_number"]), "Warning: " + x["warning"]])
    return out.getvalue().encode("utf-8-sig")


def template_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(TEMPLATE_ORDER)
    w.writerow(["EMP001", "Thandi", "Mokoena", "", "African", "Female", "No", "No", "South African",
                "Skilled Technical", "Permanent", "IT", "Developer", "25000", "2020-01-15", "", "Gauteng",
                "", ""])
    return out.getvalue().encode("utf-8-sig")
