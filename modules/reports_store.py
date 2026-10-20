"""Immutable, versioned report storage.

generate_version():
  1. builds a snapshot of every input (employees/training/earnings, company profile
     version, rule set) as canonical JSON,
  2. runs the module's validation / reconciliation checks on that snapshot,
  3. renders the file *from the snapshot only*,
  4. stores file + snapshot + hashes in an append-only `report_versions` row.

Old versions are never altered. `rerender()` reproduces a stored report's content
from its stored snapshot, which is how "can we reproduce the historical report?"
is answered.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime

from modules import bbbee, coida, ee, ee_reports, wsp
from modules.company import current_profile
from modules.rules import get_rule_set

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"


class ReportError(Exception):
    pass


def _ee_checks(s):
    a = ee.analyse(s)
    if not a.get("levels"):
        return [{"check": "Reporting period has dates", "expected": "yes", "actual": "no", "ok": False,
                 "detail": ""}]
    return ee.reconcile(a, ee.eea2_data(a), ee.eea4_data(a))


REPORT_TYPES = {
    "eea2": {"module": "ee", "label": "EEA2 (Excel)", "ext": "xlsx", "mime": XLSX,
             "snapshot": "ee", "render": ee_reports.generate_eea2, "checks": _ee_checks},
    "eea4": {"module": "ee", "label": "EEA4 (Excel)", "ext": "xlsx", "mime": XLSX,
             "snapshot": "ee", "render": ee_reports.generate_eea4, "checks": _ee_checks},
    "wsp": {"module": "wsp", "label": "WSP (Excel)", "ext": "xlsx", "mime": XLSX,
            "snapshot": "wsp", "render": wsp.generate_wsp, "checks": wsp.checks},
    "atr": {"module": "wsp", "label": "ATR (Excel)", "ext": "xlsx", "mime": XLSX,
            "snapshot": "wsp", "render": wsp.generate_atr, "checks": wsp.checks},
    "wsp_docx": {"module": "wsp", "label": "WSP & ATR (Word)", "ext": "docx", "mime": DOCX,
                 "snapshot": "wsp", "render": wsp.generate_wsp_word, "checks": wsp.checks},
    "was8": {"module": "coida", "label": "W.As.8 Return of Earnings (Excel)", "ext": "xlsx", "mime": XLSX,
             "snapshot": "coida", "render": coida.generate_was8,
             "checks": lambda s: coida.checks(s, coida.calculate(s))},
    "bbbee_xlsx": {"module": "bbbee", "label": "Internal B-BBEE Calculation (Excel)", "ext": "xlsx", "mime": XLSX,
                   "snapshot": "bbbee", "render": bbbee.generate_xlsx,
                   "checks": lambda s: bbbee.checks(s, bbbee.calc_from_snapshot(s))},
    "bbbee_pdf": {"module": "bbbee", "label": "Internal B-BBEE Calculation (PDF)", "ext": "pdf", "mime": PDF,
                  "snapshot": "bbbee", "render": bbbee.generate_pdf,
                  "checks": lambda s: bbbee.checks(s, bbbee.calc_from_snapshot(s))},
}

FILE_PREFIX = {"eea2": "EEA2", "eea4": "EEA4", "wsp": "WSP", "atr": "ATR", "wsp_docx": "WSP_ATR",
               "was8": "WAs8_Return", "bbbee_xlsx": "BBBEE_Internal_Calculation",
               "bbbee_pdf": "BBBEE_Internal_Calculation"}


def reports_dir(data_dir):
    p = os.path.join(data_dir, "reports")
    os.makedirs(p, exist_ok=True)
    return p


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def build_snapshot(db, record, report_type):
    kind = REPORT_TYPES[report_type]["snapshot"]
    company = current_profile(db)
    on = record.get("period_end") or record.get("period_start")
    if kind == "ee":
        rs = get_rule_set(db, "EE-ACT-2025", on)
        return ee.build_snapshot(db, record, company, rs), company, rs
    if kind == "wsp":
        return wsp.build_snapshot(db, record, company), company, None
    if kind == "coida":
        rs = get_rule_set(db, "COIDA-ROE", on)
        return coida.build_snapshot(db, record, company, rs), company, rs
    if kind == "bbbee":
        row = db.execute("SELECT * FROM bbbee_inputs WHERE record_id=?", (record["id"],)).fetchone()
        if row is None:
            raise ReportError("Enter the B-BBEE scorecard inputs for this period first.")
        rs = get_rule_set(db, bbbee.SCORECARD_CODES[row["scorecard_type"]], on)
        return bbbee.build_snapshot(db, record, company, rs, row), company, rs
    raise ReportError("Unknown report type.")


def _ctx(version, generated_at, user_name, rs, snap_sha, checks):
    gen_dt = datetime.fromisoformat(generated_at)
    return {
        "version_label": "Version %d" % version,
        "generated_at": gen_dt.strftime("%d %B %Y %H:%M"),
        "generated_dt": gen_dt,
        "generated_by": user_name,
        "rule_label": ("%s v%s%s" % (rs["code"], rs["version"], "" if rs["verified"] else " (UNVERIFIED)")) if rs else "n/a",
        "snapshot_sha256": snap_sha,
        "checks": checks,
    }


def generate_version(db, data_dir, record, report_type, user, reason=None):
    """Create the next stored version. Does not commit (caller owns the transaction)."""
    spec = REPORT_TYPES.get(report_type)
    if spec is None or spec["module"] != record["module"]:
        raise ReportError("That report does not belong to this compliance record.")
    prev = db.execute("SELECT MAX(version) FROM report_versions WHERE record_id=? AND report_type=?",
                      (record["id"], report_type)).fetchone()[0] or 0
    if prev and not (reason or "").strip():
        raise ReportError("Give a reason for regenerating (version %d already exists)." % prev)
    snapshot, company, rs = build_snapshot(db, record, report_type)
    snap_json = canonical(snapshot)
    snap_sha = sha256_bytes(snap_json.encode("utf-8"))
    checks = spec["checks"](snapshot)
    version = prev + 1
    generated_at = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    data = spec["render"](snapshot, _ctx(version, generated_at, user["full_name"] or user["username"], rs,
                                         snap_sha, checks))
    stored = "%s.%s" % (uuid.uuid4().hex, spec["ext"])
    path = os.path.join(reports_dir(data_dir), stored)
    with open(path, "wb") as fh:
        fh.write(data)
    file_name = "%s_%s_v%d.%s" % (FILE_PREFIX[report_type],
                                   "".join(ch if ch.isalnum() else "-" for ch in record["period_label"]),
                                   version, spec["ext"])
    try:
        cur = db.execute(
            """INSERT INTO report_versions (record_id, report_type, version, generated_at, generated_by,
                   generated_by_name, reason, snapshot_json, snapshot_sha256, company_profile_id, rule_set_id,
                   file_name, stored_name, mime, size, sha256, checks_json, checks_ok)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (record["id"], report_type, version, generated_at, user["id"], user["full_name"] or user["username"],
             (reason or "").strip() or None, snap_json, snap_sha, company.get("id"), rs["id"] if rs else None,
             file_name, stored, spec["mime"], len(data), sha256_bytes(data), canonical(checks),
             1 if all(c["ok"] for c in checks) else 0))
    except Exception:
        os.remove(path)
        raise
    return cur.lastrowid, version, checks


