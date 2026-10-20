"""Versioned schema migrations for the SQLite database.

Each migration is (version, description, function). Migrations run in order, once,
inside a transaction, and are recorded in `schema_migrations`. Before any pending
migration runs on an existing database, a full copy is written to DATA_DIR/backups.

Never edit a migration that has shipped: add a new one.
"""
import os
import sqlite3
from datetime import datetime


def _columns(db, table):
    return {r[1] for r in db.execute("PRAGMA table_info(%s)" % table).fetchall()}


def _script(db, sql):
    """Run a multi-statement script statement by statement. (sqlite3.executescript
    would COMMIT first, breaking the per-migration transaction.)"""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                db.execute(buf)
            buf = ""
    if buf.strip():
        db.execute(buf)


def _add_column(db, table, col, coldef):
    if col not in _columns(db, table):
        db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, coldef))


# ─── 1. Baseline: the schema as it existed before versioning ─────────────────

def m001_baseline(db):
    _script(db, """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_number TEXT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            id_number TEXT,
            race TEXT,
            gender TEXT,
            disability TEXT DEFAULT 'No',
            occupational_level TEXT,
            department TEXT,
            job_title TEXT,
            salary REAL,
            employment_date TEXT,
            province TEXT,
            nationality TEXT,
            coida_class_code TEXT,
            coida_subclass TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS training_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER,
            employee_name TEXT,
            intervention_type TEXT,
            nqf_level TEXT,
            cost REAL,
            provider TEXT,
            target_date TEXT,
            actual_date TEXT,
            certificate TEXT,
            status TEXT DEFAULT 'Planned',
            seta TEXT,
            sdl_number TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees(id)
        );
        CREATE TABLE IF NOT EXISTS coida_classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_code TEXT,
            subclass TEXT,
            description TEXT,
            num_employees INTEGER DEFAULT 0,
            annual_earnings REAL DEFAULT 0,
            assessment_year TEXT,
            employer_reg_number TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS logs_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            insurer TEXT,
            policy_number TEXT,
            issue_date TEXT,
            expiry_date TEXT,
            status TEXT DEFAULT 'Valid',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bbbee_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scorecard_type TEXT,
            ownership_score REAL DEFAULT 0,
            management_score REAL DEFAULT 0,
            skills_score REAL DEFAULT 0,
            esd_score REAL DEFAULT 0,
            sed_score REAL DEFAULT 0,
            total_score REAL DEFAULT 0,
            bbbee_level INTEGER DEFAULT 8,
            black_owned_percent REAL DEFAULT 0,
            assessment_year TEXT,
            company_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS company_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            reg_number TEXT,
            reporting_year TEXT,
            designated_employer TEXT,
            seta TEXT,
            sdl_number TEXT,
            skills_levy REAL DEFAULT 0,
            employer_reg_number TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS checklist_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            item_key TEXT NOT NULL,
            checked INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(module, item_key)
        );
        CREATE TABLE IF NOT EXISTS custom_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            step_key TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS hidden_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            item_key TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(module, item_key)
        );
        CREATE TABLE IF NOT EXISTS gbs_claim (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            claim_quarter TEXT,
            claim_year TEXT,
            dtic_notification_date TEXT,
            submission_deadline TEXT,
            num_campaigns INTEGER DEFAULT 0,
            num_qualifying_employees INTEGER DEFAULT 0,
            export_revenue REAL DEFAULT 0,
            premises TEXT,
            auditor TEXT,
            notes TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS gbs_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quarter TEXT NOT NULL,
            year INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Draft',
            company_name TEXT,
            dtic_notification_date TEXT,
            submission_deadline TEXT,
            num_campaigns INTEGER DEFAULT 0,
            num_qualifying_employees INTEGER DEFAULT 0,
            export_revenue REAL DEFAULT 0,
            premises TEXT,
            auditor TEXT,
            notes TEXT,
            submitted_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            full_name TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            username TEXT,
            role TEXT,
            action TEXT,
            category TEXT,
            detail TEXT,
            target TEXT
        );
        CREATE TABLE IF NOT EXISTS deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dkey TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            authority TEXT,
            due_date TEXT,
            note TEXT,
            sort_order INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    for col, coldef in {
        "trashed": "INTEGER DEFAULT 0",
        "deleted_at": "TEXT",
        "deleted_by": "TEXT",
        "purge_at": "TEXT",
        "purge_approved_at": "TEXT",
        "purge_approved_by": "TEXT",
    }.items():
        _add_column(db, "gbs_projects", col, coldef)


# ─── 2. Security: session invalidation, lockout, richer + immutable audit ───

def m002_security(db):
    for col, coldef in {
        "email": "TEXT",
        "session_version": "INTEGER NOT NULL DEFAULT 1",
        "failed_logins": "INTEGER NOT NULL DEFAULT 0",
        "locked_until": "TEXT",
        "password_changed_at": "TEXT",
        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        _add_column(db, "users", col, coldef)

    for col, coldef in {
        "user_id": "INTEGER",
        "ip": "TEXT",
        "entity": "TEXT",
        "entity_id": "TEXT",
        "before_json": "TEXT",
        "after_json": "TEXT",
    }.items():
        _add_column(db, "audit_log", col, coldef)

    _script(db, """
        CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);
        CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_log(entity, entity_id);
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    """)


# ─── 3. Compliance records: periods, workflow, report versions, evidence ────

def _append_only(table):
    return """
        CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t}
        BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t}
        BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;
    """.format(t=table)


def m003_compliance_records(db):
    import json
    from modules.rule_seed import RULE_SETS

    _script(db, """
        CREATE TABLE company_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT, trading_name TEXT, reg_number TEXT,
            paye_number TEXT, uif_number TEXT, sdl_number TEXT, coida_number TEXT,
            seta TEXT, industry TEXT, sector TEXT,
            physical_address TEXT, postal_address TEXT,
            contact_person TEXT, contact_email TEXT, contact_phone TEXT,
            financial_year_end TEXT, designated_employer TEXT,
            skills_levy REAL, annual_turnover REAL,
            black_ownership_pct REAL, black_women_ownership_pct REAL, ownership_notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE rule_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL, code TEXT NOT NULL, version TEXT NOT NULL,
            regulator TEXT, source_document TEXT, source_url TEXT,
            effective_from TEXT NOT NULL, effective_to TEXT,
            params_json TEXT NOT NULL, notes TEXT,
            last_reviewed TEXT, next_review TEXT,
            verified_by TEXT, verified_at TEXT,
            superseded_by INTEGER REFERENCES rule_sets(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(code, version)
        );
        CREATE TRIGGER rule_sets_params_frozen BEFORE UPDATE OF module, code, version, effective_from, params_json
        ON rule_sets BEGIN SELECT RAISE(ABORT, 'rule set content is immutable; add a new version'); END;
        CREATE TRIGGER rule_sets_no_delete BEFORE DELETE ON rule_sets
        BEGIN SELECT RAISE(ABORT, 'rule sets cannot be deleted'); END;

        CREATE TABLE compliance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module TEXT NOT NULL,
            period_label TEXT NOT NULL,
            period_year INTEGER,
            period_start TEXT, period_end TEXT, due_date TEXT,
            status TEXT NOT NULL DEFAULT 'DRAFT'
                CHECK (status IN ('DRAFT','PREPARED','AWAITING_REVIEW','REQUIRES_CHANGES',
                                  'APPROVED','SUBMITTED','ARCHIVED')),
            owner_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            reviewer_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            prepared_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            rule_set_id INTEGER REFERENCES rule_sets(id),
            official_source TEXT, notes TEXT, last_reviewed_at TEXT,
            deleted INTEGER NOT NULL DEFAULT 0,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_by INTEGER REFERENCES users(id) ON DELETE RESTRICT
        );
        CREATE UNIQUE INDEX ux_records_period ON compliance_records(module, period_label) WHERE deleted = 0;
        CREATE INDEX ix_records_due ON compliance_records(due_date);

        CREATE TABLE workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            event TEXT NOT NULL,
            from_status TEXT, to_status TEXT,
            user_id INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            username TEXT,
            at TEXT NOT NULL DEFAULT (datetime('now')),
            comment TEXT, payload_json TEXT
        );
        CREATE INDEX ix_events_record ON workflow_events(record_id, id);

        CREATE TABLE report_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            report_type TEXT NOT NULL,
            version INTEGER NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            generated_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            generated_by_name TEXT,
            reason TEXT,
            snapshot_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
            company_profile_id INTEGER REFERENCES company_profiles(id),
            rule_set_id INTEGER REFERENCES rule_sets(id),
            file_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
            mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            checks_json TEXT, checks_ok INTEGER NOT NULL DEFAULT 1,
            UNIQUE(record_id, report_type, version)
        );

        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            report_version_ids TEXT,
            submitted_on TEXT NOT NULL,
            submitted_by_name TEXT,
            reference_no TEXT, confirmation_no TEXT, notes TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            recorded_by INTEGER REFERENCES users(id) ON DELETE RESTRICT
        );

        CREATE TABLE evidence_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            kind TEXT NOT NULL, description TEXT,
            original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
            mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id) ON DELETE RESTRICT,
            uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE coida_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            class_code TEXT NOT NULL, subclass TEXT NOT NULL DEFAULT '',
            description TEXT, rate REAL NOT NULL CHECK (rate >= 0),
            source_note TEXT,
            UNIQUE(record_id, class_code, subclass)
        );

        CREATE TABLE coida_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL REFERENCES compliance_records(id) ON DELETE RESTRICT,
            employee_id INTEGER REFERENCES employees(id) ON DELETE RESTRICT,
            employee_number TEXT, employee_name TEXT,
            class_code TEXT NOT NULL, subclass TEXT NOT NULL DEFAULT '',
            earnings REAL NOT NULL CHECK (earnings >= 0),
            months_worked REAL,
            notes TEXT,
            UNIQUE(record_id, employee_id)
        );

        CREATE TABLE bbbee_inputs (
            record_id INTEGER PRIMARY KEY REFERENCES compliance_records(id) ON DELETE RESTRICT,
            scorecard_type TEXT NOT NULL,
            annual_turnover REAL,
            black_owned_pct REAL, black_women_owned_pct REAL,
            inputs_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_by INTEGER REFERENCES users(id) ON DELETE RESTRICT
        );
    """)
    for t in ("company_profiles", "workflow_events", "report_versions", "submissions", "evidence_files"):
        _script(db, _append_only(t))

    # Employees: lifecycle + nationality split out of race
    for col, coldef in {
        "status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        "employment_type": "TEXT NOT NULL DEFAULT 'Permanent'",
        "is_foreign_national": "INTEGER NOT NULL DEFAULT 0",
        "termination_date": "TEXT",
        "termination_reason": "TEXT",
        "archived_at": "TEXT",
        "updated_at": "TEXT",
        "updated_by": "INTEGER",
        "row_version": "INTEGER NOT NULL DEFAULT 1",
    }.items():
        _add_column(db, "employees", col, coldef)
    db.execute("UPDATE employees SET is_foreign_national=1, race=NULL WHERE race='Foreign National'")
    db.execute("UPDATE employees SET employment_type='Temporary' WHERE occupational_level='Non-Permanent'")
    db.execute("UPDATE employees SET occupational_level=NULL WHERE occupational_level='Non-Permanent'")
    # Duplicate employee numbers must be resolved before the unique index can exist.
    dups = db.execute("""SELECT id, emp_number FROM employees e WHERE emp_number IS NOT NULL AND emp_number<>''
                         AND id > (SELECT MIN(id) FROM employees WHERE emp_number=e.emp_number)""").fetchall()
    for emp_id, num in dups:
        new = "%s-DUP-%d" % (num, emp_id)
        db.execute("UPDATE employees SET emp_number=? WHERE id=?", (new, emp_id))
        db.execute("""INSERT INTO audit_log (username, role, action, category, detail, entity, entity_id)
                      VALUES ('system','-','employee_number_dedup','employee',?, 'employee', ?)""",
                   ("Duplicate employee number %s renamed to %s during upgrade" % (num, new), str(emp_id)))
    _script(db, """
        CREATE UNIQUE INDEX ux_employees_number ON employees(emp_number)
            WHERE emp_number IS NOT NULL AND emp_number <> '';
        CREATE INDEX ix_employees_status ON employees(status);
        CREATE INDEX ix_employees_name ON employees(last_name, first_name);
    """)

    for col, coldef in {
        "record_id": "INTEGER REFERENCES compliance_records(id)",
        "employee_number": "TEXT",
        "category": "TEXT",
        "skills_gap": "TEXT",
    }.items():
        _add_column(db, "training_records", col, coldef)
    db.execute("""UPDATE training_records SET employee_number =
                  (SELECT emp_number FROM employees WHERE employees.id = training_records.employee_id)""")
    _add_column(db, "gbs_projects", "record_id", "INTEGER REFERENCES compliance_records(id)")

    # Seed rule sets
    for rs in RULE_SETS:
        db.execute(
            """INSERT INTO rule_sets (module, code, version, regulator, source_document, source_url,
                                      effective_from, params_json, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (rs["module"], rs["code"], rs["version"], rs["regulator"], rs["source_document"],
             rs["source_url"], rs["effective_from"], json.dumps(rs["params"]), rs["notes"]))

    # Company info → first company profile version
    ci = db.execute("SELECT * FROM company_info ORDER BY id LIMIT 1").fetchone()
    if ci:
        cols = [r[1] for r in db.execute("PRAGMA table_info(company_info)")]
        c = dict(zip(cols, ci))
        db.execute(
            """INSERT INTO company_profiles (company_name, reg_number, designated_employer, seta,
                                             sdl_number, skills_levy, coida_number)
               VALUES (?,?,?,?,?,?,?)""",
            (c.get("company_name"), c.get("reg_number"), c.get("designated_employer"), c.get("seta"),
             c.get("sdl_number"), c.get("skills_levy"), c.get("employer_reg_number")))

    # Deadlines → compliance records for the period each deadline belongs to
    official = {
        "ee": "https://www.labour.gov.za", "wsp": "", "coida": "https://www.labour.gov.za/compensation-fund",
        "cipc_bo": "https://www.cipc.co.za", "gbs": "https://www.thedtic.gov.za", "bbbee": "",
    }
    for dkey, due, note in db.execute("SELECT dkey, due_date, note FROM deadlines").fetchall():
        if not due:
            continue
        y = int(due[:4])
        if dkey == "ee":
            label, start, end = "%d/%d" % (y - 2, y - 1), "%d-10-01" % (y - 2), "%d-09-30" % (y - 1)
        elif dkey == "coida":
            label, start, end = str(y - 1), "%d-03-01" % (y - 1), "%d-02-28" % y
        else:
            label, start, end = str(y), None, None
        db.execute(
            """INSERT INTO compliance_records (module, period_label, period_year, period_start, period_end,
                                               due_date, official_source, notes)
               VALUES (?,?,?,?,?,?,?,?)""",
            (dkey, label, y, start, end, due, official.get(dkey), note))

    # GBS projects → one compliance record per claim
    status_map = {"Draft": "DRAFT", "In Progress": "DRAFT", "Submitted": "SUBMITTED"}
    for pid, q, yr, st, deadline, trashed, submitted_at in db.execute(
            "SELECT id, quarter, year, status, submission_deadline, trashed, submitted_at FROM gbs_projects"):
        label = "%s %d" % (q, yr)
        deleted = 1 if trashed else 0
        if not deleted and db.execute(
                "SELECT 1 FROM compliance_records WHERE module='gbs' AND period_label=? AND deleted=0",
                (label,)).fetchone():
            label = "%s %d (#%d)" % (q, yr, pid)  # pre-existing duplicate claim kept distinct
        qn = int(q[1])
        start = "%d-%02d-01" % (yr, (qn - 1) * 3 + 1)
        cur = db.execute(
            """INSERT INTO compliance_records (module, period_label, period_year, period_start,
                                               due_date, status, deleted, official_source)
               VALUES ('gbs',?,?,?,?,?,?,?)""",
            (label, yr, start, deadline or None, status_map.get(st, "DRAFT"), deleted,
             official["gbs"]))
        rid = cur.lastrowid
        db.execute("UPDATE gbs_projects SET record_id=? WHERE id=?", (rid, pid))
        db.execute("""INSERT INTO workflow_events (record_id, event, to_status, username, comment)
                      VALUES (?, 'migrated', ?, 'system', 'Created from existing GBS project during upgrade')""",
                   (rid, status_map.get(st, "DRAFT")))
        # Checklist state moves with the claim to the record scope
        for t in ("checklist_state", "custom_tasks", "hidden_tasks"):
            db.execute("UPDATE %s SET module=? WHERE module=?" % t, ("record:%d" % rid, "gbs:%d" % pid))
    for (rid, module) in db.execute("SELECT id, module FROM compliance_records WHERE module<>'gbs'").fetchall():
        db.execute("""INSERT INTO workflow_events (record_id, event, to_status, username, comment)
                      VALUES (?, 'migrated', 'DRAFT', 'system', 'Created from statutory deadline during upgrade')""",
                   (rid,))
        if module == "ee":  # the single global EE checklist belongs to the current EE period
            for t in ("checklist_state", "custom_tasks", "hidden_tasks"):
                db.execute("UPDATE %s SET module=? WHERE module='ee'" % t, ("record:%d" % rid,))


