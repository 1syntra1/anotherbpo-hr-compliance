"""Compliance records (one per module per reporting period) and their workflow.

All state changes go through `transition()`, which enforces the allowed moves,
who may make them, and the preconditions. Routes never set `status` directly.
"""
import json
from datetime import date, datetime

MODULES = {
    "ee":      {"title": "Employment Equity", "authority": "Dept of Employment and Labour",
                "reports": ["eea2", "eea4"], "page": "ee"},
    "wsp":     {"title": "WSP / ATR", "authority": "SETA",
                "reports": ["wsp", "atr", "wsp_docx"], "page": "wsp"},
    "coida":   {"title": "COIDA Return of Earnings", "authority": "Compensation Fund",
                "reports": ["was8"], "page": "coida"},
    "bbbee":   {"title": "B-BBEE", "authority": "B-BBEE Commission / verification agency",
                "reports": ["bbbee_xlsx", "bbbee_pdf"], "page": "bbbee"},
    "gbs":     {"title": "GBS Quarterly Claim", "authority": "the dtic", "reports": [], "page": "gbs"},
    "cipc_bo": {"title": "CIPC Beneficial Ownership", "authority": "CIPC", "reports": [], "page": None},
}

STATUS_LABELS = {
    "DRAFT": "Draft",
    "PREPARED": "Prepared",
    "AWAITING_REVIEW": "Awaiting review",
    "REQUIRES_CHANGES": "Requires changes",
    "APPROVED": "Approved",
    "SUBMITTED": "Submitted",
    "ARCHIVED": "Archived",
}
EDITABLE = {"DRAFT", "PREPARED", "REQUIRES_CHANGES"}
CHECKLIST_LOCKED = {"SUBMITTED", "ARCHIVED"}
DONE = {"SUBMITTED", "ARCHIVED"}

# action: (allowed from-statuses, to-status, who, needs_comment, label)
#   who: "any" = any signed-in user, "reviewer" = assigned reviewer or admin, "admin"
TRANSITIONS = {
    "prepare":        ({"DRAFT"}, "PREPARED", "any", False, "Mark as prepared"),
    "unprepare":      ({"PREPARED"}, "DRAFT", "any", False, "Back to draft"),
    "request_review": ({"PREPARED"}, "AWAITING_REVIEW", "any", False, "Request review"),
    "resubmit":       ({"REQUIRES_CHANGES"}, "AWAITING_REVIEW", "any", False, "Resubmit for review"),
    "approve":        ({"AWAITING_REVIEW"}, "APPROVED", "reviewer", False, "Approve"),
    "reject":         ({"AWAITING_REVIEW"}, "REQUIRES_CHANGES", "reviewer", True, "Request changes"),
    "withdraw_approval": ({"APPROVED"}, "REQUIRES_CHANGES", "reviewer", True, "Withdraw approval"),
    "submit":         ({"APPROVED"}, "SUBMITTED", "any", False, "Record submission"),
    "reopen":         ({"SUBMITTED"}, "REQUIRES_CHANGES", "admin", True, "Reopen (admin)"),
    "archive":        ({"SUBMITTED"}, "ARCHIVED", "admin", False, "Archive"),
}


class WorkflowError(Exception):
    pass


class ConflictError(WorkflowError):
    """Someone else changed the record since the form was loaded."""


def default_period(module, year, deadline_params):
    """Suggested (label, start, end, due) for a new record; all editable afterwards."""
    from datetime import timedelta
    rule = (deadline_params or {}).get(module) or {}
    label, start, end = str(year), None, None
    if module == "ee":
        label, start, end = "%d/%d" % (year - 1, year), "%d-10-01" % (year - 1), "%d-09-30" % year
    elif module == "coida":
        start, end = "%d-03-01" % year, (date(year + 1, 3, 1) - timedelta(days=1)).isoformat()
    elif module == "wsp":
        start, end = "%d-04-01" % (year - 1), "%d-03-31" % year
    elif module == "bbbee":
        start, end = "%d-03-01" % (year - 1), (date(year, 3, 1) - timedelta(days=1)).isoformat()
    due = None
    if rule.get("rule") == "fixed":
        mm, dd = rule["mmdd"].split("-")
        due = "%d-%s-%s" % (year + rule.get("year_offset", 0), mm, dd)
    return label, start, end, due


