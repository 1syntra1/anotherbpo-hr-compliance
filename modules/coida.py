"""COIDA Return of Earnings (W.As.8): per-employee earnings per assessment period,
class rates from the employer's own assessment notice, and the assessment.

assessment per class = (sum of capped earnings in class) / 100 x class rate
capped earnings      = min(employee earnings, annual maximum for the year)

All money is Decimal, rounded half-up to cents at the employee and class level.
"""
from decimal import Decimal, ROUND_HALF_UP

from modules.xlsx_common import (data_row, header_row, new_workbook, report_meta, save, title_block,
                                 warning_row, widths)

CENT = Decimal("0.01")


def money(x):
    return Decimal(str(x or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def build_snapshot(db, record, company, rule_set):
    earnings = [dict(r) for r in db.execute(
        "SELECT employee_id, employee_number, employee_name, class_code, subclass, earnings, months_worked "
        "FROM coida_earnings WHERE record_id=? ORDER BY class_code, subclass, employee_number", (record["id"],))]
    rates = [dict(r) for r in db.execute(
        "SELECT class_code, subclass, description, rate, source_note FROM coida_rates WHERE record_id=? "
        "ORDER BY class_code, subclass", (record["id"],))]
    return {
        "kind": "coida",
        "period": {"label": record["period_label"], "start": record["period_start"], "end": record["period_end"]},
        "company": company,
        "rules": {"id": rule_set["id"], "code": rule_set["code"], "version": rule_set["version"],
                  "verified": rule_set["verified"], "params": rule_set["params"]},
        "earnings": earnings,
        "rates": rates,
    }


def max_earnings(snapshot):
    caps = snapshot["rules"]["params"].get("max_earnings_by_year", {})
    label = str(snapshot["period"]["label"])[:4]
    if label in caps:
        return money(caps[label]), True
    return None, False


def calculate(snapshot):
    cap, cap_known = max_earnings(snapshot)
    rates = {(r["class_code"], r["subclass"] or ""): r for r in snapshot["rates"]}
    classes, issues = {}, []
    if not cap_known:
        issues.append("No maximum-earnings figure in the rule set for %s: earnings are NOT capped. Add the "
                      "gazetted maximum as a new rule-set version." % snapshot["period"]["label"])
    for e in snapshot["earnings"]:
        key = (e["class_code"], e["subclass"] or "")
        earn = money(e["earnings"])
        if earn < 0:
            issues.append("%s: negative earnings." % (e["employee_number"] or e["employee_name"]))
            continue
        capped = min(earn, cap) if cap_known else earn
        c = classes.setdefault(key, {"class_code": key[0], "subclass": key[1], "employees": 0,
                                     "earnings": Decimal("0.00"), "capped": Decimal("0.00")})
        c["employees"] += 1
        c["earnings"] += earn
        c["capped"] += capped
    total_earn = total_capped = total_assess = Decimal("0.00")
    for key, c in sorted(classes.items()):
        r = rates.get(key)
        if r is None:
            issues.append("Class %s%s has earnings but no assessment rate." % (key[0], (" / " + key[1]) if key[1] else ""))
            c["rate"] = None
            c["assessment"] = None
        else:
            c["rate"] = Decimal(str(r["rate"]))
            c["description"] = r["description"]
            c["assessment"] = money(c["capped"] / 100 * c["rate"])
            total_assess += c["assessment"]
        total_earn += c["earnings"]
        total_capped += c["capped"]
    for key, r in rates.items():
        if key not in classes:
            issues.append("Rate entered for class %s with no employees allocated (shown as nil)." % key[0])
    return {"classes": [classes[k] for k in sorted(classes)], "cap": cap, "total_earnings": total_earn,
            "total_capped": total_capped, "total_assessment": total_assess, "issues": issues,
            "employees": len(snapshot["earnings"])}


def checks(snapshot, calc):
    out = []
    raw = sum((money(e["earnings"]) for e in snapshot["earnings"] if money(e["earnings"]) >= 0), Decimal("0.00"))
    out.append({"check": "Class totals = sum of employee earnings", "expected": str(raw),
                "actual": str(calc["total_earnings"]), "ok": raw == calc["total_earnings"], "detail": ""})
    n = sum(c["employees"] for c in calc["classes"])
    out.append({"check": "Employees in classes = employees with earnings", "expected": str(len(snapshot["earnings"])),
                "actual": str(n), "ok": n == len(snapshot["earnings"]), "detail": ""})
    missing = [c for c in calc["classes"] if c["rate"] is None]
    out.append({"check": "Every class has an assessment rate", "expected": "0 missing",
                "actual": "%d missing" % len(missing), "ok": not missing, "detail": ""})
    cap_known = calc["cap"] is not None
    out.append({"check": "Maximum earnings known for the period", "expected": "yes",
                "actual": "yes" if cap_known else "no", "ok": cap_known, "detail": ""})
    out.append({"check": "At least one employee", "expected": ">0", "actual": str(len(snapshot["earnings"])),
                "ok": len(snapshot["earnings"]) > 0, "detail": ""})
    return out


def generate_was8(snapshot, ctx):
    calc = calculate(snapshot)
    c = snapshot["company"]
    wb, ws = new_workbook("W.As.8")
    widths(ws, [30, 12, 34, 14, 18, 18, 12, 18])
    row = title_block(ws, "COMPENSATION FUND — RETURN OF EARNINGS (W.As.8) WORKING COPY",
                      "Compensation for Occupational Injuries and Diseases Act 130 of 1993", 8,
                      [("Employer:", c.get("company_name") or ""),
                       ("COIDA registration number:", c.get("coida_number") or ""),
                       ("Assessment period:", "%s (%s to %s)" % (snapshot["period"]["label"],
                                                                 snapshot["period"]["start"] or "?",
                                                                 snapshot["period"]["end"] or "?")),
                       ("Maximum earnings per employee:",
                        float(calc["cap"]) if calc["cap"] is not None else "NOT SET")] + report_meta(ctx))
    if not snapshot["rules"]["verified"]:
        row = warning_row(ws, row, "Rule set %s (maximum earnings) has not been verified against the gazette."
                          % snapshot["rules"]["code"], 8)
    for msg in calc["issues"]:
        row = warning_row(ws, row, msg, 8)
    row = header_row(ws, row, ["Class", "Subclass", "Description", "Employees", "Earnings (R)",
                               "Capped earnings (R)", "Rate per R100", "Assessment (R)"])
    for i, k in enumerate(calc["classes"]):
        row = data_row(ws, row, [k["class_code"], k["subclass"], k.get("description") or "", k["employees"],
                                 float(k["earnings"]), float(k["capped"]),
                                 float(k["rate"]) if k["rate"] is not None else "MISSING",
                                 float(k["assessment"]) if k["assessment"] is not None else "MISSING"],
                       money_cols=(5, 6, 8), shade=i % 2 == 0)
    row = data_row(ws, row, ["TOTAL", "", "", calc["employees"], float(calc["total_earnings"]),
                             float(calc["total_capped"]), "", float(calc["total_assessment"])],
                   money_cols=(5, 6, 8), total=True)
    ws2 = wb.create_sheet("Employees")
    widths(ws2, [16, 30, 10, 10, 16, 10])
    r2 = header_row(ws2, 1, ["Employee no.", "Name", "Class", "Subclass", "Earnings (R)", "Months"])
    for e in snapshot["earnings"]:
        r2 = data_row(ws2, r2, [e["employee_number"] or "", e["employee_name"] or "", e["class_code"],
                                e["subclass"] or "", float(money(e["earnings"])),
                                e["months_worked"] if e["months_worked"] is not None else ""], money_cols=(5,))
    ws3 = wb.create_sheet("Checks")
    widths(ws3, [48, 20, 20, 8])
    r3 = header_row(ws3, 1, ["Check", "Expected", "Actual", "OK"])
    for ch in ctx.get("checks") or checks(snapshot, calc):
        r3 = data_row(ws3, r3, [ch["check"], ch["expected"], ch["actual"], "PASS" if ch["ok"] else "FAIL"])
    return save(wb, ctx.get("generated_dt"))
