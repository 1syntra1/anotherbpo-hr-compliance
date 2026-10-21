"""WSP / ATR: training records belonging to one reporting period, rendered from a
snapshot. Employee names and numbers are copied into the training record when it
is captured, so a later termination or rename does not change history."""
import io
from decimal import Decimal

from modules.xlsx_common import (data_row, header_row, new_workbook, report_meta, save, title_block,
                                 warning_row, widths)
from modules.coida import money

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:  # pragma: no cover
    DOCX_AVAILABLE = False

COLS = ["employee_number", "employee_name", "intervention_type", "category", "nqf_level", "cost",
        "provider", "target_date", "actual_date", "certificate", "status", "skills_gap"]


def build_snapshot(db, record, company, rule_set=None):
    rows = [dict(r) for r in db.execute(
        "SELECT id, %s FROM training_records WHERE record_id=? ORDER BY id" % ", ".join(COLS), (record["id"],))]
    return {
        "kind": "wsp",
        "period": {"label": record["period_label"], "start": record["period_start"], "end": record["period_end"]},
        "company": company,
        "rules": None,
        "training": rows,
    }


def _split(snapshot):
    planned = [t for t in snapshot["training"] if t["status"] in ("Planned", "In Progress")]
    done = [t for t in snapshot["training"] if t["status"] == "Completed"]
    return planned, done


def checks(snapshot):
    planned, done = _split(snapshot)
    out = []
    other = [t for t in snapshot["training"] if t not in planned and t not in done]
    out.append({"check": "Every record is Planned, In Progress or Completed", "expected": "0 other",
                "actual": "%d other" % len(other), "ok": not other, "detail": ""})
    no_date = [t for t in done if not t["actual_date"]]
    out.append({"check": "Completed training has an actual date", "expected": "0 missing",
                "actual": "%d missing" % len(no_date), "ok": not no_date,
                "detail": ", ".join(t["employee_number"] or "?" for t in no_date[:20])})
    neg = [t for t in snapshot["training"] if (t["cost"] or 0) < 0]
    out.append({"check": "No negative costs", "expected": "0", "actual": str(len(neg)), "ok": not neg, "detail": ""})
    total = sum((money(t["cost"]) for t in snapshot["training"]), Decimal("0.00"))
    split = sum((money(t["cost"]) for t in planned + done), Decimal("0.00"))
    out.append({"check": "WSP cost + ATR cost = total cost", "expected": str(total), "actual": str(split),
                "ok": total == split, "detail": ""})
    return out


def _meta(snapshot, ctx):
    c = snapshot["company"]
    return [("Company:", c.get("company_name") or ""), ("SETA:", c.get("seta") or ""),
            ("SDL number:", c.get("sdl_number") or ""),
            ("Reporting period:", snapshot["period"]["label"])] + report_meta(ctx)


def _table(ws, row, rows, completed):
    hdr = ["#", "Employee no.", "Employee", "Intervention", "Category", "NQF", "Cost (R)", "Provider",
           "Target date"] + (["Actual date", "Certificate"] if completed else ["Status"])
    row = header_row(ws, row, hdr)
    total = Decimal("0.00")
    for i, t in enumerate(rows, 1):
        vals = [i, t["employee_number"] or "", t["employee_name"] or "", t["intervention_type"] or "",
                t["category"] or "", t["nqf_level"] or "", float(money(t["cost"])), t["provider"] or "",
                t["target_date"] or ""] + ([t["actual_date"] or "", t["certificate"] or ""] if completed
                                           else [t["status"]])
        total += money(t["cost"])
        row = data_row(ws, row, vals, money_cols=(7,), shade=i % 2 == 0)
    tot = ["TOTAL", "", "", "", "", "", float(total)] + [""] * (len(hdr) - 7)
    return data_row(ws, row, tot, money_cols=(7,), total=True)


def generate_wsp(snapshot, ctx):
    planned, _ = _split(snapshot)
    wb, ws = new_workbook("WSP")
    widths(ws, [6, 14, 24, 28, 14, 8, 14, 20, 12, 12])
    row = title_block(ws, "WORKPLACE SKILLS PLAN (WSP) — WORKING COPY", "Planned training interventions", 10,
                      _meta(snapshot, ctx))
    _table(ws, row, planned, completed=False)
    return save(wb, ctx.get("generated_dt"))


def generate_atr(snapshot, ctx):
    _, done = _split(snapshot)
    wb, ws = new_workbook("ATR")
    widths(ws, [6, 14, 24, 28, 14, 8, 14, 20, 12, 12, 14])
    row = title_block(ws, "ANNUAL TRAINING REPORT (ATR) — WORKING COPY", "Completed training", 11,
                      _meta(snapshot, ctx))
    _table(ws, row, done, completed=True)
    return save(wb, ctx.get("generated_dt"))


def generate_wsp_word(snapshot, ctx):
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx not installed.")
    planned, done = _split(snapshot)
    c = snapshot["company"]
    doc = Document()
    doc.add_heading("WORKPLACE SKILLS PLAN & ANNUAL TRAINING REPORT", level=0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading("Company information", level=1)
    levy = c.get("skills_levy")
    try:
        levy_txt = "R {:,.2f}".format(float(levy)) if levy not in (None, "") else ""
    except (TypeError, ValueError):
        levy_txt = str(levy)
    info = [("Company name", c.get("company_name") or ""), ("SETA", c.get("seta") or ""),
            ("SDL number", c.get("sdl_number") or ""), ("Skills levy", levy_txt),
            ("Reporting period", snapshot["period"]["label"])] + \
           [(k.rstrip(":"), str(v)) for k, v in report_meta(ctx)]
    t = doc.add_table(rows=len(info), cols=2)
    t.style = "Table Grid"
    for i, (k, v) in enumerate(info):
        t.rows[i].cells[0].text = k
        t.rows[i].cells[1].text = v
        t.rows[i].cells[0].paragraphs[0].runs[0].bold = True
    for heading, rows, completed in (("Planned training (WSP)", planned, False),
                                     ("Completed training (ATR)", done, True)):
        doc.add_heading(heading, level=1)
        if not rows:
            doc.add_paragraph("No records.")
            continue
        hdr = ["Employee no.", "Employee", "Programme", "NQF", "Cost (R)", "Provider",
               "Actual date" if completed else "Target date"]
        tbl = doc.add_table(rows=1 + len(rows), cols=len(hdr))
        tbl.style = "Table Grid"
        for j, h in enumerate(hdr):
            tbl.rows[0].cells[j].text = h
            tbl.rows[0].cells[j].paragraphs[0].runs[0].bold = True
        for i, r in enumerate(rows, 1):
            vals = [r["employee_number"] or "", r["employee_name"] or "", r["intervention_type"] or "",
                    r["nqf_level"] or "", "{:,.2f}".format(float(money(r["cost"]))), r["provider"] or "",
                    (r["actual_date"] if completed else r["target_date"]) or ""]
            for j, v in enumerate(vals):
                tbl.rows[i].cells[j].text = v
    doc.core_properties.author = "Dash BPO HR Compliance Suite"
    if ctx.get("generated_dt") is not None:
        doc.core_properties.created = ctx["generated_dt"]
        doc.core_properties.modified = ctx["generated_dt"]
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
