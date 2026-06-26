# Deploy — AnotherBPO HR Compliance Suite → PythonAnywhere (free)

Afrihost shared hosting is **PHP-only** (no "Setup Python App"), so we host the
Flask test on **PythonAnywhere**, which is built for Python. Free tier is enough:
it gives a public URL (`USERNAME.pythonanywhere.com`), persistent storage for the
SQLite database, and HTTPS.

**Repo (public):** `https://github.com/1syntra1/anotherbpo-hr-compliance.git`

---

## Part 1 — Create the account
- [ ] 1.1  Go to **https://www.pythonanywhere.com** → **Pricing & signup** → **Create a Beginner account** (free).
- [ ] 1.2  Verify your email and log in. Note your username — it becomes your URL
        `USERNAME.pythonanywhere.com`.

---

## Part 2 — Clone the code (Bash console)
- [ ] 2.1  Top menu → **Consoles** → **Bash** (start a new Bash console).
- [ ] 2.2  Clone the repo:
```
git clone https://github.com/1syntra1/anotherbpo-hr-compliance.git
```
- [ ] 2.3  Create a virtualenv and install dependencies:
```
mkvirtualenv --python=/usr/bin/python3.11 anotherbpo
pip install -r anotherbpo-hr-compliance/requirements.txt
```
- [ ] 2.4  Note the virtualenv path it prints (usually
        `/home/USERNAME/.virtualenvs/anotherbpo`) — you'll need it in Part 3.

---

## Part 3 — Create the web app
- [ ] 3.1  Top menu → **Web** → **Add a new web app** → **Next**.
- [ ] 3.2  Framework: choose **Manual configuration** (NOT "Flask") → **Next**.
- [ ] 3.3  Python version: **3.11** → **Next** → finish.

---

## Part 4 — Point the web app at the code
On the **Web** tab, scroll down and set:

- [ ] 4.1  **Source code:** `/home/USERNAME/anotherbpo-hr-compliance`
- [ ] 4.2  **Working directory:** `/home/USERNAME/anotherbpo-hr-compliance`
- [ ] 4.3  **Virtualenv:** enter `/home/USERNAME/.virtualenvs/anotherbpo`
- [ ] 4.4  **WSGI configuration file:** click the link (e.g.
        `/var/www/USERNAME_pythonanywhere_com_wsgi.py`) to edit it. **Delete everything**
        in that file and replace with:

```python
import sys
path = "/home/USERNAME/anotherbpo-hr-compliance"
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
application.debug = False
```

> Replace **USERNAME** with your PythonAnywhere username in all paths above.

- [ ] 4.5  **Save** the WSGI file.

---

## Part 5 — Launch & secure
- [ ] 5.1  Back on the **Web** tab, click the big green **Reload** button.
- [ ] 5.2  Open `https://USERNAME.pythonanywhere.com` → landing page should appear.
        (HTTPS is on by default.)
- [ ] 5.3  Sign in at `/login` as **`admin` / `anotherbpo2024`**.
- [ ] 5.4  Go to **Users** → reset the **admin** and **user** passwords.
- [ ] 5.5  Public test site → use **dummy data only**, not real employee records.

---

## Updating the app later
- [ ] U.1  On your PC: `git add .` → `git commit -m "..."` → `git push`
- [ ] U.2  PythonAnywhere → **Consoles → Bash**:
        `cd anotherbpo-hr-compliance && git pull`
- [ ] U.3  If `requirements.txt` changed:
        `workon anotherbpo && pip install -r requirements.txt`
- [ ] U.4  **Web** tab → **Reload**.

---

## Quick reference
| Item | Value |
|------|-------|
| Clone URL | `https://github.com/1syntra1/anotherbpo-hr-compliance.git` |
| Virtualenv | `/home/USERNAME/.virtualenvs/anotherbpo` |
| Source / working dir | `/home/USERNAME/anotherbpo-hr-compliance` |
| WSGI imports | `from app import app as application` |
| Default login | `admin` / `anotherbpo2024` (change immediately) |
| Database | `data/hr_audit.db` (auto-created, git-ignored, persists) |

## Notes
- The app makes **no outbound internet calls** from the server (Bootstrap/icons load in
  the visitor's browser via CDN), so PythonAnywhere free-tier's outbound whitelist is a non-issue.
- SQLite persists on PythonAnywhere's disk between reloads.
- Free tier sleeps nothing important for a test; the app stays reachable. (Free accounts
  must click a "run until 3 months" button occasionally to keep the web app enabled.)
