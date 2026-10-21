# HR Compliance Suite — Pre-Production Security Assessment

**Target:** Dash BPO HR Compliance Suite (Flask + SQLite, server-rendered)
**Branch assessed:** `hardening` (commit at time of test: `390009c`)
**Date:** 2026-10-21
**Scope:** Authorized white-box review + live grey-box testing of the running demo
instance (`127.0.0.1:5000`, synthetic data). No production data exists yet.
**Method:** Full source review (`app.py`, `modules/`, `templates/`), dependency &
secrets scan, and a live authenticated probe harness that reproduced each boundary
with real HTTP requests (synthetic `admin` + `user` accounts; all test artefacts removed).

---

## A. Executive summary (plain language)

The application is **well-engineered for security**. The core boundaries that matter
for an HR system — logging in, keeping sessions honest, stopping forged requests,
keeping SQL safe, handling uploaded files, and enforcing the maker-checker approval
workflow — are implemented **on the server**, not just hidden in the UI, and they held
up under direct attack. There is **no SQL injection, no cross-site scripting, no
authentication bypass, no privilege escalation to administrator, no remote code
execution, and no exposed secrets.**

One real authorization defect was found and reproduced: a **normal (non-admin) user can
edit the organisation's statutory company profile** — the figures that feed B-BBEE,
COIDA and WSP calculations. It is a two-line fix and is bounded by versioning + an audit
trail, but it should be closed before real data is loaded.

Two important framing points, stated honestly rather than hidden:

1. **This is a single-organisation application, not a multi-tenant SaaS.** There is
   exactly one company (Dash BPO, hard-coded to MICT SETA). There is no tenant model,
   so the entire "cross-tenant isolation" battery — the generic brief's highest-priority
   area — **does not apply**: there is no second tenant whose data could leak. This is
   reported as **N/A**, not PASS, because the control simply does not exist (nor is it
   needed in this architecture).
2. **There is no AI/LLM, no external API client, no OAuth/JWT, and nothing is deployed
   yet.** Those sections of the brief are N/A or deferred to go-live, and are marked so.

**Verdict: GO WITH CONDITIONS.** No item on the "block production" list is present.
Fix the four pre-production conditions below, decide the role-model question, and this
is safe to carry real HR data.

### Remediation applied — 2026-10-21 (same day)

The code-owned conditions were fixed and verified immediately after the assessment:

- **F1 FIXED** — `/company` is now `@admin_required`; the duplicate link was removed from
  the Employees sub-nav (it remains under Administration). Re-tested live: a non-admin POST
  now returns `302 → /` with `saved_by_nonadmin=False`.
- **F2 FIXED** — `pip-audit` (run per F7) showed Werkzeug 3.0.6 was **still** vulnerable to
  newer 2026 advisories. Upgraded to **Flask 3.1.3 + Werkzeug 3.1.9**; `pip-audit` now
  reports *no known vulnerabilities*. (gunicorn 26.2.0 is clean.)
- **F4 FIXED** — CSP, Permissions-Policy added in `after_request`; HSTS emitted when the
  request is HTTPS. Confirmed live.
- **F5 (tests) DONE** — regression suite added at `tests/test_authz_regression.py`;
  full suite now **299 passing**.
- **F7 DONE** — `pip-audit` added to `requirements-dev.txt` for CI.

Still open (owner = Sydney): **F3** (role-model decision) and the deploy-layer items in
**F8** (Coolify HSTS/TLS, non-root container, env secrets, backup encryption).

---

## B. Critical findings (production blockers)

**None.** No confirmed cross-tenant access (no tenants), no auth bypass, no admin
privilege escalation, no RCE, no SQLi, no SSRF, no account takeover, no exposed secrets.

---

## C. High findings (remediate before production)

**None at HIGH.** The most serious confirmed issue (F1) is assessed MEDIUM because the
actor must already hold a valid provisioned account, the affected object is a
*configuration* record (not bulk PII), and the change is versioned + audited. It is
nonetheless a **required pre-production fix** (it can alter a compliance figure).