def module_title(module):
    return MODULES.get(module, {}).get("title", module)


def get_record(db, record_id):
    row = db.execute("SELECT * FROM compliance_records WHERE id=?", (record_id,)).fetchone()
    return dict(row) if row else None


def _event(db, record_id, event, user, from_status=None, to_status=None, comment=None, payload=None):
    db.execute(
        """INSERT INTO workflow_events (record_id, event, from_status, to_status, user_id, username,
                                        comment, payload_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (record_id, event, from_status, to_status, user["id"] if user else None,
         user["username"] if user else "system", comment,
         json.dumps(payload, default=str, ensure_ascii=False) if payload is not None else None))


def add_event(db, record_id, event, user, comment=None, payload=None):
    """Timeline entry that is not a status change (report generated, evidence added...)."""
    _event(db, record_id, event, user, comment=comment, payload=payload)


def create_record(db, module, period_label, user, period_year=None, period_start=None, period_end=None,
                  due_date=None, owner_id=None, reviewer_id=None, rule_set_id=None, official_source=None,
                  notes=None):
    if module not in MODULES:
        raise WorkflowError("Unknown compliance module.")
    period_label = (period_label or "").strip()
    if not period_label or len(period_label) > 40:
        raise WorkflowError("A reporting period label (max 40 characters) is required.")
    for label, d in (("Period start", period_start), ("Period end", period_end), ("Due date", due_date)):
        if d:
            try:
                date.fromisoformat(d)
            except ValueError:
                raise WorkflowError("%s is not a valid date." % label)
    if period_start and period_end and period_start > period_end:
        raise WorkflowError("Period start must be before period end.")
    if db.execute("SELECT 1 FROM compliance_records WHERE module=? AND period_label=? AND deleted=0",
                  (module, period_label)).fetchone():
        raise WorkflowError("A %s record for %s already exists." % (module_title(module), period_label))
    cur = db.execute(
        """INSERT INTO compliance_records (module, period_label, period_year, period_start, period_end,
               due_date, owner_id, reviewer_id, rule_set_id, official_source, notes, created_by, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (module, period_label, period_year, period_start or None, period_end or None, due_date or None,
         owner_id, reviewer_id, rule_set_id, official_source, notes, user["id"], user["id"]))
    rid = cur.lastrowid
    _event(db, rid, "created", user, to_status="DRAFT",
           comment="%s %s created" % (module_title(module), period_label))
    return rid


def update_details(db, record, user, expected_version, **fields):
    """Edit owner/reviewer/due date/notes. Allowed in any non-archived status, because
    responsibility and dates are administrative, not report content."""
    if record["status"] == "ARCHIVED":
        raise WorkflowError("Archived records cannot be changed.")
    allowed = {"due_date", "owner_id", "reviewer_id", "notes", "official_source", "last_reviewed_at",
               "period_start", "period_end"}
    if record["status"] not in EDITABLE:
        allowed -= {"period_start", "period_end"}
    changes = {k: v for k, v in fields.items() if k in allowed and v != record.get(k)}
    for k in ("due_date", "period_start", "period_end", "last_reviewed_at"):
        if changes.get(k):
            try:
                date.fromisoformat(changes[k])
            except ValueError:
                raise WorkflowError("Invalid date.")
    if not changes:
        return {}
    sets = ", ".join("%s=?" % k for k in changes)
    cur = db.execute(
        "UPDATE compliance_records SET %s, updated_at=datetime('now'), updated_by=?, "
        "row_version=row_version+1 WHERE id=? AND row_version=?" % sets,
        list(changes.values()) + [user["id"], record["id"], expected_version])
    if cur.rowcount != 1:
        raise ConflictError("This record was changed by someone else. Reload and try again.")
    _event(db, record["id"], "details_updated", user,
           comment=", ".join(sorted(changes)), payload={k: [record.get(k), v] for k, v in changes.items()})
    return changes


def can_act(record, action, user):
    """(allowed, reason). Pure check; used by both the UI and transition()."""
    if action not in TRANSITIONS:
        return False, "Unknown action."
    froms, _to, who, _c, _l = TRANSITIONS[action]
    if record["deleted"]:
        return False, "This record is in the trash."
    if record["status"] not in froms:
        return False, "Not possible while the record is %s." % STATUS_LABELS[record["status"]]
    is_admin = user["role"] == "admin"
    if who == "admin" and not is_admin:
        return False, "Only an administrator can do this."
    if who == "reviewer" and not is_admin and user["id"] != record.get("reviewer_id"):
        return False, "Only the assigned reviewer or an administrator can do this."
    return True, ""


def transition(db, record_id, action, user, comment=None, checks_ok=None, override_reason=None,
               payload=None, expected_version=None):
    """Move a record through the workflow. Raises WorkflowError. Does not commit.

    checks_ok: result of the mandatory validation/reconciliation checks for the latest
    report versions (None when the module has no reports)."""
    record = get_record(db, record_id)
    if record is None:
        raise WorkflowError("Record not found.")
    if expected_version is not None and record["row_version"] != expected_version:
        raise ConflictError("This record was changed by someone else. Reload and try again.")
    ok, why = can_act(record, action, user)
    if not ok:
        raise WorkflowError(why)
    froms, to, who, needs_comment, label = TRANSITIONS[action]
    comment = (comment or "").strip() or None
    if needs_comment and not comment:
        raise WorkflowError("Please give a reason.")
    if action == "request_review" and not record.get("reviewer_id"):
        raise WorkflowError("Assign a reviewer before requesting review.")
    if action in ("approve", "submit", "request_review", "resubmit", "prepare") and checks_ok is False:
        raise WorkflowError("Mandatory validation or reconciliation checks are failing on the latest report. "
                            "Fix the data and regenerate before continuing.")
    extra = {}
    if action == "approve":
        if record.get("prepared_by") == user["id"]:
            if user["role"] != "admin":
                raise WorkflowError("You prepared this record, so someone else must approve it.")
            if not (override_reason or "").strip():
                raise WorkflowError("You prepared this record. As an administrator you may approve it only "
                                    "with a recorded override reason.")
            extra["override_reason"] = override_reason.strip()
    sets = "status=?, updated_at=datetime('now'), updated_by=?, row_version=row_version+1"
    params = [to, user["id"]]
    if action == "prepare":
        sets += ", prepared_by=?"
        params.append(user["id"])
    cur = db.execute("UPDATE compliance_records SET %s WHERE id=? AND row_version=?" % sets,
                     params + [record_id, record["row_version"]])
    if cur.rowcount != 1:
        raise ConflictError("This record was changed by someone else. Reload and try again.")
    if extra:
        payload = dict(payload or {}, **extra)
        comment = (comment + " — " if comment else "") + "Override: " + extra["override_reason"]
    _event(db, record_id, action, user, record["status"], to, comment, payload)
    return to


def timeline(db, record_id):
    return [dict(r) for r in db.execute(
        "SELECT * FROM workflow_events WHERE record_id=? ORDER BY id", (record_id,))]


def latest_versions(db, record_id):
    """{report_type: newest report_versions row} for a record."""
    out = {}
    for r in db.execute("""SELECT rv.* FROM report_versions rv
                           JOIN (SELECT report_type, MAX(version) v FROM report_versions
                                 WHERE record_id=? GROUP BY report_type) m
                           ON m.report_type=rv.report_type AND m.v=rv.version
                           WHERE rv.record_id=?""", (record_id, record_id)):
        out[r["report_type"]] = dict(r)
    return out


def checks_ok_for(db, record):
    """None if the module has no reports; False if any required report is missing or any
    latest version failed its checks; True otherwise."""
    required = MODULES.get(record["module"], {}).get("reports", [])
    if not required:
        return None
    latest = latest_versions(db, record["id"])
    primary = required[:2] if record["module"] == "ee" else required[:1]
    if any(t not in latest for t in primary):
        return False
    return all(v["checks_ok"] for v in latest.values())


# ─── Calendar / health ───────────────────────────────────────────────────────

def due_state(record, today=None):
    """(key, label) used for colour, sorting and text."""
    today = today or date.today()
    if record["status"] in DONE:
        return "done", STATUS_LABELS[record["status"]]
    if not record.get("due_date"):
        return "nodate", "No due date"
    try:
        days = (date.fromisoformat(record["due_date"]) - today).days
    except ValueError:
        return "nodate", "Invalid date"
    if days < 0:
        return "overdue", "Overdue by %d day%s" % (-days, "" if days == -1 else "s")
    if days == 0:
        return "due_soon", "Due today"
    if days <= 30:
        return "due_soon", "Due in %d day%s" % (days, "" if days == 1 else "s")
    return "upcoming", "Due in %d days" % days


def calendar(db, today=None):
    rows = db.execute(
        """SELECT r.*, o.full_name AS owner_name, v.full_name AS reviewer_name
           FROM compliance_records r
           LEFT JOIN users o ON o.id=r.owner_id
           LEFT JOIN users v ON v.id=r.reviewer_id
           WHERE r.deleted=0 AND r.status<>'ARCHIVED'""").fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["state"], d["state_label"] = due_state(d, today)
        d["title"] = module_title(d["module"])
        d["status_label"] = STATUS_LABELS[d["status"]]
        items.append(d)
    order = {"overdue": 0, "due_soon": 1, "upcoming": 2, "nodate": 3, "done": 4}
    items.sort(key=lambda x: (order[x["state"]], x.get("due_date") or "9999", x["title"]))
    return items


def health(items):
    """Overall % = completed records / records that are due or done. Records not yet
    due do not count against the score."""
    counts = {"done": 0, "overdue": 0, "due_soon": 0, "upcoming": 0, "nodate": 0}
    for i in items:
        counts[i["state"]] += 1
    scored = counts["done"] + counts["overdue"] + counts["due_soon"]
    pct = round(100 * counts["done"] / scored) if scored else 100
    return {"pct": pct, **counts}


def module_status(items):
    """One line per module: the most urgent open record, or the latest done one."""
    by = {}
    for i in items:  # items are already sorted most urgent first
        by.setdefault(i["module"], i)
    return [(m, MODULES[m]["title"], by.get(m)) for m in MODULES]


def notifications(db, items, user, today=None):
    """Actionable, de-noised notices for the signed-in user."""
    out = []
    for i in items:
        if i["state"] == "overdue":
            out.append(("danger", "%s %s is overdue." % (i["title"], i["period_label"]), i["id"]))
        elif i["state"] == "due_soon" and i["status"] not in ("APPROVED",):
            out.append(("warning", "%s %s: %s." % (i["title"], i["period_label"], i["state_label"].lower()),
                        i["id"]))
        if i["status"] == "AWAITING_REVIEW" and (i.get("reviewer_id") == user["id"] or
                                                (user["role"] == "admin" and not i.get("reviewer_id"))):
            out.append(("info", "%s %s is waiting for your review." % (i["title"], i["period_label"]), i["id"]))
        if i["status"] == "REQUIRES_CHANGES" and i.get("owner_id") == user["id"]:
            out.append(("warning", "%s %s needs changes." % (i["title"], i["period_label"]), i["id"]))
    for r in db.execute("""SELECT r.id, r.module, r.period_label FROM report_versions rv
                           JOIN compliance_records r ON r.id=rv.record_id
                           WHERE r.deleted=0 AND r.status NOT IN ('SUBMITTED','ARCHIVED') AND rv.checks_ok=0
                           AND rv.version = (SELECT MAX(version) FROM report_versions x
                                             WHERE x.record_id=rv.record_id AND x.report_type=rv.report_type)
                           GROUP BY r.id"""):
        out.append(("danger", "%s %s: report checks failed (reconciliation/validation)."
                    % (module_title(r["module"]), r["period_label"]), r["id"]))
    seen, uniq = set(), []
    for n in out:
        if n[1] not in seen:
            seen.add(n[1]); uniq.append(n)
    return uniq[:12]
