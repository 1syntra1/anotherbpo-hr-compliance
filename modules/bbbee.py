"""B-BBEE internal calculation engine.

Inputs are achieved points per element (and per priority sub-element), as worked
out from the company's own scorecard workings. The engine applies the rule set:
caps each element at max + bonus, checks priority-element sub-minimums, applies
discounting, determines the level from the thresholds, and applies the automatic
levels for black-owned EMEs/QSEs.

Output is an INTERNAL calculation. It is not a B-BBEE certificate or affidavit and
never says so.
"""
import io
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from modules.xlsx_common import (data_row, header_row, new_workbook, report_meta, save, title_block,
                                 warning_row, widths)

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    RL_AVAILABLE = True
except ImportError:  # pragma: no cover
    RL_AVAILABLE = False

TWO = Decimal("0.01")
SCORECARD_CODES = {"GENERIC": "BBBEE-GENERIC-AMENDED", "QSE": "BBBEE-QSE-AMENDED", "EME": "BBBEE-EME-AMENDED"}


class InputError(ValueError):
    pass


def d(x):
    try:
        v = Decimal(str(x if x not in (None, "") else 0))
    except Exception:
        raise InputError("'%s' is not a number." % x)
    if v != v or v.is_infinite():
        raise InputError("Invalid number.")
    return v


def classify(turnover, params):
    t = d(turnover)
    if t <= d(params.get("turnover_max_eme", 10000000)):
        return "EME"
    if t <= d(params.get("turnover_max_qse", 50000000)):
        return "QSE"
    return "GENERIC"


def level_for_points(points, thresholds):
    for minimum, level in thresholds:
        if points >= d(minimum):
            return level
    return "NC"


def _auto_level(black_owned, table):
    for minimum, level in table:
        if black_owned >= d(minimum):
            return level
    return None


def calculate(scorecard_type, params, inputs, black_owned_pct=0):
    """Return a full explanation dict. `inputs` maps element keys and sub-minimum keys
    to achieved points (bonus points under '<element>_bonus')."""
    stype = scorecard_type.upper()
    bo = d(black_owned_pct)
    if bo < 0 or bo > 100:
        raise InputError("Black ownership must be between 0 and 100%.")
    result = {"scorecard": stype, "elements": [], "subminimums": [], "notes": [], "total": Decimal("0"),
              "max_total": Decimal("0")}

    if stype == "EME":
        lvl = _auto_level(bo, params["auto_level_black_owned"])
        result.update(level=lvl, base_level=lvl, discounted=False,
                      recognition_pct=params["recognition_pct"][str(lvl)])
        result["notes"].append("EME: level set automatically from black ownership of %s%%." % bo)
        return result

    for el in params["elements"]:
        raw = d(inputs.get(el["key"]))
        bonus = d(inputs.get(el["key"] + "_bonus"))
        if raw < 0 or bonus < 0:
            raise InputError("%s points cannot be negative." % el["label"])
        if raw > d(el["max"]):
            raise InputError("%s: %s points exceeds the maximum of %s." % (el["label"], raw, el["max"]))
        if bonus > d(el["bonus"]):
            raise InputError("%s: bonus of %s exceeds the maximum bonus of %s." % (el["label"], bonus, el["bonus"]))
        achieved = raw + bonus
        result["elements"].append({
            "key": el["key"], "label": el["label"], "max": d(el["max"]), "bonus_max": d(el["bonus"]),
            "points": raw, "bonus": bonus, "achieved": achieved,
            "pct": (raw / d(el["max"]) * 100).quantize(TWO, ROUND_HALF_UP) if el["max"] else Decimal(0),
            "priority": el["priority"],
        })
        result["total"] += achieved
        result["max_total"] += d(el["max"])

    for sm in params["subminimums"]:
        pts = d(inputs.get(sm["key"]))
        if pts < 0 or pts > d(sm["max"]):
            raise InputError("%s must be between 0 and %s." % (sm["label"], sm["max"]))
        need = (d(sm["max"]) * d(sm["min_pct"]) / 100).quantize(TWO, ROUND_HALF_UP)
        result["subminimums"].append({"key": sm["key"], "label": sm["label"], "element": sm["element"],
                                      "points": pts, "required": need, "met": pts >= need})

    # Consistency: a sub-element cannot exceed its element's points
    for sm in result["subminimums"]:
        el = next(e for e in result["elements"] if e["key"] == sm["element"])
        if sm["key"] == "skills_total" and sm["points"] != el["points"]:
            result["notes"].append("Skills sub-minimum points (%s) differ from Skills element points (%s); the "
                                   "sub-minimum is assessed on the figure entered for it." % (sm["points"], el["points"]))
        if sm["points"] > el["points"] + el["bonus"]:
            raise InputError("%s (%s) cannot exceed the %s points (%s)."
                             % (sm["label"], sm["points"], el["label"], el["achieved"]))

    by_el = {}
    for sm in result["subminimums"]:
        by_el.setdefault(sm["element"], []).append(sm["met"])
    el_met = {k: all(v) for k, v in by_el.items()}
    if params["priority_rule"] == "all":
        compliant = all(el_met.values())
        rule_txt = "all priority elements (Ownership, Skills Development, ESD)"
    else:  # QSE: ownership plus at least one of skills / esd
        compliant = el_met.get("ownership", True) and (el_met.get("skills", True) or el_met.get("esd", True))
        rule_txt = "Ownership plus at least one of Skills Development or ESD"

    total = result["total"].quantize(TWO, ROUND_HALF_UP)
    result["total"] = total
    base = level_for_points(total, params["level_thresholds"])
    level, discounted = base, False
    if not compliant and base != "NC":
        steps = params.get("discount_levels", 1)
        level = base + steps if base + steps <= 8 else "NC"
        discounted = True
        failed = [s["label"] for s in result["subminimums"] if not s["met"]]
        result["notes"].append("Discounted by %d level because the sub-minimum was not met for %s: %s."
                               % (steps, rule_txt, "; ".join(failed)))
    auto = params.get("auto_level_black_owned")
    if auto:
        a = _auto_level(bo, auto)
        if a is not None and (level == "NC" or a < level):
            result["notes"].append("Black ownership of %s%% qualifies for automatic Level %s (sworn affidavit "
                                   "route), which is better than the scorecard result." % (bo, a))
            level = a
    result.update(level=level, base_level=base, discounted=discounted, compliant_priority=compliant,
                  recognition_pct=params["recognition_pct"][str(level)])
    return result


