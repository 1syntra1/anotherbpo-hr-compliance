# Deploy — AnotherBPO HR Compliance Suite → cPanel (private GitHub repo)

**Repo:** `https://github.com/1syntra1/anotherbpo-hr-compliance` (private)
**Stack:** Flask + SQLite, run via cPanel **Setup Python App** (Passenger),
deployed by pulling from GitHub with **Git Version Control**.

Work top to bottom. Tick each box as you go.

---

## Part 1 — Create a GitHub access token
cPanel needs a token to clone a **private** repo. Use a fine-grained token (read-only).

- [ ] 1.1  Go to **https://github.com** and sign in as `1syntra1`.
- [ ] 1.2  Click your **profile photo** (top-right) → **Settings**.
- [ ] 1.3  Bottom of the left menu → **Developer settings**.
- [ ] 1.4  **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
- [ ] 1.5  **Token name:** `cPanel deploy`.
- [ ] 1.6  **Expiration:** pick a date (e.g. 90 days — you can regenerate later).
- [ ] 1.7  **Resource owner:** `1syntra1`.
- [ ] 1.8  **Repository access:** choose **Only select repositories** → select
        `anotherbpo-hr-compliance`.
- [ ] 1.9  **Permissions** → **Repository permissions** → **Contents** → set to **Read-only**.
        (That is all cPanel needs to clone and pull.)
- [ ] 1.10 Click **Generate token** → **copy the token now** (GitHub shows it only once).
        Keep it somewhere safe and private.

> Classic token alternative: Developer settings → **Tokens (classic)** → Generate new token →
> tick the **`repo`** scope. Works too, but grants broader access than the fine-grained option above.

---

## Part 2 — Build your clone URL
- [ ] 2.1  Take this template and paste your token where shown:

```
https://1syntra1:YOUR_TOKEN_HERE@github.com/1syntra1/anotherbpo-hr-compliance.git
```

- [ ] 2.2  Treat this URL like a password — it contains your token. Don't share or email it.

---

## Part 3 — Clone the repo into cPanel
- [ ] 3.1  cPanel → **Git™ Version Control** → **Create**.
- [ ] 3.2  **Clone URL:** paste the URL from Part 2.
- [ ] 3.3  **Repository Path:** `hr_audit`  (creates `/home/<youruser>/hr_audit`).
- [ ] 3.4  Click **Create**. Wait for cPanel to finish cloning the files.

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
- If the clone fails with an auth error, your token is wrong/expired — regenerate it (Part 1)
  and update the URL in **Git Version Control → Manage**.
