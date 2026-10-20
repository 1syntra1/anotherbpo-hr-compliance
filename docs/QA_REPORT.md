# HR Compliance Suite — QA Report

Date: 12 October 2026 · Branch: `hardening` · Assessor: Claude (developer + QA)

**Final status: BETA.** The system is now a compliance record system with enforced workflow,
reproducible stored reports, evidence and an immutable audit trail, and it passed every test
that was run. It is **not** Production Ready because HIGH items remain open — chiefly that the
regulatory rules have not been verified by a qualified person, and some EE form sections the
brief asks for are not built yet (see §30, §33).

## What was actually executed

| Suite | Checks | Result | How |
|---|---|---|---|
| pytest (`tests/`) | 272 | 272 PASS | `pytest -q tests` on Python 3.11.9, Windows 11. Catalogue: `docs/TEST_RESULTS.csv` |
| Container (`tests/docker_e2e.py`) | 17 | 17 PASS | Docker 29.8.2: build, non-root, restart, replace, backup, restore, POC-database upgrade |
| Browser (built-in pane) | manual | see §25 | Dashboard, EE, record page, checklist AJAX, report generation on synthetic data |

**Not executed:** Firefox / Edge / Safari / real phones; screen-reader testing; a real 10–50 user
load test against gunicorn (load was simulated in-process); the Coolify deployment itself (no
Coolify server yet); verification of any regulation against official sources. Nothing in this
report claims those passed.

All test data is synthetic. No real employee information was used.

---

## 1. Executive summary
Built on the existing app (not rewritten): versioned migrations; reporting periods as compliance
records with a server-enforced review/approval workflow; immutable report versions with frozen
input snapshots; submission + evidence records; a versioned regulatory rules register; employee
lifecycle; transactional validated import; compliance calendar/health/notifications; and a full
security pass (CSRF, session revocation, lockout, secret handling, upload hardening, formula
injection). Calculation errors found in Stage 1 were fixed (B-BBEE levels, QSE/EME rules, EE
gender/foreign-national handling, EEA4 pro-rating, fabricated COIDA rates removed).

## 2. Architecture review
See `docs/ARCHITECTURE.md`. Flask + SQLite kept as required. New logic lives in `modules/`
(records, reports_store, ee, coida, bbbee, wsp, employees, company, rules, migrations); routes stay
in `app.py`, matching the existing style. Renderers are pure functions of a snapshot.

## 3. Database review
Foreign keys now enforced (`PRAGMA foreign_keys=ON`); WAL mode; busy timeout. Unique: employee
number (partial index), one live record per module+period, report version per record+type.
CHECK constraints on status, COIDA earnings ≥ 0, rates ≥ 0. Append-only triggers on audit_log,
workflow_events, report_versions, submissions, evidence_files, company_profiles; rule-set content
frozen. Indexes on audit time/entity, employee status/name, record due date. Migration v3 tested
on the real POC database: integrity_check ok, foreign_key_check clean, backup written first.
Legacy duplicate employee numbers are renamed `-DUP-<id>` and audited (none in the POC data).

## 4. Security review
Fixed: hard-coded secret (S1), known seed passwords (S2), no CSRF (S3), debug server on LAN (S4),
stale role in cookie (S5), missing server-side authorisation (S6), unvalidated checklist scopes
(S7), formula injection (S8), unlimited/unsafe uploads (S9), no lockout (S10), cookie flags (S11),
error detail leakage (S12), no password policy (S13), replayable GBS submit (S14), GET logout (S15),
login timing enumeration (S16), open redirect via Referer (found in Stage 11). Security headers
(nosniff, DENY framing, same-origin referrer, no-store). 

## 5. Privacy review
See `docs/PRIVACY.md`. Data minimisation in EE snapshots; admin-only per-employee export for
access requests; archive-before-delete; evidence warning; access log without query strings.
Open: encryption at rest depends on server disk encryption (M1).

## 6. Authentication review
Passwords hashed (Werkzeug scrypt). 12-character policy, common-password and username checks.
Forced change on first sign-in and after admin reset. Lockout 5 failures / 15 min. Session cleared
and reissued on sign-in. 15-minute idle sign-out with 1-minute warning; server backstop at 16 min.
"Stay logged in" = 30 days (L3). Logout is POST. Tested: success, failure, lockout, disabled user,
forced change, logout, back-button after logout, inactivity expiry, forged role in cookie, demoted /
deleted user with live session.

