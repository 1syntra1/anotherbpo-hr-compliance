"""Employment Equity: reporting population, EEA2 / EEA4 figures and reconciliation.

Everything here works on a *snapshot* (plain dicts), never on the live database,
so a stored report can be re-derived exactly from its stored snapshot.

Assumptions (also recorded in the EE rule set and flagged for verification):
- Workforce profile = employees employed on the last day of the period.
- Recruitment / terminations = start / termination dates inside the period.
- EEA4 remuneration = monthly salary x months employed within the period
  (pro-rated by days when `prorate_part_year`), capped at the annual factor.
- Foreign nationals are reported in separate columns, not under a race group.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def money(x):
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def months_in_period(start, end, p_start, p_end):
    """Months (fractional, by days) an employee was employed within the period."""
    s = max(start or p_start, p_start)
    e = min(end or p_end, p_end)
    if e < s:
        return Decimal(0)
    days = Decimal((e - s).days + 1)
    period_days = Decimal((p_end - p_start).days + 1)
    return (days / period_days * 12).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def employee_snapshot_row(e):
    """Fields an EE report may rely on. Deliberately no names or ID numbers
    (data minimisation): employee number is enough to trace a figure."""
    return {
        "id": e["id"], "emp_number": e["emp_number"], "race": e["race"], "gender": e["gender"],
        "disability": e["disability"], "foreign": bool(e["is_foreign_national"]),
        "level": e["occupational_level"], "employment_type": e["employment_type"],
        "start": e["employment_date"], "end": e["termination_date"], "status": e["status"],
        "monthly_salary": e["salary"],
    }


def build_snapshot(db, record, company, rule_set):
    p = rule_set["params"]
    rows = db.execute("SELECT * FROM employees ORDER BY id").fetchall()
    return {
        "kind": "ee",
        "period": {"label": record["period_label"], "start": record["period_start"], "end": record["period_end"]},
        "company": company,
        "rules": {"id": rule_set["id"], "code": rule_set["code"], "version": rule_set["version"],
                  "verified": rule_set["verified"], "params": p},
        "employees": [employee_snapshot_row(e) for e in rows],
    }


def analyse(snapshot):
    """Return the population, validation issues and every figure the reports need."""
    p = snapshot["rules"]["params"]
    levels, races, genders = p["occupational_levels"], p["race_groups"], p["genders"]
    p_start, p_end = _d(snapshot["period"]["start"]), _d(snapshot["period"]["end"])
    issues = []
    if not p_start or not p_end:
        issues.append({"severity": "error", "employee": None,
                       "message": "The reporting period has no start/end dates."})
        return {"issues": issues, "population": [], "hires": [], "terms": [], "excluded": []}

    population, hires, terms, excluded = [], [], [], []
    for e in snapshot["employees"]:
        start, end = _d(e["start"]), _d(e["end"])
        ref = e["emp_number"] or "id %s" % e["id"]
        if e["start"] and not start:
            issues.append({"severity": "error", "employee": ref, "message": "Invalid employment date."})
            continue
        if e["end"] and not end:
            issues.append({"severity": "error", "employee": ref, "message": "Invalid termination date."})
            continue
        if start is None:
            issues.append({"severity": "error", "employee": ref, "message": "Missing employment date."})
            continue
        employed_at_end = start <= p_end and (end is None or end >= p_end)
        in_period = start <= p_end and (end is None or end >= p_start)
        if not in_period:
            continue
        problems = []
        if e["level"] not in levels:
            problems.append("occupational level")
        if e["gender"] not in genders:
            problems.append("gender")
        if not e["foreign"] and e["race"] not in races:
            problems.append("race")
        if e["employment_type"] not in ("Permanent", "Temporary"):
            problems.append("employment type")
        if problems:
            issues.append({"severity": "error", "employee": ref,
                           "message": "Missing or invalid " + ", ".join(problems) + "."})
            excluded.append(e)
            continue
        salary = e["monthly_salary"]
        if salary is None:
            issues.append({"severity": "error", "employee": ref, "message": "Missing salary (needed for EEA4)."})
        elif salary < 0:
            issues.append({"severity": "error", "employee": ref, "message": "Negative salary."})
        elif salary == 0:
            issues.append({"severity": "warning", "employee": ref, "message": "Salary is zero."})
        months = months_in_period(start, end, p_start, p_end) if p["prorate_part_year"] else Decimal(12)
        factor = Decimal(str(p["monthly_to_annual_factor"]))
        rem = money(Decimal(str(salary or 0)) * min(months, factor)) if (salary or 0) > 0 else Decimal("0.00")
        row = dict(e, months=str(months), remuneration=str(rem), at_end=employed_at_end)
        if employed_at_end:
            population.append(row)
        if p_start <= start <= p_end:
            hires.append(row)
        if end and p_start <= end <= p_end:
            terms.append(row)
            if not employed_at_end and e["status"] == "ACTIVE":
                issues.append({"severity": "warning", "employee": ref,
                               "message": "Has a termination date but status is still Active."})
    return {"issues": issues, "population": population, "hires": hires, "terms": terms, "excluded": excluded,
            "levels": levels, "races": races, "genders": genders}


def _col_key(e):
    g = "M" if e["gender"] == "Male" else "F"
    return ("Foreign", g) if e["foreign"] else (e["race"], g)


def columns(races):
    return [(r, g) for r in races for g in ("M", "F")] + [("Foreign", "M"), ("Foreign", "F")]


def matrix(rows, levels, races, employment_type=None):
    cols = columns(races)
    m = {lvl: {c: 0 for c in cols} for lvl in levels}
    for e in rows:
        if employment_type and e["employment_type"] != employment_type:
            continue
        m[e["level"]][_col_key(e)] += 1
    return m


def eea2_data(a):
    lv, rc = a["levels"], a["races"]
    disabled = [e for e in a["population"] if e["disability"] == "Yes"]
    return {
        "columns": columns(rc),
        "permanent": matrix(a["population"], lv, rc, "Permanent"),
        "temporary": matrix(a["population"], lv, rc, "Temporary"),
        "disabled": matrix(disabled, lv, rc),
        "recruitment": matrix(a["hires"], lv, rc),
        "terminations": matrix(a["terms"], lv, rc),
    }


def eea4_data(a):
    lv, rc = a["levels"], a["races"]
    cols = columns(rc)
    head = {lvl: {c: 0 for c in cols} for lvl in lv}
    rem = {lvl: {c: Decimal("0.00") for c in cols} for lvl in lv}
    for e in a["population"]:
        k = _col_key(e)
        head[e["level"]][k] += 1
        rem[e["level"]][k] += Decimal(e["remuneration"])
    return {"columns": cols, "headcount": head, "remuneration": rem}


def _total(m):
    return sum(sum(r.values()) for r in m.values())


def reconcile(a, e2, e4):
    """Independent cross-checks between the population, EEA2 and EEA4."""
    checks = []

    def chk(name, expected, actual, detail=""):
        checks.append({"check": name, "expected": str(expected), "actual": str(actual),
                       "ok": expected == actual, "detail": detail})

    pop = a["population"]
    eea2_total = _total(e2["permanent"]) + _total(e2["temporary"])
    chk("EEA2 workforce total = population", len(pop), eea2_total)
    chk("EEA4 headcount total = population", len(pop), _total(e4["headcount"]))
    chk("EEA2 total = EEA4 total", eea2_total, _total(e4["headcount"]))
    for lvl in a["levels"]:
        two = sum(e2["permanent"][lvl].values()) + sum(e2["temporary"][lvl].values())
        four = sum(e4["headcount"][lvl].values())
        if two != four:
            chk("Level '%s': EEA2 = EEA4" % lvl, two, four)
    for c in e2["columns"]:
        two = sum(e2["permanent"][l][c] + e2["temporary"][l][c] for l in a["levels"])
        four = sum(e4["headcount"][l][c] for l in a["levels"])
        if two != four:
            chk("Group %s %s: EEA2 = EEA4" % c, two, four)
    rem_people = sum((Decimal(e["remuneration"]) for e in pop), Decimal("0.00"))
    rem_report = sum((v for r in e4["remuneration"].values() for v in r.values()), Decimal("0.00"))
    chk("EEA4 remuneration total = sum of employees", rem_people, rem_report)
    disabled_people = sum(1 for e in pop if e["disability"] == "Yes")
    chk("Disability total = employees flagged disabled", disabled_people, _total(e2["disabled"]))
    errors = [i for i in a["issues"] if i["severity"] == "error"]
    chk("No employees excluded by data errors", 0, len(errors),
        "; ".join("%s: %s" % (i["employee"], i["message"]) for i in errors[:20]))
    return checks