# ─── Snapshot & reports ──────────────────────────────────────────────────────

def build_snapshot(db, record, company, rule_set, inputs_row):
    import json
    return {
        "kind": "bbbee",
        "period": {"label": record["period_label"], "start": record["period_start"], "end": record["period_end"]},
        "company": company,
        "rules": {"id": rule_set["id"], "code": rule_set["code"], "version": rule_set["version"],
                  "verified": rule_set["verified"], "params": rule_set["params"]},
        "scorecard_type": inputs_row["scorecard_type"],
        "annual_turnover": inputs_row["annual_turnover"],
        "black_owned_pct": inputs_row["black_owned_pct"],
        "inputs": json.loads(inputs_row["inputs_json"] or "{}"),
    }


def calc_from_snapshot(s):
    return calculate(s["scorecard_type"], s["rules"]["params"], s["inputs"], s["black_owned_pct"] or 0)


def checks(s, calc):
    out = []
    expected = classify(s["annual_turnover"] or 0, s["rules"]["params"])
    out.append({"check": "Scorecard type matches turnover band", "expected": expected,
                "actual": s["scorecard_type"], "ok": expected == s["scorecard_type"],
                "detail": "Sector codes may set different bands; confirm."})
    if calc["elements"]:
        add = sum((e["achieved"] for e in calc["elements"]), Decimal(0)).quantize(TWO)
        out.append({"check": "Total = sum of element points", "expected": str(add), "actual": str(calc["total"]),
                    "ok": add == calc["total"], "detail": ""})
    return out


def _level_txt(level):
    return "Non-compliant" if level == "NC" else "Level %s" % level