def get_version(db, version_id):
    r = db.execute("SELECT * FROM report_versions WHERE id=?", (version_id,)).fetchone()
    return dict(r) if r else None


def read_file(data_dir, version):
    """Return the stored bytes, verifying they still match the recorded hash."""
    path = os.path.join(reports_dir(data_dir), os.path.basename(version["stored_name"]))
    with open(path, "rb") as fh:
        data = fh.read()
    if sha256_bytes(data) != version["sha256"]:
        raise ReportError("Stored report file does not match its recorded fingerprint (possible tampering "
                          "or corruption). Restore it from backup.")
    return data


def rerender(db, version):
    """Re-create the report from its stored snapshot (proves reproducibility)."""
    spec = REPORT_TYPES[version["report_type"]]
    snapshot = json.loads(version["snapshot_json"])
    if sha256_bytes(version["snapshot_json"].encode("utf-8")) != version["snapshot_sha256"]:
        raise ReportError("Stored snapshot does not match its fingerprint.")
    from modules.rules import rule_set_by_id
    rs = rule_set_by_id(db, version["rule_set_id"])
    checks = json.loads(version["checks_json"] or "[]")
    return spec["render"](snapshot, _ctx(version["version"], version["generated_at"], version["generated_by_name"],
                                         rs, version["snapshot_sha256"], checks))
