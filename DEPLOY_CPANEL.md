# Deploy — AnotherBPO HR Compliance Suite → cPanel (Afrihost)

**Repo:** `https://github.com/1syntra1/anotherbpo-hr-compliance`
**Stack:** Flask + SQLite, run via cPanel **Setup Python App** (Passenger),
deployed by pulling from GitHub with **Git Version Control**.

> **What worked on Afrihost:** cPanel's Git tool has a hard **5-second timeout** that
> SSH clones kept exceeding (host-key handshake). The reliable method is to clone the
> **public** repo over plain **HTTPS** (clones in ~1s, no key, no SSH). Steps below use that.
> If you don't want the repo public long-term, see **"Keeping it private"** at the bottom.

---

## Part 1 — Make sure the repo is public (for the HTTPS clone)
- [ ] 1.1  The repo is currently **public**. (To check/change later: GitHub → repo →
        **Settings** → scroll to **Danger Zone** → **Change visibility**.)

---

## Part 2 — Clone the repo into cPanel (HTTPS)
- [ ] 2.1  cPanel → **Git™ Version Control** → **Create**.
- [ ] 2.2  **Clone URL** (plain HTTPS — no username, no token):

```
https://github.com/1syntra1/anotherbpo-hr-compliance.git
```

- [ ] 2.3  **Repository Path:** `hr_audit`  (creates `/home/<youruser>/hr_audit`).
- [ ] 2.4  Click **Create** → it completes immediately.

---

## Part 3 — Create the Python application
- [ ] 3.1  cPanel → **Setup Python App** → **Create Application**.
- [ ] 3.2  **Python version:** `3.11` (or newest available).
- [ ] 3.3  **Application root:** `hr_audit`.
- [ ] 3.4  **Application URL:** the domain / subdomain / subfolder to serve from.
- [ ] 3.5  **Application startup file:** `passenger_wsgi.py`.
- [ ] 3.6  **Application Entry point:** `application`.
- [ ] 3.7  Click **Create**.

---

## Part 4 — Install dependencies & set the secret key
- [ ] 4.1  In Setup Python App, set **Configuration files** to `requirements.txt` → **Run Pip Install**.
- [ ] 4.2  Add an **Environment variable**: `HR_AUDIT_SECRET` = a long random string (40+ chars).
- [ ] 4.3  Click **Restart**.

---

## Part 5 — Go live & secure it
- [ ] 5.1  Open your **Application URL** → landing page should appear.
- [ ] 5.2  Sign in at `/login` as **`admin` / `anotherbpo2024`**.
- [ ] 5.3  **Users** → reset the **admin** password.
- [ ] 5.4  Reset the **user** password (or delete the `user` account).
- [ ] 5.5  cPanel → **SSL/TLS Status** → enable **AutoSSL / Let's Encrypt** (HTTPS).
- [ ] 5.6  Public test site → use **dummy data only**, not real employee records.

---

## Updating the app later
You have cPanel **Terminal** access, which is the easiest route (no 5-second cap):

- [ ] U.1  On your PC: `git add .` → `git commit -m "..."` → `git push`
- [ ] U.2  cPanel → **Terminal** → `cd hr_audit && git pull`
- [ ] U.3  cPanel → **Setup Python App** → **Restart**

(The Git Version Control UI's **Update from Remote** also works for the public repo.)

---

## Cleanup — abandoned SSH-key attempt
We first tried an SSH deploy key, which **did not work** on Afrihost (the 5-second timeout).
Those artifacts have been removed so nothing dangling is left:

- [x] GitHub repo **Deploy key** ("Afrihost cPanel") — **deleted**.
- [x] Local key folder `C:\Users\Psylence\anotherbpo_deploy` — **deleted**.
- [ ] **cPanel imported SSH key** `id_rsa` — optional to remove: cPanel → **SSH Access →
      Manage SSH Keys** → `id_rsa` → **Delete** (harmless to leave, but unused).
- [ ] The earlier **GitHub personal access token** (shown on screen) — make sure it was
      **deleted** in GitHub → Settings → Developer settings → Personal access tokens.

---

## Keeping it private (optional, after the test)
If you want the repo private again:
1. GitHub → repo → Settings → **Change visibility** → Private.
2. Updates then need auth. Easiest with your Terminal access: set up the SSH deploy key
   *for manual pulls only* (it bypasses the 5s UI limit), or use a token in a
   `git pull https://<token>@github.com/...` command run in Terminal.
   (The cPanel "Update from Remote" button won't work for a private repo on Afrihost.)

---

## Quick reference
| Item | Value |
|------|-------|
| Clone URL (public) | `https://github.com/1syntra1/anotherbpo-hr-compliance.git` |
| Repo path | `hr_audit` |
| Startup file | `passenger_wsgi.py` |
| Entry point | `application` |
| Default login | `admin` / `anotherbpo2024` (change immediately) |
| Secret env var | `HR_AUDIT_SECRET` |
| Database | `data/hr_audit.db` (auto-created, git-ignored, back it up) |

## Notes
- SQLite & the company logo are git-ignored — never pushed. Each environment keeps its own data.
- Trashed projects auto-purge after the 30-day hold next time the Trash/GBS page loads — no cron needed.
- SQLite suits a small number of concurrent users (fine for this test). Migrate to MySQL for heavy concurrent use later.