## 7. Authorization review
Every admin route uses `admin_required`, which reads the role from the database on each request.
Standard users blocked (tested) from users, audit, trash, all admin POSTs, permanent employee
deletion, rule verification, data export. Workflow permissions (reviewer/admin/preparer separation)
enforced in `transition()`. Cannot self-demote, self-disable, self-delete or remove the last active
admin.

## 8. Employee register review
Lifecycle ACTIVE → TERMINATED → ARCHIVED (+ reactivate). Permanent delete: admin, archived only,
typed confirmation, refused while referenced. Search, status/level filters, sort, pagination (50).
Validation of every field incl. SA ID Luhn/date check, passport for foreign nationals, dates
(no future, no end-before-start), salary bounds, Unicode/apostrophes/hyphens, long names, formula
and markup characters. Optimistic locking on edits. Field-level before/after in the audit log.

## 9. Import / export review
All-or-nothing: every row validated before any write; one bad row → nothing imported, with a
per-row problem list and downloadable CSV report (token-bound to the uploader's session). Tested:
valid CSV/XLSX, empty, header-only, corrupt XLSX, non-zip XLSX, .xls/.exe, binary renamed .csv,
missing/extra columns, duplicates in file and in register, blank rows, invalid/future dates,
invalid numbers, negative/huge salaries, Unicode, quotes and commas, formula/CSV injection, XSS
payloads, 5,000-row file (< 30 s ceiling; actual well under), row cap, simultaneous overlapping
imports (exactly one succeeds).

## 10. Employment Equity review
Population = employed on the period's last day; recruitment/terminations by dates in period;
foreign nationals in separate columns; temporary employees separate; disability by level/group;
EEA4 remuneration pro-rated by days. Validation excludes and lists employees with missing EE
attributes, which fails reconciliation (blocks approval). Reconciliation: totals, per level, per
group, remuneration, disability, exclusions — tested including a deliberately tampered EEA4.
Generated EEA2/EEA4 inspected programmatically at 0, 1, 10, 100, 1,000 employees: sheet names, no
formulas, no error values, totals equal independent counts. **Open:** see H2.

## 11. GBS review
Each claim is a compliance record (one per quarter, enforced). Create, edit (validated), trash
(only while editable), admin restore/approve purge/cancel, workflow submit/reopen through the
record. Year rollover (Q4 → Q1) tested. Purge removes the project data but keeps the record's
history; never purges claims with reports, submissions or evidence. Old submit/reopen URLs no
longer bypass approval (tested).

## 12. WSP / ATR review
Training belongs to a reporting period; employee number and name copied at capture (history kept
after rename/archive — tested). Category, skills gap, dates, cost validation; formula input
refused. WSP/ATR Excel and Word generated from snapshots; Word structure inspected.

## 13. B-BBEE review
Engine with versioned scorecards (Generic, QSE, EME): element caps and bonus caps, priority
sub-minimums, one-level discounting (Level 8 → Non-compliant), QSE "ownership + one" rule,
automatic levels for black-owned EMEs/QSEs, turnover classification check. Boundary values for
every level tested independently. Output renamed "Internal B-BBEE Calculation" and states it is not
a certificate (PDF text and metadata inspected). **Limitation:** element points are entered, not
derived from raw indicator data (M3b).

## 14. COIDA review
Data model Employee → assessment period → class → earnings. Earnings built from the register
(monthly salary × months in period, pro-rated) and adjustable per employee with audit. Rates are
entered per period from the employer's assessment notice (the fabricated default rates were
removed). Earnings capped at the period's maximum from the rules register; missing rate or missing
maximum fails the checks. Formula `capped earnings / 100 × rate` tested against hand calculations,
including cap, decimals, zero, multiple classes, part-year and skipped employees; W.As.8 output
inspected.

## 15. Checklist review
Checklists for EE, GBS, WSP, COIDA, B-BBEE, CIPC BO; per-record scope; add/complete/uncomplete/
hide/restore/reset with audit. Scopes validated server-side. Frozen at submission (409 on change,
tested). Reset stores the previous state in the record timeline. **Gap:** per-task owner/notes/due
date are not editable per task (L5).