def generate_xlsx(s, ctx):
    calc = calc_from_snapshot(s)
    c = s["company"]
    wb, ws = new_workbook("Scorecard")
    widths(ws, [42, 12, 12, 12, 14, 12])
    row = title_block(ws, "INTERNAL B-BBEE CALCULATION — SCORECARD SUMMARY",
                      "Internal working only. This is NOT a B-BBEE certificate or sworn affidavit.", 6,
                      [("Company:", c.get("company_name") or ""), ("Measurement period:", s["period"]["label"]),
                       ("Scorecard:", calc["scorecard"]),
                       ("Annual turnover (R):", float(d(s["annual_turnover"]))),
                       ("Black ownership (%):", float(d(s["black_owned_pct"])))] + report_meta(ctx))
    if not s["rules"]["verified"]:
        row = warning_row(ws, row, "Rule set %s has not been verified against the Codes of Good Practice."
                          % s["rules"]["code"], 6)
    if calc["elements"]:
        row = header_row(ws, row, ["Element", "Max points", "Bonus max", "Points", "Bonus", "% of max"])
        for i, e in enumerate(calc["elements"]):
            row = data_row(ws, row, [e["label"] + (" (priority)" if e["priority"] else ""), float(e["max"]),
                                     float(e["bonus_max"]), float(e["points"]), float(e["bonus"]), float(e["pct"])],
                           shade=i % 2 == 0)
        row = data_row(ws, row, ["TOTAL", float(calc["max_total"]), "", float(calc["total"]), "", ""], total=True) + 1
        row = header_row(ws, row, ["Priority sub-minimum", "Required", "Achieved", "Met", "", ""])
        for sm in calc["subminimums"]:
            row = data_row(ws, row, [sm["label"], float(sm["required"]), float(sm["points"]),
                                     "Yes" if sm["met"] else "NO", "", ""])
        row += 1
    row = data_row(ws, row, ["Level from points", _level_txt(calc["base_level"]), "", "", "", ""], bold=True)
    row = data_row(ws, row, ["Final level", _level_txt(calc["level"]), "", "", "", ""], bold=True)
    row = data_row(ws, row, ["Procurement recognition", "%s%%" % calc["recognition_pct"], "", "", "", ""], bold=True)
    for n in calc["notes"]:
        row = warning_row(ws, row, n, 6)
    return save(wb, ctx.get("generated_dt"))


def generate_pdf(s, ctx):
    if not RL_AVAILABLE:
        raise RuntimeError("reportlab not installed.")
    calc = calc_from_snapshot(s)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm,
                            rightMargin=2 * cm, title="Internal B-BBEE Calculation",
                            author="Dash BPO HR Compliance Suite", invariant=1)
    st = getSampleStyleSheet()
    navy = colors.HexColor("#1A2744")
    story = [
        Paragraph("INTERNAL B-BBEE CALCULATION — SCORECARD SUMMARY",
                  ParagraphStyle("t", parent=st["Title"], textColor=navy, fontSize=15)),
        Paragraph("Internal working document. This is NOT a B-BBEE verification certificate or sworn affidavit. "
                  "Only a SANAS-accredited verification agency (or a valid sworn affidavit where permitted) "
                  "can confirm a B-BBEE level.",
                  ParagraphStyle("w", parent=st["Normal"], textColor=colors.HexColor("#7A4B00"),
                                 backColor=colors.HexColor("#FFF3CD"), borderPadding=6, fontSize=9)),
        Spacer(1, 0.5 * cm),
    ]
    info = [["Company", s["company"].get("company_name") or ""], ["Measurement period", s["period"]["label"]],
            ["Scorecard", calc["scorecard"]], ["Total points", str(calc["total"])],
            ["Level from points", _level_txt(calc["base_level"])], ["Final level", _level_txt(calc["level"])],
            ["Procurement recognition", "%s%%" % calc["recognition_pct"]]] + \
           [[k.rstrip(":"), str(v)] for k, v in report_meta(ctx)]
    t = Table(info, colWidths=[6 * cm, 10 * cm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                           ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#D9E8F5")), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    story += [t, Spacer(1, 0.4 * cm)]
    if calc["elements"]:
        rows = [["Element", "Max", "Points", "Bonus"]] + [
            [e["label"], str(e["max"]), str(e["points"]), str(e["bonus"])] for e in calc["elements"]] + [
            ["TOTAL", str(calc["max_total"]), str(calc["total"]), ""]]
        t2 = Table(rows, colWidths=[9 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm])
        t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        story += [t2, Spacer(1, 0.4 * cm)]
        rows = [["Priority sub-minimum", "Required", "Achieved", "Met"]] + [
            [sm["label"], str(sm["required"]), str(sm["points"]), "Yes" if sm["met"] else "NO"]
            for sm in calc["subminimums"]]
        t3 = Table(rows, colWidths=[9 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm])
        t3.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONTSIZE", (0, 0), (-1, -1), 9)]))
        story += [t3, Spacer(1, 0.4 * cm)]
    for n in calc["notes"]:
        story.append(Paragraph(n, st["Normal"]))
    if not s["rules"]["verified"]:
        story.append(Paragraph("Rule set %s has not been verified against the official Codes." % s["rules"]["code"],
                               st["Italic"]))
    doc.build(story)
    return buf.getvalue()
