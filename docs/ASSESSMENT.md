# HR Compliance Suite — Stage 1 Assessment & Stage 2 Database Design

> Historical record of the starting point. Current state: see `docs/QA_REPORT.md`.

Date: 12 October 2026
Scope: full read of `app.py` (1,928 lines), `modules/*.py`, templates, `static/js/main.js`.
Execution status: **no code has been executed and no tests have been run.** This machine has no
Python interpreter, Docker or WSL. Every finding below comes from reading the source. Findings marked
"verify" depend on external regulation and must be confirmed against the current official source.

---

## 1. Executive summary

The application is a working single-file Flask app with good bones: parameterised SQL throughout,
hashed passwords, an admin/user split, a GBS trash bin with admin-approved purge, and a reusable
checklist engine. It is, however, a **report generator over live data**, not a compliance record
system:

- Every report is rebuilt from the *current* employee table at download time. Nothing generated is
  stored. A historical EEA2 cannot be reproduced once any employee changes. This is the central gap.
- There is no reporting-period concept. "Reporting year" is a free-text field on one shared
  `company_info` row that every module overwrites.
- Employees are hard-deleted by any user. There is no terminated/archived status or termination date.
- Several calculations disagree with the regulations as I understand them (B-BBEE levels, QSE and
  EME rules, COIDA rates). These need verification before anyone files from this system.
- There is no CSRF protection anywhere, and the secret key and seed passwords are in the public repo.

**Current status: NOT READY.** Several CRITICAL findings are open, and none of the testing stages
have been run.

---

## 2. Architecture as found

| Layer | Implementation |
|---|---|
| App | Single `app.py`, 47 routes, Flask sessions, decorators `login_required` / `admin_required` |
| Data | SQLite, 14 tables created by `init_db()` on import. Migrations are ad-hoc `ALTER TABLE` on one table |
| Reports | `modules/gbs.py` (EEA2/EEA4, despite the name), `wsp.py`, `bbbee.py`, `coida.py`. All take a live `db` handle and query current data |
| Checklists | `modules/checklists.py` static definitions plus `checklist_state` / `custom_tasks` / `hidden_tasks` keyed by a scope string (`ee`, `gbs:5`). The scope mechanism extends cleanly to per-period records |
| Front end | Jinja2 + Bootstrap 5.3.3 from jsDelivr CDN, one `main.js` (checklist AJAX, idle logout) |
| Tests | **None exist** |

Strengths to keep: the parameterised queries, the checklist scope design, GBS trash/approval/purge
flow, the audit-log page and filters, the deadline view helper, and the report styling.

---

## 3. Findings

Severity: CRITICAL / HIGH / MEDIUM / LOW. Line numbers refer to `app.py` unless stated.

### 3.1 Historical integrity and data model

| # | Sev | Finding | Where |
|---|---|---|---|
| D1 | CRITICAL | Reports are generated from live data and never stored. A historical report cannot be reproduced | `ee_generate`, `wsp_generate`, `coida_generate`, `bbbee_generate` |
| D2 | CRITICAL | No reporting periods. EE/WSP/COIDA data and reports are not tied to any period | schema |
| D3 | CRITICAL | `DELETE FROM employees` is available to every logged-in user. No status, termination date or archive | `delete_employee` :808 |
| D4 | HIGH | One `company_info` row shared by all modules. The EE form and WSP form each overwrite parts of it, and edits change every past report | :867, :1306 |
| D5 | HIGH | `PRAGMA foreign_keys` is never enabled, so `training_records.employee_id` can orphan silently | `get_db` |
| D6 | HIGH | No unique constraint on `emp_number`. Imports can create duplicates freely | schema |
| D7 | MEDIUM | Money stored as `REAL` (float). Rounding drift in totals | `salary`, `cost`, `annual_earnings` |
| D8 | MEDIUM | Dates stored as unvalidated TEXT on employees, training and LOGS | various |
| D9 | MEDIUM | No indexes beyond PKs/uniques. This is fine now, but needed at 5–10k employees | schema |
| D10 | LOW | `gbs_claim` table is legacy and unused | schema |
| D11 | MEDIUM | COIDA earnings are stored as hand-typed class aggregates, not derived from employees per assessment year | `coida_classes` |
| D12 | MEDIUM | No migration versioning. Schema changes cannot be tracked or rolled back | `init_db` |