## 16. Audit-log review
Append-only at database level (UPDATE/DELETE abort — tested). Records user id, role, IP, entity,
entity id, before/after JSON for employees, company, users, rules, workflow, reports, downloads,
imports, exports, checklist changes, refused workflow actions and blocked admin access. Audit
writes now share the transaction of the change they describe. **Limit:** someone with direct file
access can drop triggers; no hash chain (M3).

## 17. Report-generation review
Every report version stores type, period, version, time, user, reason, snapshot + SHA-256,
company-profile version, rule set, file SHA-256, checks. Download verifies the hash (tamper test
passes); "Verify" re-renders from the snapshot and compares content. Concurrent generation yields
contiguous versions with no overwrite (tested).

## 18. Historical-data review
The required John scenario passes: after changing John's salary, department, level, race and
gender, terminating and archiving him, the old EEA2/EEA4 downloads are byte-identical, re-render
identically from their snapshot, and the new version reflects the change. Company-profile edits
also leave old reports unchanged (tested).

## 19. Approval workflow review
States and transitions per the brief (REJECTED = "Request changes" → REQUIRES_CHANGES →
"Resubmit"). Tested bypass attempts: forged actions/status fields, replayed submit, wrong user
approving, preparer approving, admin self-approval without reason, stale row_version.

## 20. Submission-evidence review
Submission captures date (not future), reference (required), confirmation, submitter, notes and
the exact report versions/hashes; checklist state frozen into the timeline. Evidence: pdf, png,
jpg, xlsx, docx, csv, txt, eml, content-checked by magic bytes; random stored names; append-only;
download as attachment only. Rejected (tested): renamed .exe, double extension, php/html, macro
workbook, empty, no extension, oversized (413), traversal names.

## 21. Compliance-calendar review
Dashboard: health % (completed / due-or-done), counts, per-module status, calendar ordered by
urgency, owner/reviewer, notifications (overdue, due ≤30 days, awaiting your review, changes
needed, failed checks). Status shown with text and icons, not colour alone. Deadlines are
per-record and editable; defaults come from the rules register. CIPC BO stays a deadline/status
record with a checklist and evidence — no separate workflow.

## 22. Regulatory-rule review
`rule_sets`: regulator, source document, URL, effective dates, review dates, verifier, parameters;
content immutable; new versions by effective date; unverified flagged everywhere and in report
headers. **All six seeded rule sets are UNVERIFIED** (H1). Statements in code/docs about the
2025 EE amendments, Amended Codes thresholds and COIDA maximum reflect the developer's
understanding only.

## 23. Backup/recovery review
`scripts/backup.py` (online SQLite backup, integrity check, all files, SHA-256 manifest, atomic
write, retention; excludes secrets) and `scripts/restore.py` (verifies everything before touching
data, moves current data aside, refuses unsafe archive paths). Tested in the container: backup,
add data, restore → backed-up state returns. Off-server copy and scheduling are deployment tasks
(H4).