---

## D. Findings (detailed)

### F1 — Non-admin can modify the organisation's company profile  ·  MEDIUM  ·  CONFIRMED

- **Severity:** Medium (CVSS 4.0 ~5.0, AV:N/PR:L/UI:N, integrity of statutory config)
- **OWASP:** A01:2021/2025 Broken Access Control · **API Top 10:** API5 Broken Function-Level Authorization
- **CWE:** CWE-862 Missing Authorization (CWE-285)
- **Component / endpoint:** `GET,POST /company` → `company()` at `app.py:1129-1157`
- **Affected role:** any authenticated `user` (non-admin)
- **Description:** Every other organisation-wide governance surface is `@admin_required`
  (user management, rule-sets, trash, audit log, employee export/delete). The company
  profile editor is only `@login_required` (`app.py:1130`). The POST handler writes a new
  profile version from any submitted fields with no role check.
- **Security / business impact:** A non-admin can change the SETA, SDL number, ownership
  %, and annual turnover — inputs that drive the B-BBEE level and COIDA/WSP outputs. A
  malicious or careless normal user could shift a reported B-BBEE level.
- **Why it is bounded (not High):** profiles are append-only versioned; reports already
  generated keep their frozen input snapshot (`modules/reports_store.py`); every change is
  written to the immutable audit log.
- **Steps to reproduce (reproduced live):** as a `user`, `GET /company` for the CSRF
  token, then `POST /company` with `_csrf` + `ownership_notes=SECTEST-NONADMIN-WROTE-THIS`.
  Harness result: `post_status=302 loc=/company saved_by_nonadmin=True` (verified by
  reading the value back as admin; then reverted).
- **Expected:** non-admins receive the same redirect-to-dashboard block as other admin
  routes. **Actual:** the write succeeds.
- **Root cause:** decorator inconsistency — `@login_required` where `@admin_required` was intended.
- **Fix:** change the decorator on `company()` to `@admin_required` (keep `GET` viewable
  by all if desired by splitting read/write, but simplest correct fix is admin-only).
- **Long-term:** add a one-line authorization test asserting a `user` POST to `/company`
  is blocked (see Section K).
- **Regression test:** `test_nonadmin_cannot_edit_company_profile`.
- **Priority:** fix before production.

### F2 — Outdated Werkzeug with known CVEs  ·  MEDIUM

- **OWASP:** A06:2021 Vulnerable & Outdated Components / 2025 Software Supply Chain Failures · **CWE:** CWE-1104 / CWE-400
- **Component:** `requirements.txt:5` → `Werkzeug==3.0.3` (installed 3.0.3)
- **Description:** Werkzeug 3.0.3 predates the 3.0.6 security release:
  - **CVE-2024-49767** — resource exhaustion / DoS parsing `multipart/form-data`. Reachable
    via the evidence/import upload endpoints by any authenticated user.
  - **CVE-2024-49766** — `safe_join` / path handling weakness on Windows (the host OS here).
- **Impact:** authenticated DoS; Windows path edge-cases. Not RCE, not a blocker.
- **Fix (applied):** `pip-audit` showed 3.0.6 was still vulnerable to newer 2026 advisories,
  so upgraded to **Flask 3.1.3 + Werkzeug 3.1.9**; `pip-audit` now clean, 299 tests pass.
  gunicorn 26.2.0 has no known vulnerability.
- **Priority:** DONE (was: fix before production).

### F3 — Flat authorization model / least-privilege & data minimisation  ·  MEDIUM  ·  BY DESIGN — needs a decision

