# Deploy — HR Compliance Suite on Coolify (self-hosted)

Everything runs on your own server: a git server (Forgejo recommended, or GitLab) holds the
code, Coolify builds the Docker image from it and runs one container, and all data lives
on one persistent volume.

```
Forgejo / GitLab  ──(webhook / deploy key)──>  Coolify  ──>  container "hrcs"  ──>  volume  /data
                                                   │                                 ├── hr_audit.db
                                                   └── Caddy/Traefik (HTTPS)         ├── reports/   (stored report versions)
                                                                                     ├── evidence/  (uploaded evidence)
                                                                                     ├── uploads/, backups/
                                                                                     └── .secret_key
```

---

## 1. Move the code to the local git server

- [ ] 1.1 In Coolify: **Services → New → Forgejo** (or install GitLab separately). Create an
  organisation and an empty **private** repository, e.g. `hr/hr-compliance`.
- [ ] 1.2 From this PC, in the project folder:
  ```
  git remote rename origin github
  git remote add origin http://<git-server>/hr/hr-compliance.git
  git push --mirror origin
  ```
- [ ] 1.3 Check the repository on the git server shows every branch and the history.
- [ ] 1.4 **Delete the GitHub repository** (`1syntra1/anotherbpo-hr-compliance`). Its history contains
  the old demo password and fallback key. Then `git remote remove github`.

## 2. Create the application in Coolify

- [ ] 2.1 **Sources**: add the git server (Forgejo: "Private repository (with deploy key)"; add the key
  Coolify shows as a read-only deploy key on the repository).
- [ ] 2.2 **New resource → Application → your repository**, branch `main`, **Build pack: Dockerfile**.
- [ ] 2.3 **Ports exposes:** `8000`. Health check path: `/healthz`.
- [ ] 2.4 **Domain:** e.g. `https://hr.yourdomain.co.za` (public DNS → this server) or an internal name.
  Coolify issues the HTTPS certificate. Office-only? Keep it internal and give remote staff a VPN
  (Tailscale / WireGuard) instead of exposing it to the internet.

## 3. Persistent storage — do not skip

- [ ] 3.1 **Storages → Add → Volume mount.** Name `hrcs-data`, destination path **`/data`**.
- [ ] 3.2 Only one replica / instance. SQLite allows one writer; never scale this app horizontally.

Without the volume every redeploy starts with an empty database.

## 4. Environment variables

| Variable | Value | Notes |
|---|---|---|
| `HR_AUDIT_SECRET` | 64 random characters | Optional. If unset, a key is generated once into `/data/.secret_key`. If set, at least 32 characters. Keep it out of git. |
| `HR_ADMIN_USERNAME` | `admin` (default) | First run only. |
| `HR_ADMIN_PASSWORD` | a strong temporary password | First run only. If unset, one is generated into `/data/initial_admin_password.txt`. Either way it must be changed at first sign-in. |
| `HR_COOKIE_SECURE` | leave unset (= on) | Set `0` **only** if you deliberately serve plain HTTP on a closed network. |
| `HR_TRUST_PROXY` | `1` (set in the image) | Correct client IPs in the audit log behind Coolify's proxy. |
| `HR_MAX_UPLOAD_MB` | `10` (default) | Upload limit for imports and evidence. |
| `GUNICORN_THREADS` | `8` (default) | |

Generate a secret on any machine with Python: `python -c "import secrets;print(secrets.token_hex(32))"`.

## 5. First deploy

- [ ] 5.1 **Deploy.** Watch the log for `Booting worker` and a green health check.
- [ ] 5.2 If you did not set `HR_ADMIN_PASSWORD`: Coolify → the app → **Terminal** →
  `cat /data/initial_admin_password.txt`.
- [ ] 5.3 Sign in, choose a new password (forced), then **delete the file**:
  `rm /data/initial_admin_password.txt`.
- [ ] 5.4 Users → create the real accounts (each gets a temporary password and must change it).
- [ ] 5.5 Company Profile → complete every field.
- [ ] 5.6 Rules Register → have the named reviewer verify each rule set (see §8).

### Bringing the POC database across (optional)

The app upgrades older databases automatically and writes `backups/hr_audit-pre-migration-*.db`
first. To carry the POC data over: stop the app in Coolify, copy the old `hr_audit.db` into the volume
(Coolify terminal / `docker cp`), make it owned by uid 10001 (`chown 10001:10001 /data/hr_audit.db`),
start the app. Demo users and demo data should be cleared before go-live rather than migrated.

## 6. Backups

The volume is the whole system. Backups that live only on the same disk are not backups.

- [ ] 6.1 Coolify → the app → **Scheduled tasks** → add, daily at 01:00:
  `python scripts/backup.py --keep 14`
  This writes `/data/backups/hrcs-backup-YYYYmmdd-HHMMSS.tar.gz` containing a consistent database
  snapshot (SQLite online backup + integrity check), every stored report and evidence file, and a
  SHA-256 manifest. Incomplete runs never leave a file that looks finished.
- [ ] 6.2 Copy the archives **off the server** every day: a cron job on the host
  (`rsync /var/lib/docker/volumes/<volume>/_data/backups/ nas:/hrcs-backups/`) or Coolify's
  S3-compatible backup target pointed at a NAS / MinIO you control.
- [ ] 6.3 The secret key is not in backups by default (sessions simply end after a restore).
  Store `HR_AUDIT_SECRET` in your password manager if you set it.
- [ ] 6.4 Test a restore every quarter (§7) on a spare machine.

## 7. Restore

1. Coolify → **Stop** the app.
2. Put the archive in the volume (e.g. `/data/backups/`).
3. Run a one-off container on the same volume:
   `docker run --rm -v <volume>:/data <image> python scripts/restore.py /data/backups/hrcs-backup-....tar.gz --yes`
4. The script verifies every file against the manifest and checks the database **before** touching
   anything. Current data is moved to `/data/backups/pre-restore-<time>/`, never deleted.
5. Start the app. Sign in and spot-check a stored report with **Verify** on its record.

## 8. Before go-live (one-time checklist)

- [ ] GitHub repository deleted; code only on the local git server.
- [ ] Demo / POC accounts removed or disabled; every user has their own account.
- [ ] Demo / POC employee and compliance data cleared (fresh volume, or delete before use).
- [ ] Rules Register: EE, B-BBEE (Generic/QSE/EME), COIDA maximum earnings and the default deadlines
      verified against official sources by a named person, with next-review dates.
- [ ] COIDA class rates entered per period from the employer's own assessment notice.
- [ ] Daily backup task running and copies landing off-server; one restore tested.
- [ ] HTTPS working; `HR_COOKIE_SECURE` not disabled.
- [ ] A POPIA information officer named; breach-response steps in `docs/PRIVACY.md` agreed.

## 9. Upgrades and rollback

- **Upgrade:** push to `main` on the git server → Coolify redeploys. Schema migrations run once on
  start-up and always back up the database first.
- **Rollback code:** Coolify → Deployments → redeploy the previous one.
- **Rollback data** (only if a migration went wrong): stop the app, copy
  `/data/backups/hr_audit-pre-migration-vN-*.db` over `/data/hr_audit.db`, redeploy the previous image.

## 10. Local use on this PC

Double-click `start.bat` (sets up `.venv` on first run, serves `http://localhost:5000`, data in
`.\data`). Tests: `.venv\Scripts\python -m pytest -q tests` and, with Docker running,
`.venv\Scripts\python tests\docker_e2e.py`.