### 3.2 Security

| # | Sev | Finding | Where |
|---|---|---|---|
| S1 | CRITICAL | Secret key falls back to a constant committed to a public repo, so session cookies can be forged (including `role=admin`) | :15 |
| S2 | CRITICAL | Seed accounts `admin`/`user` with password `anotherbpo2024`, which is in the public repo and also printed by `start.bat` | :299, `start.bat` |
| S3 | CRITICAL | No CSRF protection on any POST route. A malicious page can make a logged-in admin create users, change roles or approve purges | all forms |
| S4 | HIGH | `start.bat` runs `app.run(debug=True, host="0.0.0.0")`, which exposes the Werkzeug debugger to the LAN (remote code execution if the PIN is obtained) | :1928 |
| S5 | HIGH | Role and identity are cached in the session cookie for up to 30 days. Deleting or demoting a user does not end their session | `login`, `admin_required` |
| S6 | HIGH | Standard users can: hard-delete employees, edit statutory deadlines, submit and reopen GBS claims with no review, and edit checklists of submitted claims. All are enforced only by UI, or not at all | :622, :808, :1125, :1144, checklist routes |
| S7 | HIGH | Checklist endpoints accept any `module` string. A user can write checklist state into a submitted GBS project (`gbs:N`) or invent scopes | :1555–1724 |
| S8 | HIGH | Formula injection: text from user input (employee names, providers, programme names) is written into generated Excel. Values beginning `=`, `+`, `-` or `@` become live formulas | `modules/wsp.py` and others |
| S9 | HIGH | No upload size limit (`MAX_CONTENT_LENGTH` unset). Uploads are saved to disk under the user-supplied name, overwrite each other, and are never cleaned up | :704 |
| S10 | MEDIUM | No login rate-limit or lockout | `login` |
| S11 | MEDIUM | Cookie flags not set: `SESSION_COOKIE_SECURE`, `SAMESITE=Lax` | config |
| S12 | MEDIUM | Import errors flash the raw exception text to the user | :731 |
| S13 | MEDIUM | No password policy for new users or resets | `users_add`, `users_reset` |
| S14 | MEDIUM | `/gbs/<id>/submit` and `/reopen` do not check `trashed` or current status (submit is replayable; a trashed project can be reopened) | :1125, :1144 |
| S15 | LOW | Logout is a GET (cross-site logout possible) | :538 |
| S16 | LOW | Account-enumeration timing: the password hash is only checked when the username exists | `login` |
| S17 | LOW | `/api/bbbee/level` returns 500 on a non-JSON body | :1727 |

SQL injection: every query is parameterised. The one string-built statement (`ALTER TABLE … %s`) uses
constants only. **No SQLi found by reading the code, which is not the same as testing.**
XSS: Jinja2 autoescape is on, no `|safe` is used, and `main.js` escapes custom task text.
**No XSS found by reading the code, also not tested.**

### 3.3 Audit log

| # | Sev | Finding |
|---|---|---|
| A1 | HIGH | Not logged: employee create/edit/delete, imports, company changes, B-BBEE, COIDA, WSP, every report generation |
| A2 | HIGH | No IP address, entity ID, or before/after values |
| A3 | MEDIUM | `log_action` swallows all errors, so audit failure is silent |
| A4 | MEDIUM | Nothing in the database prevents UPDATE/DELETE on `audit_log`. A purge is impossible through the UI, but not protected at DB level |

### 3.4 Calculations and regulatory rules (verify against official sources)

