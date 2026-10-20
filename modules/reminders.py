"""Date-driven WSP / skills-development reminders.

Pure read-only computation over the operational trackers. The same function
feeds both the in-app reminder panels and (later) the scheduled e-mail job, so
there is one source of truth for "what needs attention".

Each reminder is a dict:
    {level, title, detail, endpoint, args}
level is 'danger' | 'warning' | 'info' (maps to Bootstrap alert classes).
"""
from datetime import date

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def _mlabel(y, m):
    return "%s %d" % (MONTHS[m - 1], y)


def _prev_month(today):
    return (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)


def compute_wsp_reminders(db, today=None):
    today = today or date.today()
    out = []
    py, pm = _prev_month(today)
    pm_prefix = "%04d-%02d%%" % (py, pm)
    pm_key = "%04d-%02d" % (py, pm)

    # 1. Monthly training capture — nothing logged for last month
    if db.execute("SELECT COUNT(*) c FROM training_events WHERE training_date LIKE ?", (pm_prefix,)).fetchone()["c"] == 0:
        out.append({"level": "warning", "title": "Log training for %s" % _mlabel(py, pm),
                    "detail": "No training has been captured for last month.",
                    "endpoint": "wsp_training_log", "args": {"year": py, "month": pm}})

    # 2. EMP201 / SDL — last month not recorded
    row = db.execute("SELECT emp201_received FROM sdl_tracker WHERE period_month=?", (pm_key,)).fetchone()
    if row is None or not row["emp201_received"]:
        out.append({"level": "warning", "title": "Record EMP201 / SDL for %s" % _mlabel(py, pm),
                    "detail": "Confirm the EMP201 was submitted and the SDL paid.",
                    "endpoint": "wsp_sdl", "args": {"year": py}})

    # 3. Quarterly mandatory-grant verification
    q = (today.month - 1) // 3 + 1
    gr = db.execute("SELECT status FROM grants WHERE grant_type='Mandatory' AND year=? AND quarter=?",
                    (today.year, "Q%d" % q)).fetchone()
    if gr is None or gr["status"] != "Received":
        out.append({"level": "info", "title": "Verify mandatory grant (Q%d %d)" % (q, today.year),
                    "detail": "Confirm receipt of the MICT SETA mandatory grant for this quarter.",
                    "endpoint": "wsp_grants", "args": {"year": today.year}})

    # 4. WSP / ATR submission deadline approaching
    dl = db.execute("""SELECT period_label, due_date FROM compliance_records
                       WHERE module='wsp' AND deleted=0 AND due_date IS NOT NULL AND due_date >= ?
                       ORDER BY due_date LIMIT 1""", (today.isoformat(),)).fetchone()
    if dl:
        try:
            days = (date.fromisoformat(dl["due_date"]) - today).days
            if days <= 90:
                out.append({"level": "danger" if days <= 30 else "warning",
                            "title": "WSP/ATR submission due %s" % dl["due_date"],
                            "detail": "%d day(s) to the %s submission." % (days, dl["period_label"]),
                            "endpoint": "wsp", "args": {}})
        except ValueError:
            pass

    # 5. Learnership / internship recruitment cycle
    for p in db.execute("""SELECT title, recruitment_start FROM learnership_plans
                           WHERE recruitment_start IS NOT NULL AND status='Planned'""").fetchall():
        try:
            rs = date.fromisoformat(p["recruitment_start"])
        except ValueError:
            continue
        days = (rs - today).days
        if 0 <= days <= 45:
            out.append({"level": "info", "title": "Recruitment starts soon: %s" % p["title"],
                        "detail": "Recruitment begins %s (in %d day(s))." % (p["recruitment_start"], days),
                        "endpoint": "wsp_learnerships", "args": {"year": rs.year}})
    if today.month in (9, 10) and db.execute(
            "SELECT COUNT(*) c FROM learnership_plans WHERE year=?", (today.year,)).fetchone()["c"] == 0:
        out.append({"level": "info", "title": "Plan the learnership / internship intake",
                    "detail": "Recruitment usually begins in October — no programmes are planned yet.",
                    "endpoint": "wsp_learnerships", "args": {"year": today.year}})

    # 6. Bursary application period
    if today.month >= 9 and db.execute(
            "SELECT COUNT(*) c FROM bursary_awards WHERE year=?", (today.year,)).fetchone()["c"] == 0:
        out.append({"level": "info", "title": "Bursary applications",
                    "detail": "No bursaries recorded for %d yet — the application period may be open." % today.year,
                    "endpoint": "wsp_bursaries", "args": {"year": today.year}})

    return out
