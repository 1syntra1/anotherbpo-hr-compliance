# Architecture

Flask (single process, threaded gunicorn) + SQLite + files on one volume. Deliberately small:
an internal system for one company, tens of users, up to ~10,000 employees.

## Data model

```
company_profiles (append-only versions)            rule_sets (versioned, content immutable)
        │                                                   │
employees ──┐                                              │
training_records ──┐                                       │
coida_earnings / coida_rates / bbbee_inputs ──┐            │
                                              ▼            ▼
                compliance_records  (one per module + reporting period; UNIQUE while not deleted)
                 │  status, owner, reviewer, due date, row_version (optimistic locking)
                 ├── workflow_events   (append-only timeline; every transition, report, evidence, reset)
                 ├── report_versions   (append-only; snapshot JSON + file + hashes + checks)
                 ├── submissions       (append-only; date, reference, confirmation, versions submitted)
                 ├── evidence_files    (append-only; stored under random names, SHA-256)
                 └── checklist_state / custom_tasks / hidden_tasks  (scope "record:<id>")
audit_log (append-only: user, IP, entity, before/after JSON)
```

Append-only tables are enforced by SQLite triggers (`RAISE(ABORT)` on UPDATE/DELETE), so the
application itself cannot rewrite history. Schema changes go through numbered migrations
(`modules/migrations.py`), each in one transaction, with a database backup before any run.

## Historical integrity (how an old report stays the same)

Chosen approach: **immutable report versions with a frozen input snapshot.**

When a report is generated for a compliance record (`modules/reports_store.py`):

1. All inputs are copied into a canonical JSON snapshot: the relevant employee fields
   (EE: no names or ID numbers, employee numbers only), training rows, COIDA earnings and
   rates, B-BBEE inputs, the current company-profile version and the rule set in force for
   the period.
2. The module's checks run on the snapshot (EE reconciliation, COIDA totals, etc.).
3. The file is rendered **only from the snapshot** (renderers never touch the database).
4. File, snapshot, both SHA-256 fingerprints, company-profile id, rule-set id, checks, user and
   reason are stored as version N. Version N+1 needs a reason.

Consequences:
- Changing, terminating, archiving or even deleting an employee, editing the company profile, or
  adding a new rule-set version cannot change a stored report.
- **Download** serves the stored bytes after verifying their hash (tamper/corruption detection).
- **Verify** re-renders the version from its stored snapshot and compares the content with the
  stored file — the "can we reproduce the historical report?" check.
- A submission records exactly which version ids/hashes were submitted.

Alternatives rejected: effective-dated history on every table (large rewrite, every query
becomes temporal); storing only files (not reproducible or explainable).

## Workflow

`modules/records.py`: one transition table (allowed from-states, target, who may act, reason
required). Every route goes through `transition()`; status is never set directly.

```
DRAFT → PREPARED → AWAITING_REVIEW → APPROVED → SUBMITTED → ARCHIVED
                        │   ▲             │          │
                        ▼   │ resubmit    │ withdraw │ reopen (admin, reason)
                    REQUIRES_CHANGES ◄────┴──────────┘
```

Rules enforced server-side: reviewer must be assigned before review; only the reviewer or an
admin approves; the preparer cannot approve their own record (an admin may, with a recorded
override reason); prepare/approve/submit are refused while any latest report's checks fail;
data, report generation and checklists are locked outside DRAFT/PREPARED/REQUIRES_CHANGES (checklist
frozen at submission); stale forms are refused via `row_version`.

## Regulatory rules

`rule_sets` holds every rate, threshold, weight and default deadline with regulator, source,
effective date, review dates and verifier. Content is immutable; a regulation change is a new
version with a new `effective_from`, selected by period date. Seeded values are marked
**unverified** until a named person verifies them. The UI flags unverified rules on the
dashboard, the record page and in every generated report.

## Security model

Session cookie (HttpOnly, SameSite=Lax, Secure) carries only the user id and a
`session_version`; the user row is re-read on every request, so role changes, disabling,
deletion and password resets take effect immediately. CSRF token on every POST. Lockout after 5
failed sign-ins. Generic error pages; details only in server logs. Uploaded files are stored
under random names outside the web root, type-checked by content, downloaded only as
attachments by signed-in users. All SQL is parameterised. Generated spreadsheets contain no
formulas (totals computed in Python); any formula-typed cell is converted to text.

All signed-in users can see all compliance records: it is a single-company system and records
are shared work. Administration (users, audit log, trash, permanent deletion, rule verification,
data export) is admin-only.
