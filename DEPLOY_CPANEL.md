# Deploy — AnotherBPO HR Compliance Suite → cPanel (private GitHub repo)

**Repo:** `https://github.com/1syntra1/anotherbpo-hr-compliance` (private)
**Stack:** Flask + SQLite, run via cPanel **Setup Python App** (Passenger),
deployed by pulling from GitHub with **Git Version Control**.

Work top to bottom. Tick each box as you go.

---

> **Important (Afrihost / most cPanel hosts):** the Git Version Control tool **rejects
> tokens or passwords embedded in the clone URL** ("The clone URL cannot include a
> password"). Private repos must be cloned over **SSH using a deploy key**, as below.

## Part 1 — Generate an SSH key in cPanel
- [ ] 1.1  cPanel → **SSH Access** → **Manage SSH Keys**.
- [ ] 1.2  Click **Generate a New Key**.
- [ ] 1.3  **Key name:** leave the default (`id_rsa`) — git picks it up automatically.
- [ ] 1.4  **Passphrase:** leave **empty** (so automated pulls work).
- [ ] 1.5  Click **Generate Key**.
- [ ] 1.6  Back on Manage SSH Keys → find the key → **Manage** → **Authorize**.
- [ ] 1.7  **View / Download** the **Public Key** → copy the whole text (starts with `ssh-rsa …`).

---

## Part 2 — Add the public key to GitHub as a Deploy Key
- [ ] 2.1  GitHub → repo **anotherbpo-hr-compliance** → **Settings**.
- [ ] 2.2  Left menu → **Deploy keys** → **Add deploy key**.
- [ ] 2.3  **Title:** `Afrihost cPanel`.
- [ ] 2.4  **Key:** paste the public key from 1.7.
- [ ] 2.5  Leave **Allow write access** *unchecked* (read-only is enough).
- [ ] 2.6  Click **Add key**.

---

## Part 3 — Clone the repo into cPanel (SSH URL)
- [ ] 3.1  cPanel → **Git™ Version Control** → **Create**.
- [ ] 3.2  **Clone URL** — use the SSH form (no token, no password):

```
git@github.com:1syntra1/anotherbpo-hr-compliance.git
```

- [ ] 3.3  **Repository Path:** `hr_audit`  (creates `/home/<youruser>/hr_audit`).
- [ ] 3.4  Click **Create**. Wait for cPanel to finish cloning the files.

> If this fails with a host-authenticity / `known_hosts` error: open cPanel's **SSH Access
> → Terminal** (or SSH in) and run `ssh -T git@github.com`, type `yes` to accept GitHub's
> fingerprint, then retry the clone.

---

## Part 4 — Create the Python application
- [ ] 4.1  cPanel → **Setup Python App** → **Create Application**.
- [ ] 4.2  **Python version:** `3.11` (or the newest available).
- [ ] 4.3  **Application root:** `hr_audit`.
- [ ] 4.4  **Application URL:** choose the domain / subdomain / subfolder to serve from.
- [ ] 4.5  **Application startup file:** `passenger_wsgi.py`.
- [ ] 4.6  **Application Entry point:** `application`.
- [ ] 4.7  Click **Create**.

---

## Part 5 — Install dependencies & set the secret key
- [ ] 5.1  Still in Setup Python App, set **Configuration files** to `requirements.txt`.
- [ ] 5.2  Click **Run Pip Install** and wait for it to finish.
- [ ] 5.3  Under **Environment variables**, add:
        **Name** = `HR_AUDIT_SECRET`  ·  **Value** = a long random string (40+ characters).
- [ ] 5.4  Click **Restart** (or **Restart Application**).

---

## Part 6 — Go live & secure it
- [ ] 6.1  Open your **Application URL** → you should see the landing page.
- [ ] 6.2  Go to `/login` and sign in as **`admin` / `anotherbpo2024`**.
- [ ] 6.3  Go to **Users** → reset the **admin** password.
- [ ] 6.4  Reset the **user** password too (or delete the `user` account if not needed).
- [ ] 6.5  cPanel → **SSL/TLS Status** → enable **AutoSSL / Let's Encrypt** for the domain
        (so logins are encrypted over HTTPS).
- [ ] 6.6  This is a **test** site on the public internet — load **dummy data only**, not real
        employee records.

---

## Updating the app later
When you make changes locally and want them live:

- [ ] U.1  On your PC:  `git add .`  →  `git commit -m "..."`  →  `git push`
- [ ] U.2  cPanel → **Git Version Control** → **Manage** → **Update from Remote**.
- [ ] U.3  cPanel → **Setup Python App** → **Restart**.

---

## Quick reference
| Item | Value |
|------|-------|
| Repo | `https://github.com/1syntra1/anotherbpo-hr-compliance` (private) |
| Startup file | `passenger_wsgi.py` |
| Entry point | `application` |
| Default login | `admin` / `anotherbpo2024` (change immediately) |
| Secret env var | `HR_AUDIT_SECRET` |
| Database | `data/hr_audit.db` (auto-created, git-ignored, back it up) |

## Notes
- The SQLite database and the company logo are **git-ignored**, so they are never pushed —
  each environment keeps its own data, and the build ships neutral "AnotherBPO" text branding.
- SQLite suits a small number of concurrent users (fine for this test). For heavy concurrent
  use later, migrate to MySQL (cPanel provides it).
- Trashed projects auto-purge after the 30-day hold the next time the Trash/GBS page loads —
  no cron job needed.
- If the clone fails with an auth error, the SSH deploy key isn't matching — re-copy the
  public key (Part 1.7) into the repo's **Deploy keys** (Part 2), and make sure you used the
  `git@github.com:...` SSH URL, not an `https://` one.