| # | Sev | Finding | Where |
|---|---|---|---|
| R1 | CRITICAL | COIDA rates are labelled in code as "approximate… indicative", keyed by letters A–O. Compensation Fund tariffs are published per industry class/subclass each year. **Any W.As.8 figure from this code is unreliable** | `modules/coida.py:14–33` |
| R2 | HIGH | COIDA maximum earnings ceiling hard-coded at R563,520 ("2024/2025"). This changes annually and is not period-aware | `coida.py:36` |
| R3 | HIGH | B-BBEE Generic thresholds: code has Level 6 at ≥65 and Level 8 at ≥0. As I understand the Amended Codes (2013), Level 6 is ≥70, Level 8 is ≥40, and below 40 is Non-Compliant. **Verify** | `bbbee.py:28` |
| R4 | HIGH | B-BBEE QSE: code uses separate thresholds (90/80/70…) and a 25/25/25/25 element split. As I understand it, the Amended Codes QSE scorecard is Ownership 25, Management Control 15, Skills 25, ESD 30, SED 5, using the same level thresholds as Generic. **Verify** | `bbbee.py:33`, :142 |
| R5 | HIGH | B-BBEE EME: code gives Level 1 at ≥51% black-owned. As I understand it, 100% → Level 1, ≥51% → Level 2, otherwise Level 4. **Verify** | `bbbee.py:60` |
| R6 | HIGH | No priority-element subminimums or level discounting. Element scores are typed in, not calculated | `bbbee_save` |
| R7 | HIGH | The PDF is titled "BBBEE COMPLIANCE CERTIFICATE SUMMARY" and downloads as `BBBEE_Certificate.pdf`. It must not read as a certificate | `bbbee.py:224`, :1434 |
| R8 | HIGH | EE: any gender other than "Male" (including blank) is counted as Female | `gbs.py:40`, `app.py:855` |
| R9 | HIGH | EE: race list includes "Foreign National", conflating race and nationality. EEA2 reports foreign nationals separately from designated groups, so this needs a separate nationality flag | `RACES` |
| R10 | HIGH | EE: no employment dates in the population logic. Terminated employees and hires outside the period are counted or missed. EEA2 workforce-movement sections (recruitment, promotion, termination) cannot be produced | `_build_matrix` |
| R11 | HIGH | EEA4 annualises as `salary × 12`, assuming monthly salary with no part-year proration, and excludes variable pay | `gbs.py:243` |
| R12 | MEDIUM | EEA2 disability count is a single total, not broken down by level, race and gender | `gbs.py:178` |
| R13 | MEDIUM | EE 2025 amendments (effective 1 Jan 2025 as I understand it): designated-employer threshold is now 50+ employees with the turnover test removed, and sector numerical targets apply. The app has a free-text "designated employer" field and no sector or target support. **Verify** | `ee` |
| R14 | MEDIUM | Statutory due dates are seeded as fixed 2026/2027 dates. WSP (30 Jun 2026) and CIPC BO (30 Jul 2026) now show overdue with no way to roll to the next period | `init_db` :311 |
| R15 | MEDIUM | WSP/ATR "Employee Number" column prints the internal DB id, not `emp_number`. Reports include every record ever made, with no period filter | `wsp.py:91`, :81 |
| R16 | MEDIUM | `.xls` is an accepted upload type but openpyxl cannot read `.xls`, so every `.xls` import fails | :72, :719 |
| R17 | LOW | WSP Word export crashes if `skills_levy` holds non-numeric text (`:,.2f` on a string) | `wsp.py:202` |

### 3.5 Import

No validation of any field. Errors are reported only as a raw exception. Header aliasing is limited to
two spellings. The import is accidentally atomic (one commit at the end, and the connection closes
uncommitted on error), but this is not intentional and not reported as such. No duplicate detection
and no error report.

### 3.6 UX and operations

- The 1-minute inactivity logout (`IDLE_WARNING_AFTER=30`) will be disruptive for real HR work.
  This is a product decision; 15 minutes is typical.
- Bootstrap and the icons load from a CDN, so they fail on a local or offline network.
- No Dockerfile, no gunicorn, no health check, no backup procedure.

---

## 4. Stage 2 — proposed database design

### 4.1 Historical-integrity strategy (recommended)

**Immutable report versions with a frozen input snapshot.** When a report is generated for a
compliance record:

1. Build the input dataset (the relevant employees, filtered by employment dates for the period,
   plus training and earnings rows) as a canonical JSON document.
2. Store that JSON, the current company-profile version id and the rule-set id in `report_versions`,
   together with the generated file (saved under a random name outside `static/`) and its SHA-256.
3. Versions are append-only. Regenerating creates version N+1 with a mandatory reason.
4. "Open old report" serves the stored file. "Reproduce" re-runs the generator against the stored
   snapshot and checks the hash.

