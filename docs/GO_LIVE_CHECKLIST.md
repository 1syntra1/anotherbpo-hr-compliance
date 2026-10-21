# Go-Live Checklist — HR Compliance Suite

Deployment target: **Coolify** (Docker, self-hosted). This list covers the security and
operational steps to move from the POC/demo to a live instance holding real HR data. It
pairs with `docs/SECURITY_ASSESSMENT.md` (the pre-production assessment).

The container image is already hardened: runs as a **non-root** user (uid 10001, `nologin`),
app code owned by root (not writable by the runtime user), `/data` the only writable path,
`HR_TRUST_PROXY=1` baked in, gunicorn single-worker (SQLite-safe) with request-size limits
and access logs that **omit query strings**. The remaining work is configuration + data.

## 1. Environment variables (set in Coolify)

| Variable | Required | Purpose |
|---|---|---|
| `HR_AUDIT_SECRET` | Recommended | Flask session signing key (≥32 chars). If unset, a random key is generated and persisted to `/data/.secret_key` on the volume. Changing it signs everyone out. |
| `HR_ADMIN_PASSWORD` | Recommended at first boot | Initial admin password. If unset, one is generated and written to `/data/initial_admin_password.txt`. Either way the admin must change it at first sign-in. |
| `HR_COOKIE_SECURE` | Leave default (on) | Keep unset / `1` in production so session cookies are HTTPS-only. Never set `0` in prod. |
| `HR_BACKUP_PASSPHRASE` | **Yes** | Encrypts backups at rest (they contain employee PII). Store it with your recovery credentials — **a lost passphrase makes backups unrecoverable.** |
| `FORWARDED_ALLOW_IPS` | Optional | Defaults to `*` (fine while only Coolify's proxy can reach the container). If the container port is ever exposed directly, set to the proxy IP/CIDR so clients cannot spoof scheme/client-IP. |
| `GUNICORN_THREADS`, `LOG_LEVEL`, `PORT` | Optional | Tuning. |

## 2. TLS / HSTS

- [ ] Terminate TLS at the Coolify proxy; force HTTP→HTTPS redirect.
- [ ] Confirm HSTS is served: `curl -sI https://<host>/ | grep -i strict-transport`. The app
      emits HSTS automatically once requests arrive over HTTPS (via `HR_TRUST_PROXY`/ProxyFix).
- [ ] Confirm the security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`,
      `Referrer-Policy`, `Content-Security-Policy`, `Permissions-Policy`.

## 3. First admin & demo data (do before anyone signs in)

- [ ] Start the instance with a strong `HR_ADMIN_PASSWORD` (or collect the generated one from
      `/data/initial_admin_password.txt`), sign in, change the password, then **delete**
      `initial_admin_password.txt` from the volume.
- [ ] **Clear demo/POC data.** The demo database and any seeded sample records/users must not
      carry into production. Start from a fresh `/data` (empty volume) so only the migrations
      + the real first admin exist, then load real data. Do not reuse the POC `anotherbpo2024`
      credential anywhere.
- [ ] Create the ≤10 real user accounts (one admin + the rest `user`). Each is forced to set
      their own password at first sign-in.

## 4. Backups & recovery

- [ ] Set `HR_BACKUP_PASSPHRASE` and schedule `python scripts/backup.py` (daily is typical).
      Encrypted archives are written to `/data/backups/hrcs-backup-*.tar.gz.enc`.
- [ ] Copy backups **off the host** (the `/data` volume alone is not a backup).
- [ ] Set retention with `--keep N` (default 14).
- [ ] **Test a restore** into a scratch dir before you rely on it:
      `HR_DATA_DIR=/tmp/r python scripts/restore.py <archive> --passphrase "$HR_BACKUP_PASSPHRASE"`.

## 5. Dependencies & CI

- [ ] Keep `pip-audit` in CI (`pip-audit -r requirements.txt`); the build should fail on a
      known vulnerability. Current status: **clean** (Flask 3.1.3, Werkzeug 3.1.9).

## 6. Post-deploy smoke test

- [ ] `GET /healthz` returns 200.
- [ ] Sign in as admin; confirm a normal `user` cannot reach `/users`, `/audit`, `/trash`,
      `/company` (they redirect to the dashboard).
- [ ] Generate one report and download it; upload one evidence file.

## 7. POPIA / privacy (needs a privacy/legal reviewer)

- [ ] Confirm data-retention and employee-record deletion processes.
- [ ] Confirm who holds `user` accounts (decided: one admin + ≤10 trusted compliance staff —
      all legitimately need the data; revisit if accounts ever widen).
- [ ] Record that backups contain special-category personal information and are encrypted at rest.

---

*Security architecture reviewed 2026-10-21 — verdict GO WITH CONDITIONS; the code-side
conditions (F1, F2, F4, F5, F7) are fixed. This checklist closes the deploy-side items (F8).*
