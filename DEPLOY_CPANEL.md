# Deploying the AnotherBPO HR Compliance Suite on cPanel (via GitHub)

This is a Flask + SQLite app. cPanel runs it through **Setup Python App**
(Phusion Passenger), using `passenger_wsgi.py` as the entry point. We deploy by
pulling the code from GitHub with cPanel's **Git Version Control** feature.

---

## A. One-time: get the code onto GitHub
(Already done if you used the project's git setup — the repo lives at the URL you pushed to.)

```
git add .
git commit -m "your message"
git push
```

---

## B. Clone the repo into cPanel

1. Log in to cPanel → **Git Version Control** → **Create**.
2. **Clone URL:** your GitHub repo URL.
   - Public repo → `https://github.com/<user>/<repo>.git`
   - Private repo → use an HTTPS URL with a GitHub **Personal Access Token**, e.g.
     `https://<token>@github.com/<user>/<repo>.git`
3. **Repository Path:** e.g. `hr_audit` (creates `/home/<youruser>/hr_audit`).
4. Click **Create**. cPanel clones the files.

To pull future updates: Git Version Control → **Manage** → **Update from Remote**
(then restart the Python app — step D).

---

## C. Create the Python application

cPanel → **Setup Python App** → **Create Application**:

- **Python version:** 3.11 (or newest available)
- **Application root:** `hr_audit` (the path you cloned into)
- **Application URL:** the domain / subdomain / subfolder to serve it from
- **Application startup file:** `passenger_wsgi.py`
- **Application Entry point:** `application`

Click **Create**.

---

## D. Install dependencies & set the secret key

1. In Setup Python App, set **Configuration files** to `requirements.txt`, then
   **Run Pip Install**. (Or use the shown `source .../activate` command in cPanel
   Terminal and run `pip install -r requirements.txt`.)
2. Add an **Environment variable**:
   - **Name:** `HR_AUDIT_SECRET`
   - **Value:** a long random string (40+ characters)
3. Click **Restart**.

Visit your Application URL — you should see the landing page. Log in at `/login`.

---

## E. First-run checklist

- **Change the default passwords immediately.** Log in as `admin / anotherbpo2024`,
  go to **Users**, and reset both the `admin` and `user` passwords.
- **Enable HTTPS** for the domain (cPanel AutoSSL / Let's Encrypt) so logins are encrypted.
- This is a **test** deployment — use **dummy data**, not real employee records, while it
  is on shared hosting across the public internet.

---

## Notes & limits

- **Database:** SQLite at `data/hr_audit.db` inside the app folder. It is created
  automatically on first run and is **git-ignored** (never committed), so each
  environment keeps its own data. Back it up by downloading that file.
- **Branding:** this build ships a neutral "AnotherBPO" text wordmark — no company
  logo. Real branding can be restored later.
- **Trash purge:** trashed projects auto-purge after their 30-day hold the next time
  the Trash or GBS dashboard loads — no cron job required.
- **Scale:** SQLite suits a small number of concurrent users. For heavy concurrent
  use later, migrate to MySQL/PostgreSQL (cPanel provides MySQL).
- Local development is unchanged: run `start.bat` (or `python app.py`); debug stays on
  locally and is forced **off** in production by `passenger_wsgi.py`.