# ─── 4. Dash BPO: MICT SETA only + SDL reference ─────────────────────────────

def m004_mict_seta_sdl(db):
    """Dash BPO uses MICT SETA only, SDL reference L640813558. Append a company-profile
    version with these set (merging any existing values) unless already correct."""
    SETA, SDL = "MICT SETA", "L640813558"
    data_cols = [r[1] for r in db.execute("PRAGMA table_info(company_profiles)").fetchall()
                 if r[1] not in ("id", "created_at")]
    row = db.execute(
        "SELECT %s FROM company_profiles ORDER BY id DESC LIMIT 1" % ", ".join(data_cols)).fetchone()
    cur = dict(zip(data_cols, row)) if row else {c: None for c in data_cols}
    if cur.get("seta") == SETA and cur.get("sdl_number") == SDL:
        return
    cur["seta"], cur["sdl_number"], cur["created_by"] = SETA, SDL, None
    db.execute("INSERT INTO company_profiles (%s) VALUES (%s)"
               % (", ".join(data_cols), ", ".join("?" * len(data_cols))),
               [cur.get(c) for c in data_cols])
    db.execute("""INSERT INTO audit_log (username, role, action, category, detail, entity)
                  VALUES ('system','-','company_seta_sdl_set','company',
                          'Set SETA to MICT SETA and SDL number to L640813558 during upgrade','company_profile')""")