## 24. Docker/Coolify review
`python:3.11.9-slim`, non-root uid 10001, code not writable, single `/data` volume, healthcheck,
gunicorn gthread (1 worker — required for SQLite and start-up migrations), ProxyFix, access log
without query strings. Tested: clean build, start, restart, replacement (redeploy), session and
data survival, backup/restore, POC upgrade, no passwords in logs. Not tested: Coolify itself,
HTTPS termination (Coolify's proxy), image rollback. Guide: `DEPLOY_COOLIFY.md`.

## 25. UI/UX review
Existing look kept; new pages: compliance records, record detail (workflow, reports, submission,
evidence, checklist, timeline), company profile, rules register, change password, error page.
Breadcrumbs, empty states, confirmation on destructive actions, disabled actions show the reason.
Bootstrap/icons served locally (works offline). Browser check (built-in pane): dashboard, EE page,
record page render; checklist tick saves via AJAX with CSRF (200); report generation works; no
console errors. At a 375 px emulated width no element overflows the layout viewport, but the
pane's scaled screenshot was inconclusive — check on a real phone (M7).

## 26. Accessibility review
Added: labels for new form fields, `aria-label` on icon buttons, captions/`scope` on new tables,
status icons alongside colour, `aria-hidden` on decorative icons, focusable tooltip wrappers for
disabled actions. Not done: screen-reader pass, contrast audit, older templates' labels (M7).

## 27. Performance review
In-process timings with 10,000 employees: dashboard 0.01 s, employee page 0.03 s, search 0.01 s,
EE preview 0.14 s, EEA2 generation 0.38 s. Import of 5,000 rows well under the 30 s ceiling.
No N+1 found in hot paths; GBS list computes checklist progress per project (fine at expected
volumes). No optimisation beyond indexes was justified.

## 28. Adversarial security testing (executed as tests)
| Attacker | Attempts | Outcome |
|---|---|---|
| Unauthenticated | every page/admin URL, POST without/with forged CSRF, unknown ids | Blocked (302 to login / 400 / 404) |
| Standard user → admin | admin pages/actions, forged role in cookie, approve own work, reopen, restore trash, delete employee, export | Blocked server-side |
| Other records / IDs | non-existent report/evidence/record ids, forged checklist scopes, ids in hidden fields | 404 / 400; module ids validated |
| Modify submitted records | regenerate, checklist toggle, replay submit, edit GBS claim | Refused (409 / message) |
| Bypass approval | forged action/status, old GBS submit URL | Refused |
| Alter audit history | UPDATE/DELETE on audit_log, events, versions | Aborted by triggers |
| Malicious files | exe renamed, double extension, php/html, xlsm, oversized, corrupt xlsx | Rejected |
| SQL injection | search, sort parameters | Inert (parameterised; sort whitelisted) |
| XSS | names in register, usernames, task text | Escaped / rejected |
| Path traversal | `../../etc/passwd.pdf`, `..\..\windows\win.ini.pdf`, import report token | Neutralised / 404 |
| Open redirect | hostile Referer on checklist reset | Fixed (was possible), regression test added |
| Formula injection | names/providers starting `=`/`@`, formula cells | Refused at input; neutralised in output |

**Successful attacks:** one — the open redirect via `Referer`, found and fixed in this pass. None
remaining in the tested set.

## 29. Critical defects
None open. (Stage 1 criticals D1–D3, S1–S3, R1 fixed and covered by tests.)

## 30. High defects (open)
| # | Item | Impact | Owner |
|---|---|---|---|
| H1 | All rule sets unverified (EE, B-BBEE ×3, COIDA maximum, default deadlines) | Reports could be filed with wrong thresholds / caps / dates | Named compliance reviewer |
| H2 | EE module lacks EE plan, numerical goals/targets, sector targets, barriers, promotions, skills-development and disciplinary sections; EEA2/EEA4 are working copies, not the official form layout | Not a complete EE submission pack | Development (next stage) |
| H4 | Off-server backups not configured yet | One disk failure loses all HR records | Sydney / server admin at deployment |
| H5 | Public GitHub history still contains the old demo password and fallback key | Anyone can read them; harmless only once the repo is deleted and no deployment uses them | Sydney |

## 31. Medium defects (open)
M1 ID numbers/salaries not encrypted at rest (rely on volume encryption). M2 No MFA. M3 Audit
immutability is trigger-based (no hash chain). M3b B-BBEE points entered per element, not computed
from raw indicators. M4 Employee history is via audit diffs, not effective-dated records.
M5 Money stored as REAL (all calculations use Decimal). M6 Notifications are in-app only (no
email). M7 Cross-browser, real-phone and screen-reader testing not done; older templates need an
accessibility pass. M8 Public landing page still shows fixed due dates in marketing copy.

## 32. Low defects (open)
L1 Dashboard module cards still carry some descriptive copy from the POC. L2 Employee export
matches snapshots by id pattern and may over-include. L3 "Stay logged in" lasts 30 days. L4 Local
dev server logs query strings. L5 Checklist tasks have no per-task owner/notes/due date editing.

## 33. Missing functionality
EE plan/targets/barriers/promotions/skills/disciplinary capture (H2); raw-indicator B-BBEE
sub-scorecards (M3b); email notifications (M6); per-task checklist metadata (L5); EEA/WSP output
in the regulators' exact upload formats.

## 34. Recommended improvements
1. Verify the rule sets (H1) — highest value, no code needed.
2. Build the EE plan/targets module (H2) on top of the record model.
3. Deploy to Coolify with off-server backups; run a restore drill.
4. Add MFA (TOTP) for administrators.
5. Accessibility and cross-browser pass on all templates.

## 35. Production readiness
**BETA.** Ready for internal use on synthetic or real data in a controlled pilot once deployed
with backups, with reports treated as working copies. Not Production Ready while H1, H2, H4 and H5
are open.