- **OWASP:** A01 Broken Access Control (design) · **CWE:** CWE-272 Least Privilege Violation
- **Description:** There are exactly two roles (`admin`, `user`, `app.py:3261`). Any `user`
  can read/write **all** employee PII (ID numbers, salaries, demographics), download **any**
  evidence/report/document by id, generate & download statutory reports containing aggregated
  PII, and edit all WSP/COIDA/B-BBEE/GBS tracker data. Object routes filter by primary key
  only — no per-record owner scoping (by design, one org). Admin-only surfaces are limited to
  user management, employee **bulk** export/delete, trash, audit, and rule-sets.
- **POPIA relevance:** this is a data-minimisation / access-control question. It is acceptable
  **only if every account holder is a trusted compliance/HR staff member.** If line managers,
  departmental users, or rank-and-file employees will ever get accounts, the single `user`
  tier is insufficient and a finer role model (e.g. read-only, department-scoped) is needed.
- **Decision required (owner):** *Who gets `user` accounts?* If only the HR/compliance team,
  document that acceptance. If broader, add role granularity before those users are onboarded.
- **Mitigating controls already present:** no self-registration (admin-provisioned only),
  bulk employee export is admin-only (no one-click mass exfil for a normal user), and every
  read of a report/evidence file is audited.

### F4 — Missing hardening headers (CSP, HSTS, Permissions-Policy)  ·  LOW