# ─── 5. WSP: monthly SDL / EMP201 tracker ────────────────────────────────────

def m005_sdl_tracker(db):
    _script(db, """
        CREATE TABLE sdl_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_month TEXT NOT NULL UNIQUE,          -- 'YYYY-MM'
            emp201_received INTEGER NOT NULL DEFAULT 0,
            sdl_payable REAL,
            sdl_paid REAL,
            received_date TEXT,
            notes TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX ix_sdl_month ON sdl_tracker(period_month);
    """)


MIGRATIONS = [
    (1, "baseline schema", m001_baseline),
    (2, "security: sessions, lockout, immutable audit", m002_security),
    (3, "compliance records, workflow, report versions, evidence, rules", m003_compliance_records),
    (4, "Dash BPO: MICT SETA + SDL number", m004_mict_seta_sdl),
    (5, "WSP: monthly SDL/EMP201 tracker", m005_sdl_tracker),
]


def backup_database(db_path, backup_dir, label):
    """Consistent online copy using SQLite's backup API. Returns the backup path."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, "hr_audit-%s-%s.db" % (label, stamp))
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def migrate(db_path, backup_dir):
    """Apply pending migrations. Returns the list of versions applied."""
    existed = os.path.exists(db_path) and os.path.getsize(db_path) > 0
    db = sqlite3.connect(db_path, isolation_level=None)  # explicit BEGIN/COMMIT below
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        description TEXT,
                        applied_at TEXT DEFAULT (datetime('now')))""")
        done = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
        pending = [m for m in MIGRATIONS if m[0] not in done]
        if not pending:
            return []
        if existed:
            backup_database(db_path, backup_dir, "pre-migration-v%d" % pending[0][0])
        applied = []
        for version, desc, fn in pending:
            try:
                db.execute("BEGIN")
                fn(db)
                db.execute("INSERT INTO schema_migrations (version, description) VALUES (?,?)",
                           (version, desc))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            applied.append(version)
        return applied
    finally:
        db.close()