This fits SQLite, needs no effective-dated history on every table, and makes the John scenario hold
by construction. Employee field changes are additionally captured as before/after diffs in the audit
log.

### 4.2 Tables

New:

- `schema_migrations(version, applied_at)`: numbered migrations, with an automatic DB backup before each run.
- `company_profiles(id, …all company fields incl. PAYE/UIF/SDL/COIDA no., SETA, address, sector, financial year end, ownership…, created_at, created_by)`: append-only. The latest row is current.
- `rule_sets(id, module, code, version, regulator, source_document, source_url, effective_from, effective_to, last_reviewed, next_review, verified_by, verified_at, params_json, notes)`: thresholds, rates, ceilings and scorecard weights live here, not in code. `verified_at IS NULL` shows as "unverified" in the UI.
- `compliance_records(id, module, period_label, period_year, period_start, period_end, due_date, status, owner_id, reviewer_id, rule_set_id, created_at/by, updated_at/by, row_version)` with `UNIQUE(module, period_label)`. This replaces `deadlines`. CIPC BO is a record with no reports, so there is no separate workflow.
- `workflow_events(id, record_id, from_status, to_status, user_id, at, comment)`: the timeline.
- `report_versions(id, record_id, report_type, version, generated_at, generated_by, reason, snapshot_json, company_profile_id, rule_set_id, file_path, sha256, mime, reconciliation_json)` with `UNIQUE(record_id, report_type, version)`.
- `submissions(id, record_id, report_version_id, submitted_at, submitted_by, reference_no, confirmation_no, notes)`.
- `evidence_files(id, record_id, kind, original_name, stored_name, sha256, mime, size, uploaded_by, uploaded_at)`.
- `coida_earnings(id, record_id, employee_id, class_code, subclass, earnings)`: per employee, per assessment year.
- `bbbee_element_scores(id, record_id, element, max_points, achieved, subminimum_met, inputs_json)`.

Changed:

- `employees`: add `status` (ACTIVE/TERMINATED/ARCHIVED), `employment_type` (permanent/temporary), `is_foreign_national`, `termination_date`, `termination_reason`, `archived_at`, `occupational_category`, `updated_at/by`, `row_version`. Add `UNIQUE(emp_number)`. Move "Foreign National" out of race.
- `training_records`: add `record_id` (WSP/ATR period) and an `employee_number` snapshot.
- `users`: add `email`, `failed_logins`, `locked_until`, `session_version` (bumped on role change, delete or reset, which invalidates live sessions), `password_changed_at`. Soft-disable instead of delete when the user has history.
- `audit_log`: add `user_id`, `ip`, `entity`, `entity_id`, `before_json`, `after_json`. Add SQLite triggers that `RAISE(ABORT)` on UPDATE/DELETE.
- Checklists: scope becomes `record:<id>`. The existing `gbs:<id>` and `ee` scopes migrate. When a record is submitted, its checklist state is frozen into `workflow_events`, so a reset cannot erase it.
- Money is stored as integer cents in new tables. Existing REAL columns are converted at migration.

### 4.3 Workflow (enforced server-side)

`DRAFT → PREPARED → AWAITING_REVIEW → APPROVED → SUBMITTED → ARCHIVED`, plus
`AWAITING_REVIEW → REQUIRES_CHANGES → (edit) → AWAITING_REVIEW`.

One transition table in code defines allowed moves, the role allowed to make each move, and whether
the actor must differ from the preparer. Every state-changing route goes through a single
`transition(record, to_status)` function. Edits are refused unless the status is DRAFT,
PREPARED or REQUIRES_CHANGES. The move to APPROVED is refused if any mandatory reconciliation check
fails. Optimistic locking (`row_version`) prevents silent overwrites from simultaneous edits.

---

## 5. Order of work after approval

1. Stage 6 first items (S1–S4) are small and independent: secret enforcement, CSRF (Flask-WTF or a minimal token), seed-from-env, remove debug run. **Do before anything is deployed.**
2. Migrations framework, then Stage 3 schema.
3. Workflow, then versioning, reconciliation, dashboard.
4. A test suite (pytest) built alongside, starting with calculation tests using independent hand-computed expectations.
5. Dockerfile, gunicorn, volume, backup/restore scripts.

Testing stages 10–13 require Python 3.11 and Docker on a machine I can drive.