- **OWASP:** A05 Security Misconfiguration · **CWE:** CWE-693 Protection Mechanism Failure
- **Component:** `after_request` at `app.py:144-152`
- **Present:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin`, `Cache-Control: no-store` (logged-in) — all confirmed live.
- **Missing:** `Content-Security-Policy` (XSS defence-in-depth; low residual risk given solid
  autoescaping), `Strict-Transport-Security` (set at the Coolify/TLS proxy at go-live),
  `Permissions-Policy`.
- **Fix:** add a conservative CSP (`default-src 'self'`; the app vendors Bootstrap locally so
  no CDN exceptions are needed), HSTS at the proxy, and a minimal Permissions-Policy.

### F5 — No rate limiting beyond per-account login lockout  ·  LOW

- **OWASP:** API4 Unrestricted Resource Consumption · **CWE:** CWE-770
- **Description:** Login has a 5-attempt / 15-min per-account lockout (good, and
  enumeration-resistant via a dummy-hash timing path + generic error). But report/PDF/XLSX
  generation, exports, and imports have no throttle; an authenticated user could hammer
  expensive endpoints. There is also no IP-based login throttle across many usernames.
- **Impact:** authenticated resource exhaustion. Low for an internal, account-gated app.
- **Fix (go-live):** rate-limit at the reverse proxy, or add a lightweight per-user limiter
  on generation/export routes.

### F6 — `data/` runtime secrets not blanket git-ignored  ·  LOW / INFO

- **CWE:** CWE-312 Cleartext Storage · **Component:** `.gitignore:11-13`
- `.gitignore` excludes `*.db` and `uploads/`, but not the whole `data/` dir. `.secret_key`
  and `initial_admin_password.txt` are written into `DATA_DIR` and are **not currently
  tracked**, but a broad `data/` rule would prevent any future accidental commit.
- **Fix:** add `data/` to `.gitignore`.

### F7 — Dependency inventory verification  ·  INFO

- Install `pip-audit` and run it in CI at go-live; several installed versions are numbered
  ahead of known public releases and cannot be CVE-mapped from memory alone.

### F8 — Deployment-layer controls  ·  NOT TESTED (go-live)

- Nothing is deployed; the Dockerfile + Coolify plan was not live-tested. At go-live verify:
  HSTS + TLS redirect at the proxy, container runs non-root, secrets injected via env (not
  baked into the image), `HR_TRUST_PROXY=1` so client IPs are correct in the audit log,
  `HR_COOKIE_SECURE` left at default (on), and backup encryption/retention.

---

## E. OWASP Top 10 mapping

| Category | Result |
|---|---|
| A01 Broken Access Control | **F1 (Medium, confirmed)**, F3 (design) |
| A02 Cryptographic Failures | PASS — Werkzeug PBKDF2 hashing, `secrets` module, no custom crypto |
| A03 Injection | PASS — parameterised queries, whitelisted identifiers, LIKE-escaped, Jinja autoescape, no `render_template_string`, no `|safe` |
| A04 Insecure Design | PASS — maker-checker, immutable report snapshots, append-only audit |
| A05 Security Misconfiguration | F4 (headers); debug off, generic errors |
| A06 Vulnerable & Outdated Components / Supply Chain | **F2 (Werkzeug)**, F7 |
| A07 Identification & Authentication Failures | PASS — lockout, rotation, revocation, policy, forced change |
| A08 Software & Data Integrity Failures | PASS — pinned deps, SHA-256 on stored files, frozen report inputs |
| A09 Logging & Monitoring Failures | PASS (logging) / observation (no active alerting) |
| A10 SSRF | N/A — the app makes no outbound URL fetches |

## F. API security (OWASP API Top 10)

| | Result |
|---|---|
| API1 BOLA | N/A cross-org (single org); flat object access is F3 |
| API2 Broken Authentication | PASS |
| API3 Broken Object Property Level Auth | PASS — writes use fixed allowlisted column sets (`_emp_form_raw`), no mass assignment |
| API4 Unrestricted Resource Consumption | F5 |
| API5 Broken Function Level Authorization | **F1** |
| API6 Unrestricted Access to Sensitive Business Flows | PASS — workflow guarded server-side |
| API7 SSRF | N/A |
| API8 Security Misconfiguration | F4 |
| API9 Improper Inventory Management | PASS — small, fully enumerated route set; no legacy/undocumented endpoints |
| API10 Unsafe Consumption of APIs | N/A — no third-party API consumption |

## G. Tenant isolation report

**N/A — single-organisation application.** There is one `company_profiles` singleton
(latest-version read, `modules/company.py:42`), no `tenant_id`/`org_id` anywhere, and
all records key to `users(id)`, not to an organisation. There is no tenant boundary to
cross because there is exactly one tenant. READ/WRITE/DELETE/EXPORT/SEARCH/FILES/REPORTS/ADMIN
cross-tenant tests are therefore not applicable.

## H. Role matrix (Role → Action → Expected → Actual)

| Action | `user` expected | `user` actual | `admin` |
|---|---|---|---|
| View dashboard / records / employees | allow | allow ✓ | allow |
| Edit/terminate employees, upload evidence, edit trackers | allow | allow ✓ | allow |
| Prepare record; approve a record they prepared | allow / **deny** | deny ✓ (maker-checker) | override w/ reason |
| Approve as the assigned reviewer | allow | allow ✓ | allow |
| **Edit company profile** | **deny** | **allow ✗ (F1)** | allow |
| Create/reset/delete users, change roles | deny | deny ✓ (302→/) | allow |
| View audit log | deny | deny ✓ | allow |
| Bulk employee export / delete | deny | deny ✓ | allow |
| Empty/approve trash, edit rule-sets | deny | deny ✓ | allow |

## I. Attack chains

- **External, no account:** reaches only `/login`, `/home`, `/healthz`; everything else
  302→login. Login is lockout-protected + enumeration-resistant. **Dead end — no data.**
- **Ordinary user → admin:** blocked. Cannot create an admin (`evil_admin` not created in
  test), cannot toggle own role, cannot reset others. **No escalation path found.**
- **Ordinary user → tamper with a compliance figure:** F1 (edit company profile) — the one
  real chain, bounded by versioning + audit. **Fix F1 to close it.**

## J. The worst an ordinary user can realistically do

Assuming they hold a valid, admin-provisioned `user` account:

1. **Access:** all employee PII, all compliance records, all evidence/reports/documents,
   all trackers; can generate & download statutory reports; **can edit the company profile (F1).**
2. **Cannot access:** user management, audit log, bulk employee export/delete, trash, rule-sets.
3. **Cross tenants:** N/A (one org).
4. **Escalate to admin:** **No** (confirmed live).
5. **Steal HR data:** Within the app, yes — they can view/download all HR data (no bulk
   one-click export without admin, but data is viewable). This is the F3 design question.
6. **Compromise an admin account:** No — no reset without admin; resets bump `session_version`.
7. **Compromise infrastructure:** No app-level path (no RCE/SSRF/injection); infra untested.
8. **Persist:** No app mechanism (cannot mint accounts/keys); limited to their own account.
9. **Evade detection:** Partially — all actions are written to an append-only audit log they
   cannot alter (DB triggers), but there is no real-time alerting (F5/A09 observation).
10. **Must fix before prod:** F1, F2, F4; **decide F3**.

## K. Security regression suite (add to CI)

1. `test_nonadmin_cannot_edit_company_profile` — `user` POST `/company` ⇒ not applied (F1).
2. `test_nonadmin_blocked_from_admin_routes` — `/users*`, `/audit`, `/trash*`, employee
   export/delete ⇒ 302 away, no state change.
3. `test_csrf_required_on_state_change` — tokenless POST ⇒ 400.
4. `test_preparer_cannot_approve_own_record` — maker-checker (likely already covered).
5. `test_no_stacktrace_on_error` — 404/500 body contains no traceback/SQL/path.
6. `test_security_headers_present` — nosniff/frame/referrer on responses.
7. `test_secret_key_not_hardcoded_dev_value` — already present (`tests/test_security.py`).

## L. Production readiness

**GO WITH CONDITIONS.** Conditions, all small and none architectural:

1. **F1 — DONE** — `/company` is admin-only.
2. **F2 — DONE** — upgraded to Flask 3.1.3 + Werkzeug 3.1.9 (pip-audit clean).
3. **F4 — DONE (app side)** — CSP + Permissions-Policy added; HSTS still to be set at the Coolify proxy.
4. **F3 — OPEN (Sydney)** — decide and document the role model (who gets accounts).
5. At deploy (F8, OPEN): non-root container, secrets via env, `HR_TRUST_PROXY=1`, TLS redirect,
   backup encryption/retention.

---

## Scorecard

| Domain | Result |
|---|---|
| Authentication | PASS |
| Authorization | PASS WITH OBSERVATIONS (F1 confirmed; F3 design) |
| Tenant Isolation | N/A (single-org) |
| API Security | PASS WITH OBSERVATIONS (F1, F5) |
| Input Validation | PASS |
| Injection | PASS |
| Session Security | PASS |
| File Security | PASS |
| Data Protection | PASS WITH OBSERVATIONS (F3) |
| Privacy / POPIA | PASS WITH OBSERVATIONS (needs legal review; deletion/retention) |
| Cryptography | PASS |
| Logging | PASS |
| Monitoring / Alerting | PASS WITH OBSERVATIONS (no active alerting) |
| Error Handling | PASS |
| Infrastructure | NOT TESTED (not deployed) |
| Cloud Security | NOT TESTED (not deployed) |
| Dependency Security | PASS (F2 fixed: Flask 3.1.3 + Werkzeug 3.1.9, pip-audit clean) |
| CI/CD | NOT TESTED |
| AI Security | N/A (no AI/LLM) |
| Business Logic | PASS |
| Availability | PASS WITH OBSERVATIONS (F5) |
| Backup / Recovery | NOT TESTED (go-live) |

---

### What was NOT tested, and why
Multi-tenant isolation (no tenants), AI/LLM & prompt-injection (no AI), OAuth/OIDC/JWT
(session-cookie auth only), CORS (no cross-origin API; same-origin only), GraphQL/WebSockets
(none), mobile/PWA (server-rendered only), and all infrastructure/container/CI-CD/cloud/backup
controls (nothing is deployed yet — review at go-live). These are marked N/A or NOT TESTED
above rather than silently passed.
