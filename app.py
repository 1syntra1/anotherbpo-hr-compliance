import os
import io
import csv
import sqlite3
import secrets
import logging
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (
    Flask, g, render_template, request, redirect,
    url_for, flash, send_file, jsonify, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException

log = logging.getLogger("hr_audit")

# ─── Configuration ───────────────────────────────────────────────────────────
# All runtime state (database, uploads, generated reports, secret key) lives under
# DATA_DIR so a single persistent volume is enough in Docker/Coolify.
DATA_DIR = os.environ.get("HR_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATABASE = os.path.join(DATA_DIR, "hr_audit.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
ALLOWED_EXTENSIONS = {"csv", "xlsx"}
MAX_UPLOAD_MB = int(os.environ.get("HR_MAX_UPLOAD_MB", "10"))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def _load_secret_key():
    """HR_AUDIT_SECRET wins. Otherwise a random key is generated once and kept in
    DATA_DIR (never in the repo), so sessions survive restarts on the same volume."""
    env_key = os.environ.get("HR_AUDIT_SECRET")
    if env_key:
        if len(env_key) < 32:
            raise RuntimeError("HR_AUDIT_SECRET must be at least 32 characters.")
        return env_key
    path = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if len(key) >= 32:
            return key
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = _load_secret_key()
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies by default; set HR_COOKIE_SECURE=0 only for plain-HTTP local use.
    SESSION_COOKIE_SECURE=os.environ.get("HR_COOKIE_SECURE", "1") != "0",
    SESSION_COOKIE_NAME="hrcs_session",
)

PURGE_HOLD_DAYS = 30  # admin-approved deletions are held this many days before purge
MAX_FAILED_LOGINS = 5       # consecutive failures before the account is locked
LOCKOUT_MINUTES = 15

# ── Session behaviour ────────────────────────────────────────
# When "stay logged in" is NOT chosen: after IDLE_WARNING_AFTER seconds of inactivity a
# warning popup appears and counts down LOGOUT_COUNTDOWN seconds before signing out.
# INACTIVITY_TIMEOUT is the server-side backstop and must comfortably exceed
# IDLE_WARNING_AFTER + LOGOUT_COUNTDOWN so a "Stay signed in" click always succeeds.
IDLE_WARNING_AFTER = 14 * 60  # seconds idle before the warning popup
LOGOUT_COUNTDOWN = 60         # seconds counted down inside the popup
INACTIVITY_TIMEOUT = 16 * 60  # server-side backstop (seconds)
IDLE_LABEL = "15 minutes"
app.permanent_session_lifetime = timedelta(days=30)  # "stay logged in" duration


# ─── CSRF protection ─────────────────────────────────────────────────────────

def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


@app.before_request
def csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
    expected = session.get("_csrf")
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        abort(400, description="Your form expired or was not sent from this site. Please reload the page and try again.")


# ─── Authentication / session ────────────────────────────────────────────────

@app.before_request
def load_current_user():
    """Re-read the signed-in user on every request so a deleted, disabled or demoted
    account loses access immediately (the cookie alone is never trusted for role)."""
    g.user = None
    if not session.get("logged_in"):
        return
    row = get_db().execute("SELECT * FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
    if (row is None or not row["active"]
            or row["session_version"] != session.get("session_version")):
        session.clear()
        if request.endpoint not in ("login", "static", "landing"):
            flash("Your session has ended. Please sign in again.", "info")
            return redirect(url_for("login"))
        return
    g.user = row
    session["role"] = row["role"]
    session["username"] = row["username"]


@app.before_request
def enforce_inactivity_timeout():
    """Sign out non-'remember' sessions after INACTIVITY_TIMEOUT of no requests.
    (A client-side idle timer logs the user out even without a request; this is the
    server-side backstop.) Sessions with 'stay logged in' are exempt."""
    if not session.get("logged_in"):
        return
    if session.get("remember"):
        return
    if request.endpoint in ("logout", "static"):
        return
    now = datetime.now().timestamp()
    last = session.get("last_activity")
    if last is not None and (now - last) > INACTIVITY_TIMEOUT:
        session.clear()
        flash("You were signed out after %s of inactivity." % IDLE_LABEL, "info")
        return redirect(url_for("login"))
    session["last_activity"] = now


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()")
    # Content-Security-Policy: all resources come from our own origin (Bootstrap is
    # vendored locally, no CDNs). 'unsafe-inline' is still required because templates use
    # inline <script> blocks and inline style="" attributes (e.g. the dashboard donuts).
    # Future hardening: move inline scripts to per-request nonces and drop 'unsafe-inline'
    # from script-src. Even as-is this blocks external script/resource injection,
    # clickjacking, <base> hijacking and cross-origin form posting.
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'")
    # HSTS only over HTTPS (behind the Coolify TLS proxy, ProxyFix sets the scheme). Sending
    # it over plain HTTP would be ignored anyway; this avoids surprising local HTTP use.
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if session.get("logged_in"):
        # Employee data must not linger in the browser cache after sign-out.
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(HTTPException)
def handle_http_error(e):
    if request.is_json or request.path.startswith(("/checklist/", "/api/", "/session/")):
        return jsonify({"ok": False, "error": e.description}), e.code
    return render_template("error.html", code=e.code, title=e.name, message=e.description), e.code


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    # Never show stack traces, SQL or paths to the user; keep the detail in the server log.
    log.exception("Unhandled error on %s %s", request.method, request.path)
    if request.is_json or request.path.startswith(("/checklist/", "/api/", "/session/")):
        return jsonify({"ok": False, "error": "Something went wrong. The error has been logged."}), 500
    return render_template("error.html", code=500, title="Something went wrong",
                           message="The error has been logged. Please try again or contact an administrator."), 500


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if g.get("user") is None:
            return redirect(url_for("login"))
        if g.user["role"] != "admin":
            log_action("access_denied", "auth", "Blocked non-admin access to %s" % request.path)
            flash("That area is restricted to administrators.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ─── Database helpers ────────────────────────────────────────────────────────

def connect_db():
    db = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _seed_admin(db):
    """First run only: create a single administrator. The password comes from
    HR_ADMIN_PASSWORD, or is generated and written to DATA_DIR for the operator to
    collect. Either way the account must change its password at first sign-in."""
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        return
    username = os.environ.get("HR_ADMIN_USERNAME", "admin")
    password = os.environ.get("HR_ADMIN_PASSWORD")
    if not password:
        password = secrets.token_urlsafe(14)
        path = os.path.join(DATA_DIR, "initial_admin_password.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Username: %s\nPassword: %s\n\nDelete this file after first sign-in.\n"
                     % (username, password))
        log.warning("Created first administrator '%s'. Password written to %s", username, path)
    db.execute(
        """INSERT INTO users (username, password_hash, role, full_name, must_change_password)
           VALUES (?,?,?,?,1)""",
        (username, generate_password_hash(password), "admin", "System Administrator"),
    )


def _seed_starter_records(db):
    """Fresh install: open the current period for each annual obligation so the
    compliance calendar is useful on day one. Everything is editable."""
    import json
    from modules import records as recmod
    row = db.execute("SELECT params_json FROM rule_sets WHERE code='STATUTORY-DEADLINES'").fetchone()
    params = json.loads(row[0]) if row else {}
    system = {"id": None, "username": "system"}
    today = date.today()
    for module, yr in (("ee", today.year), ("wsp", today.year), ("coida", today.year - 1),
                       ("cipc_bo", today.year), ("bbbee", today.year)):
        label, start, end, due = recmod.default_period(module, yr, params)
        if due and due < today.isoformat():  # that cycle has passed: open the next one still due
            yr += 1
            label, start, end, due = recmod.default_period(module, yr, params)
        recmod.create_record(db, module, label, system, period_year=yr, period_start=start, period_end=end,
                             due_date=due)


def init_db():
    from modules.migrations import migrate
    migrate(DATABASE, os.path.join(DATA_DIR, "backups"))
    db = connect_db()
    try:
        # WAL lets readers continue while one request writes; it persists in the file.
        db.execute("PRAGMA journal_mode = WAL")
        _seed_admin(db)
        if not db.execute("SELECT COUNT(*) FROM compliance_records").fetchone()[0]:
            _seed_starter_records(db)
        db.commit()
    finally:
        db.close()


# Initialise / migrate the database on startup
with app.app_context():
    init_db()


# Dash BPO falls under MICT SETA only. Keep this a single entry so the company
# profile cannot be pointed at the wrong SETA.
SETAS = ["MICT SETA"]


if os.environ.get("HR_TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─── Audit log ───────────────────────────────────────────────────────────────

def _client_ip():
    # Behind Coolify's proxy the real client is the first X-Forwarded-For hop
    # (ProxyFix is applied when HR_TRUST_PROXY=1, so remote_addr is already correct).
    try:
        return request.remote_addr
    except RuntimeError:
        return None


def log_action(action, category, detail, target=None, username=None,
               entity=None, entity_id=None, before=None, after=None, commit=True):
    """Append an entry to the audit log.

    Pass commit=False when the audited change is part of a larger transaction, so the
    change and its audit entry are saved (or rolled back) together. Failures are
    written to the server log rather than silently dropped."""
    import json
    user = g.get("user") if g else None
    try:
        db = get_db()
        db.execute(
            """INSERT INTO audit_log (username, role, action, category, detail, target,
                                      user_id, ip, entity, entity_id, before_json, after_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                username or (user["username"] if user else session.get("username", "unknown")),
                user["role"] if user else session.get("role", "-"),
                action, category, detail, target,
                user["id"] if user else None,
                _client_ip(),
                entity, None if entity_id is None else str(entity_id),
                json.dumps(before, default=str, ensure_ascii=False) if before is not None else None,
                json.dumps(after, default=str, ensure_ascii=False) if after is not None else None,
            ),
        )
        if commit:
            db.commit()
    except Exception:
        log.exception("AUDIT LOG WRITE FAILED: %s / %s / %s", action, category, detail)


def friendly_scope(db, module):
    """Turn a checklist scope like 'record:5' into a readable label."""
    if module and module.startswith("record:"):
        try:
            row = db.execute("SELECT module, period_label FROM compliance_records WHERE id=?",
                             (int(module.split(":", 1)[1]),)).fetchone()
            if row:
                from modules.records import module_title
                return "%s %s" % (module_title(row["module"]), row["period_label"])
        except (ValueError, IndexError):
            pass
    return module


def resolve_item_text(db, module, item_key):
    """Best-effort human-readable text for a checklist item key."""
    if item_key and item_key.startswith("custom-"):
        try:
            cid = int(item_key.split("-", 1)[1])
            row = db.execute("SELECT text FROM custom_tasks WHERE id=?", (cid,)).fetchone()
            if row:
                return row["text"]
        except (ValueError, IndexError):
            pass
        return "(custom task)"
    # Predefined: search the checklist definition for the record's module
    from modules.checklists import get_checklist
    base = module
    if module and module.startswith("record:"):
        row = db.execute("SELECT module FROM compliance_records WHERE id=?", (module.split(":", 1)[1],)).fetchone()
        base = row["module"] if row else None
    checklist = get_checklist(base) if base else None
    if checklist:
        for step in checklist["steps"]:
            for it in step["items"]:
                if it["key"] == item_key:
                    return it["text"]
    return item_key


# ─── Trash / purge helpers ───────────────────────────────────────────────────

def purge_expired_projects(db):
    """Remove trashed GBS projects whose 30-day hold has elapsed. The compliance record
    behind the project is kept as a deleted tombstone with its full history, so the
    audit trail and timeline survive; records with submissions, reports or evidence
    are never purged."""
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    rows = db.execute(
        "SELECT * FROM gbs_projects WHERE trashed=1 AND purge_at IS NOT NULL AND purge_at <= ?",
        (now,),
    ).fetchall()
    purged = 0
    for r in rows:
        rid = r["record_id"]
        if rid and db.execute(
                "SELECT (SELECT COUNT(*) FROM submissions WHERE record_id=?) + "
                "(SELECT COUNT(*) FROM report_versions WHERE record_id=?) + "
                "(SELECT COUNT(*) FROM evidence_files WHERE record_id=?)", (rid, rid, rid)).fetchone()[0]:
            log_action("trash_purge_blocked", "trash", "Purge skipped: GBS %s %s has evidence" % (r["quarter"], r["year"]),
                       target="gbs:%d" % r["id"], username="system", commit=False)
            db.execute("UPDATE gbs_projects SET purge_at=NULL WHERE id=?", (r["id"],))
            continue
        db.execute("DELETE FROM gbs_projects WHERE id=?", (r["id"],))
        if rid:
            scope = "record:%d" % rid
            db.execute("DELETE FROM checklist_state WHERE module=?", (scope,))
            db.execute("DELETE FROM custom_tasks WHERE module=?", (scope,))
            db.execute("DELETE FROM hidden_tasks WHERE module=?", (scope,))
            db.execute("""INSERT INTO workflow_events (record_id, event, username, comment)
                          VALUES (?, 'purged', 'system', 'Project data purged after the approved hold')""", (rid,))
        log_action(
            "trash_purge", "trash",
            "Auto-purged after %d-day hold: GBS %s %s" % (PURGE_HOLD_DAYS, r["quarter"], r["year"]),
            target="gbs:%d" % r["id"], username="system", commit=False,
        )
        purged += 1
    db.commit()
    return purged


# ─── Checklist helpers ───────────────────────────────────────────────────────

def get_checklist_states(db, module):
    """Return {item_key: bool} for all stored checkbox states of a module."""
    rows = db.execute(
        "SELECT item_key, checked FROM checklist_state WHERE module=?", (module,)
    ).fetchall()
    return {r["item_key"]: bool(r["checked"]) for r in rows}


def get_custom_tasks(db, module):
    """Return {step_key: [ {id, text, key} ]} for a module's user-added tasks."""
    rows = db.execute(
        "SELECT id, step_key, text FROM custom_tasks WHERE module=? ORDER BY id",
        (module,),
    ).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["step_key"], []).append(
            {"id": r["id"], "text": r["text"], "key": "custom-%d" % r["id"]}
        )
    return grouped


def get_hidden_tasks(db, module):
    """Return a set of item_keys hidden for this module."""
    rows = db.execute(
        "SELECT item_key FROM hidden_tasks WHERE module=?", (module,)
    ).fetchall()
    return {r["item_key"] for r in rows}


def checklist_progress(checklist, states, custom_by_step=None, hidden=None):
    """Return (done, total, percent) including predefined + custom tasks,
    excluding any items in `hidden`."""
    hidden = hidden or set()
    valid_keys = {
        it["key"] for step in checklist["steps"] for it in step["items"]
        if it["key"] not in hidden
    }
    if custom_by_step:
        for tasks in custom_by_step.values():
            for t in tasks:
                if t["key"] not in hidden:
                    valid_keys.add(t["key"])
    total = len(valid_keys)
    done = sum(1 for k in valid_keys if states.get(k))
    pct = round(done / total * 100) if total else 0
    return done, total, pct


# ─── Landing / Auth ──────────────────────────────────────────────────────────

@app.route("/home")
def landing():
    return render_template("landing.html", now=datetime.now)


COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456789012", "qwertyuiop12",
    "anotherbpo2024", "letmein12345", "welcome12345", "admin1234567", "changeme1234",
}


def password_problem(password, username=""):
    """Return a reason the password is unacceptable, or None."""
    if len(password) < 12:
        return "Password must be at least 12 characters."
    if len(password) > 256:
        return "Password is too long."
    if password.lower() in COMMON_PASSWORDS:
        return "That password is too common."
    if username and username.lower() in password.lower():
        return "Password must not contain the username."
    if len(set(password)) < 5:
        return "Password is too repetitive."
    return None


# A real hash of a random value, so a login for an unknown username costs the same
# time as a wrong password (no account enumeration by timing).
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def _start_session(user, remember):
    session.clear()  # new session content on every sign-in (no fixation)
    session["logged_in"] = True
    session["user_id"] = user["id"]
    session["session_version"] = user["session_version"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["full_name"] = user["full_name"] or user["username"]
    session["remember"] = remember
    session.permanent = remember  # remember = 30-day cookie; else browser-session cookie
    session["last_activity"] = datetime.now().timestamp()


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()[:150]
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        now = datetime.now()

        if user and user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > now:
            check_password_hash(_DUMMY_HASH, password)
            log_action("login_locked", "auth", "Sign-in refused: account locked",
                       target=username, username=username)
            flash("Invalid username or password, or the account is temporarily locked.", "danger")
            return render_template("login.html"), 401

        ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
        if user and ok and user["active"]:
            db.execute("UPDATE users SET last_login=datetime('now'), failed_logins=0, locked_until=NULL "
                       "WHERE id=?", (user["id"],))
            db.commit()
            remember = bool(request.form.get("remember"))
            _start_session(user, remember)
            g.user = user
            log_action("login", "auth",
                       "Signed in (%s%s)" % (user["role"], ", stay logged in" if remember else ""))
            if user["must_change_password"]:
                flash("Please choose a new password before continuing.", "warning")
                return redirect(url_for("account_password"))
            flash(f"Welcome back, {session['full_name']}!", "success")
            return redirect(url_for("dashboard"))

        if user:
            fails = (user["failed_logins"] or 0) + 1
            locked = None
            if fails >= MAX_FAILED_LOGINS:
                locked = (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")
                fails = 0
            db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                       (fails, locked, user["id"]))
            db.commit()
            if locked:
                log_action("account_locked", "auth",
                           "Locked for %d minutes after %d failed sign-ins" % (LOCKOUT_MINUTES, MAX_FAILED_LOGINS),
                           target=username, username=username)
        log_action("login_failed", "auth",
                   "Failed sign-in attempt", target=username, username=username or "unknown")
        flash("Invalid username or password, or the account is temporarily locked.", "danger")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.before_request
def force_password_change():
    user = g.get("user")
    if (user is not None and user["must_change_password"]
            and request.endpoint not in ("account_password", "logout", "static", "session_ping")):
        return redirect(url_for("account_password"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def account_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        user = g.user
        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "danger")
        elif new != confirm:
            flash("The new passwords do not match.", "danger")
        elif check_password_hash(user["password_hash"], new):
            flash("The new password must be different from the current one.", "danger")
        elif password_problem(new, user["username"]):
            flash(password_problem(new, user["username"]), "danger")
        else:
            db = get_db()
            db.execute(
                """UPDATE users SET password_hash=?, must_change_password=0,
                   password_changed_at=datetime('now'), session_version=session_version+1
                   WHERE id=?""",
                (generate_password_hash(new), user["id"]),
            )
            log_action("password_change", "auth", "Changed own password",
                       entity="user", entity_id=user["id"], commit=False)
            db.commit()
            # Other sessions for this user are now invalid; keep this one.
            fresh = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            _start_session(fresh, session.get("remember", False))
            flash("Password changed.", "success")
            return redirect(url_for("dashboard"))
    return render_template("account_password.html")


@app.route("/session/ping", methods=["POST"])
@login_required
def session_ping():
    """Lightweight keep-alive. before_request refreshes last_activity, so a
    'Stay signed in' click resets the inactivity clock."""
    return jsonify({"ok": True})


@app.route("/logout", methods=["POST"])
def logout():
    timed_out = request.form.get("timeout")
    if g.get("user") is not None:
        log_action("logout", "auth",
                   "Auto sign-out (%s inactivity)" % IDLE_LABEL if timed_out else "Signed out")
    session.clear()
    if timed_out:
        flash("You were signed out after %s of inactivity." % IDLE_LABEL, "info")
        return redirect(url_for("login"))
    flash("You have been logged out.", "info")
    return redirect(url_for("landing"))


# ─── Shared helpers for compliance records ───────────────────────────────────

from modules import records as rec
from modules import reports_store
from modules.company import FIELDS as COMPANY_FIELDS, ProfileError, current_profile, save_profile
from modules.employees import (EMPLOYMENT_TYPES, GENDERS, OCC_LEVELS, PROVINCES, RACES, ImportFileError,
                               error_report_csv, insert_employee, plan_import, template_csv)
from modules.employees import validate as validate_employee
from modules.rules import all_rule_sets, get_rule_set, rule_set_by_id, unverified_count

EVIDENCE_KINDS = ["Supporting document", "Submitted report", "Confirmation / acknowledgement", "Receipt",
                  "Email confirmation", "Proof of payment", "Certificate / letter", "Other"]
EVIDENCE_TYPES = {  # extension: (mime, magic-bytes check)
    "pdf": ("application/pdf", lambda b: b.startswith(b"%PDF")),
    "png": ("image/png", lambda b: b.startswith(b"\x89PNG")),
    "jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8")),
    "jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8")),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", lambda b: b.startswith(b"PK")),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             lambda b: b.startswith(b"PK")),
    "csv": ("text/csv", lambda b: b"\x00" not in b[:4096] and not b.startswith(b"PK")),
    "txt": ("text/plain", lambda b: b"\x00" not in b[:4096] and not b.startswith(b"PK")),
    "eml": ("message/rfc822", lambda b: b"\x00" not in b[:4096]),
}


def active_users(db):
    return db.execute("SELECT id, username, full_name, role FROM users WHERE active=1 ORDER BY full_name").fetchall()


def load_record_or_404(db, record_id):
    r = rec.get_record(db, record_id)
    if r is None:
        abort(404, description="That compliance record does not exist.")
    return r


def default_period(module, year, db):
    rs = get_rule_set(db, "STATUTORY-DEADLINES")
    return rec.default_period(module, year, rs["params"] if rs else {})


def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def scope_for(record):
    return "record:%d" % record["id"]


def checklist_bundle(db, record):
    from modules.checklists import get_checklist
    checklist = get_checklist(record["module"])
    if not checklist:
        return None
    scope = scope_for(record)
    states = get_checklist_states(db, scope)
    custom = get_custom_tasks(db, scope)
    hidden = get_hidden_tasks(db, scope)
    done, total, pct = checklist_progress(checklist, states, custom, hidden)
    return {"scope": scope, "checklist": checklist, "states": states, "custom": custom, "hidden": hidden,
            "done": done, "total": total, "pct": pct, "locked": record["status"] in rec.CHECKLIST_LOCKED}


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    items = rec.calendar(db)
    bbbee_level = None
    latest = db.execute("""SELECT rv.snapshot_json FROM report_versions rv JOIN compliance_records r ON r.id=rv.record_id
                           WHERE rv.report_type='bbbee_xlsx' AND r.deleted=0 ORDER BY rv.id DESC LIMIT 1""").fetchone()
    if latest:
        import json
        from modules import bbbee as bb
        try:
            lvl = bb.calc_from_snapshot(json.loads(latest[0]))["level"]
            bbbee_level = "Non-compliant" if lvl == "NC" else "Level %s" % lvl
        except Exception:
            bbbee_level = None
    import calendar as _cal
    today = date.today()
    upcoming = sorted([i for i in items if i.get("due_date") and i["state"] != "done"],
                      key=lambda i: i["due_date"])[:5]
    ym = today.strftime("%Y-%m")
    due_days = {int(i["due_date"][8:10]) for i in items if i.get("due_date") and i["due_date"][:7] == ym}
    cal_weeks = [[{"day": d or None, "today": d == today.day, "due": d in due_days and d != 0}
                  for d in wk] for wk in _cal.Calendar(firstweekday=0).monthdayscalendar(today.year, today.month)]
    health = rec.health(items)
    return render_template(
        "dashboard.html",
        calendar=items,
        health=health,
        module_status=rec.module_status(items),
        notices=rec.notifications(db, items, g.user),
        unverified_rules=unverified_count(db),
        emp_count=db.execute("SELECT COUNT(*) FROM employees WHERE status='ACTIVE'").fetchone()[0],
        training_count=db.execute("SELECT COUNT(*) FROM training_records").fetchone()[0]
        + db.execute("SELECT COUNT(*) FROM training_events").fetchone()[0],
        logs_count=db.execute("SELECT COUNT(*) FROM logs_records").fetchone()[0],
        bbbee_level=bbbee_level,
        upcoming=upcoming,
        cal_weeks=cal_weeks,
        cal_month_label=today.strftime("%B %Y"),
        cal_weekdays=["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"],
        current_year=datetime.now().year,
    )


@app.route("/reports")
@login_required
def reports_hub():
    """Reports & insights hub — entry points to the reporting views."""
    db = get_db()
    emp_total = db.execute("SELECT COUNT(*) FROM employees WHERE status='ACTIVE'").fetchone()[0]
    submissions = db.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    reports_made = db.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
    return render_template("reports.html", emp_total=emp_total, submissions=submissions,
                           reports_made=reports_made)


# ─── Compliance records ──────────────────────────────────────────────────────

@app.route("/compliance")
@login_required
def compliance_list():
    db = get_db()
    module = request.args.get("module", "")
    status = request.args.get("status", "")
    where, params = ["r.deleted=0"], []
    if module in rec.MODULES:
        where.append("r.module=?"); params.append(module)
    if status in rec.STATUS_LABELS:
        where.append("r.status=?"); params.append(status)
    rows = db.execute(
        """SELECT r.*, o.full_name AS owner_name, v.full_name AS reviewer_name
           FROM compliance_records r LEFT JOIN users o ON o.id=r.owner_id LEFT JOIN users v ON v.id=r.reviewer_id
           WHERE %s ORDER BY r.period_year DESC, r.module, r.period_label DESC""" % " AND ".join(where),
        params).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["state"], d["state_label"] = rec.due_state(d)
        d["title"] = rec.module_title(d["module"])
        items.append(d)
    return render_template("compliance_list.html", items=items, modules=rec.MODULES, statuses=rec.STATUS_LABELS,
                           f_module=module, f_status=status, users=active_users(db),
                           current_year=date.today().year)


@app.route("/compliance/new", methods=["POST"])
@login_required
def compliance_new():
    db = get_db()
    f = request.form
    module = f.get("module", "")
    if module == "gbs":
        flash("GBS claims are opened from the GBS page (one per quarter).", "info")
        return redirect(url_for("gbs"))
    year = _int_or_none(f.get("period_year")) or date.today().year
    if not 2000 <= year <= 2100:
        flash("Enter a sensible year.", "danger")
        return redirect(url_for("compliance_list"))
    label, start, end, due = default_period(module, year, db)
    rs = None
    if module == "ee":
        rs = get_rule_set(db, "EE-ACT-2025", f.get("period_end") or end)
    elif module == "coida":
        rs = get_rule_set(db, "COIDA-ROE", f.get("period_end") or end)
    try:
        rid = rec.create_record(
            db, module, (f.get("period_label") or label).strip(), g.user, period_year=year,
            period_start=f.get("period_start") or start, period_end=f.get("period_end") or end,
            due_date=f.get("due_date") or due, owner_id=_int_or_none(f.get("owner_id")),
            reviewer_id=_int_or_none(f.get("reviewer_id")), rule_set_id=rs["id"] if rs else None,
            official_source=f.get("official_source") or None)
        log_action("record_create", "compliance", "Opened %s %s" % (rec.module_title(module), f.get("period_label") or label),
                   entity="compliance_record", entity_id=rid, commit=False)
        db.commit()
    except rec.WorkflowError as e:
        db.rollback()
        flash(str(e), "danger")
        return redirect(url_for("compliance_list"))
    except sqlite3.IntegrityError:
        db.rollback()
        flash("A record for that module and period already exists.", "danger")
        return redirect(url_for("compliance_list"))
    flash("Compliance record opened.", "success")
    return redirect(url_for("record_detail", record_id=rid))


@app.route("/compliance/<int:record_id>")
@login_required
def record_detail(record_id):
    import json
    db = get_db()
    r = load_record_or_404(db, record_id)
    users = active_users(db)
    names = {u["id"]: (u["full_name"] or u["username"]) for u in db.execute("SELECT id, full_name, username FROM users")}
    versions = [dict(v) for v in db.execute(
        "SELECT id, report_type, version, generated_at, generated_by_name, reason, checks_ok, checks_json, file_name, "
        "size, sha256, snapshot_sha256, rule_set_id, company_profile_id FROM report_versions WHERE record_id=? "
        "ORDER BY report_type, version DESC", (record_id,))]
    for v in versions:
        v["checks"] = json.loads(v["checks_json"] or "[]")
        v["label"] = reports_store.REPORT_TYPES[v["report_type"]]["label"]
    latest_ids = {v["id"] for v in rec.latest_versions(db, record_id).values()}
    actions = []
    checks_ok = rec.checks_ok_for(db, r)
    for a, (froms, to, who, needs_comment, label) in rec.TRANSITIONS.items():
        ok, why = rec.can_act(r, a, g.user)
        if ok and checks_ok is False and a in ("prepare", "request_review", "resubmit", "approve", "submit"):
            ok, why = False, "Generate the required reports and make sure their checks pass first."
        if r["status"] in froms:
            actions.append({"action": a, "label": label, "ok": ok, "why": why, "needs_comment": needs_comment})
    rule = rule_set_by_id(db, r["rule_set_id"]) if r["rule_set_id"] else None
    gbs_project_id = None
    if r["module"] == "gbs":
        p = db.execute("SELECT id FROM gbs_projects WHERE record_id=?", (record_id,)).fetchone()
        gbs_project_id = p["id"] if p else None
    return render_template(
        "record.html", r=r, title=rec.module_title(r["module"]), module=rec.MODULES[r["module"]],
        status_labels=rec.STATUS_LABELS, editable=r["status"] in rec.EDITABLE and not r["deleted"],
        state=rec.due_state(r), users=users, names=names, versions=versions, latest_ids=latest_ids,
        report_types={k: reports_store.REPORT_TYPES[k] for k in rec.MODULES[r["module"]]["reports"]},
        actions=actions, checks_ok=checks_ok,
        submissions=db.execute("SELECT * FROM submissions WHERE record_id=? ORDER BY id DESC", (record_id,)).fetchall(),
        evidence=db.execute("SELECT * FROM evidence_files WHERE record_id=? ORDER BY id DESC", (record_id,)).fetchall(),
        evidence_kinds=EVIDENCE_KINDS, evidence_exts=sorted(EVIDENCE_TYPES),
        timeline=rec.timeline(db, record_id), cl=checklist_bundle(db, r), rule=rule,
        gbs_project_id=gbs_project_id, today=date.today().isoformat(),
    )


@app.route("/compliance/<int:record_id>/details", methods=["POST"])
@login_required
def record_details(record_id):
    db = get_db()
    r = load_record_or_404(db, record_id)
    f = request.form
    fields = {k: (f.get(k) or None) for k in ("due_date", "notes", "official_source", "last_reviewed_at",
                                               "period_start", "period_end") if k in f}
    for k in ("owner_id", "reviewer_id"):
        if k in f:
            fields[k] = _int_or_none(f.get(k))
            if fields[k] and not db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (fields[k],)).fetchone():
                flash("Choose an active user.", "danger")
                return redirect(url_for("record_detail", record_id=record_id))
    try:
        changes = rec.update_details(db, r, g.user, _int_or_none(f.get("row_version")) or -1, **fields)
        if changes:
            log_action("record_update", "compliance", "Updated %s %s: %s" % (
                rec.module_title(r["module"]), r["period_label"], ", ".join(sorted(changes))),
                entity="compliance_record", entity_id=record_id,
                before={k: r.get(k) for k in changes}, after=changes, commit=False)
        db.commit()
        flash("Saved." if changes else "No changes.", "success" if changes else "info")
    except rec.WorkflowError as e:
        db.rollback()
        flash(str(e), "danger")
    return redirect(url_for("record_detail", record_id=record_id))


@app.route("/compliance/<int:record_id>/transition", methods=["POST"])
@login_required
def record_transition(record_id):
    db = get_db()
    r = load_record_or_404(db, record_id)
    f = request.form
    action = f.get("action", "")
    try:
        payload = None
        if action == "submit":
            submitted_on = (f.get("submitted_on") or "").strip()
            try:
                if date.fromisoformat(submitted_on) > date.today():
                    raise rec.WorkflowError("Submission date cannot be in the future.")
            except ValueError:
                raise rec.WorkflowError("Enter the date the submission was made.")
            ref = (f.get("reference_no") or "").strip()[:100]
            if not ref:
                raise rec.WorkflowError("Enter the submission reference number.")
            latest = rec.latest_versions(db, record_id)
            cl = checklist_bundle(db, r)
            payload = {"report_versions": {k: {"id": v["id"], "version": v["version"], "sha256": v["sha256"]}
                                           for k, v in latest.items()},
                       "checklist": ({"done": cl["done"], "total": cl["total"],
                                      "states": cl["states"]} if cl else None)}
        to = rec.transition(db, record_id, action, g.user, comment=f.get("comment"),
                            checks_ok=rec.checks_ok_for(db, r), override_reason=f.get("override_reason"),
                            payload=payload, expected_version=_int_or_none(f.get("row_version")))
        if action == "submit":
            db.execute(
                """INSERT INTO submissions (record_id, report_version_ids, submitted_on, submitted_by_name,
                       reference_no, confirmation_no, notes, recorded_by) VALUES (?,?,?,?,?,?,?,?)""",
                (record_id, ",".join(str(v["id"]) for v in payload["report_versions"].values()), submitted_on,
                 (f.get("submitted_by_name") or g.user["full_name"] or g.user["username"])[:120], ref,
                 (f.get("confirmation_no") or "").strip()[:100] or None, (f.get("notes") or "").strip()[:2000] or None,
                 g.user["id"]))
        if r["module"] == "gbs":  # keep the GBS project list status in step with the workflow
            db.execute("UPDATE gbs_projects SET status=?, submitted_at=?, updated_at=datetime('now') WHERE record_id=?",
                       ("Submitted" if to in rec.DONE else "In Progress",
                        datetime.now().isoformat(sep=" ", timespec="seconds") if to == "SUBMITTED" else None,
                        record_id))
        log_action("record_" + action, "compliance", "%s %s: %s → %s" % (
            rec.module_title(r["module"]), r["period_label"], rec.STATUS_LABELS[r["status"]], rec.STATUS_LABELS[to]),
            entity="compliance_record", entity_id=record_id, before={"status": r["status"]}, after={"status": to},
            commit=False)
        db.commit()
        flash("%s %s is now %s." % (rec.module_title(r["module"]), r["period_label"], rec.STATUS_LABELS[to]), "success")
    except rec.WorkflowError as e:
        db.rollback()
        log_action("record_action_refused", "compliance", "Refused '%s' on %s %s: %s" % (
            action, rec.module_title(r["module"]), r["period_label"], e), entity="compliance_record",
            entity_id=record_id)
        flash(str(e), "danger")
    return redirect(url_for("record_detail", record_id=record_id))


@app.route("/compliance/<int:record_id>/generate", methods=["POST"])
@login_required
def record_generate(record_id):
    db = get_db()
    r = load_record_or_404(db, record_id)
    report_type = request.form.get("report_type", "")
    if r["deleted"] or r["status"] not in rec.EDITABLE:
        flash("Reports can only be generated while the record is Draft, Prepared or Requires changes. "
              "Submitted reports are frozen.", "danger")
        return redirect(url_for("record_detail", record_id=record_id))
    try:
        vid, version, checks = reports_store.generate_version(db, DATA_DIR, r, report_type, g.user,
                                                              request.form.get("reason"))
        rec.add_event(db, record_id, "report_generated", g.user,
                      comment="%s version %d%s" % (reports_store.REPORT_TYPES[report_type]["label"], version,
                                                    "" if all(c["ok"] for c in checks) else " — CHECKS FAILED"),
                      payload={"report_version_id": vid, "reason": request.form.get("reason")})
        log_action("report_generate", "report", "Generated %s v%d for %s %s" % (
            report_type, version, rec.module_title(r["module"]), r["period_label"]),
            entity="report_version", entity_id=vid, commit=False)
        db.commit()
    except reports_store.ReportError as e:
        db.rollback()
        flash(str(e), "danger")
        return redirect(url_for("record_detail", record_id=record_id))
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Someone generated the same report at the same moment. Nothing was overwritten — refresh to see "
              "the latest version.", "warning")
        return redirect(url_for("record_detail", record_id=record_id) + "#reports")
    failed = [c for c in checks if not c["ok"]]
    if failed:
        flash("Version %d generated, but %d check(s) failed: %s. It cannot be approved until fixed."
              % (version, len(failed), "; ".join(c["check"] for c in failed[:3])), "warning")
    else:
        flash("Version %d generated. All checks passed." % version, "success")
    return redirect(url_for("record_detail", record_id=record_id) + "#reports")


@app.route("/reports/<int:version_id>/download")
@login_required
def report_download(version_id):
    db = get_db()
    v = reports_store.get_version(db, version_id)
    if v is None:
        abort(404)
    try:
        data = reports_store.read_file(DATA_DIR, v)
    except (OSError, reports_store.ReportError) as e:
        log.error("Report file problem for version %s: %s", version_id, e)
        flash("The stored file for this report is missing or damaged. Restore it from backup.", "danger")
        return redirect(url_for("record_detail", record_id=v["record_id"]))
    log_action("report_download", "report", "Downloaded %s" % v["file_name"], entity="report_version",
               entity_id=version_id)
    return send_file(io.BytesIO(data), mimetype=v["mime"], as_attachment=True, download_name=v["file_name"])


def _content_signature(mime, data):
    """Comparable content of a generated file (values, not zip timestamps)."""
    if mime == reports_store.XLSX:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data))
        return [(ws.title, [[c.value for c in row] for row in ws.iter_rows()]) for ws in wb.worksheets]
    if mime == reports_store.DOCX:
        from docx import Document
        doc = Document(io.BytesIO(data))
        return ([p.text for p in doc.paragraphs],
                [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables])
    return data


@app.route("/reports/<int:version_id>/verify", methods=["POST"])
@login_required
def report_verify(version_id):
    db = get_db()
    v = reports_store.get_version(db, version_id)
    if v is None:
        abort(404)
    try:
        stored = reports_store.read_file(DATA_DIR, v)
        again = reports_store.rerender(db, v)
        same = _content_signature(v["mime"], stored) == _content_signature(v["mime"], again)
    except (OSError, reports_store.ReportError) as e:
        flash("Verification failed: %s" % e, "danger")
        same = None
    if same:
        flash("Verified: %s re-created from its stored source data matches the stored file, and the file's "
              "fingerprint is intact." % v["file_name"], "success")
    elif same is False:
        flash("MISMATCH: re-creating %s from its stored data does not match the stored file." % v["file_name"], "danger")
    log_action("report_verify", "report", "Verified %s: %s" % (v["file_name"], {True: "match", False: "MISMATCH", None: "error"}[same]),
               entity="report_version", entity_id=version_id)
    return redirect(url_for("record_detail", record_id=v["record_id"]) + "#reports")


@app.route("/compliance/<int:record_id>/evidence", methods=["POST"])
@login_required
def record_evidence(record_id):
    import hashlib
    import uuid
    db = get_db()
    r = load_record_or_404(db, record_id)
    if r["deleted"] or r["status"] == "ARCHIVED":
        flash("Evidence cannot be added to an archived or deleted record.", "danger")
        return redirect(url_for("record_detail", record_id=record_id))
    f = request.files.get("file")
    kind = request.form.get("kind", "")
    if kind not in EVIDENCE_KINDS:
        flash("Choose what kind of evidence this is.", "danger")
        return redirect(url_for("record_detail", record_id=record_id) + "#evidence")
    if not f or not f.filename:
        flash("Choose a file.", "danger")
        return redirect(url_for("record_detail", record_id=record_id) + "#evidence")
    original = os.path.basename(f.filename.replace("\\", "/"))[:200]
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    data = f.read()
    spec = EVIDENCE_TYPES.get(ext)
    if spec is None or not data or not spec[1](data):
        log_action("evidence_rejected", "compliance", "Rejected upload '%s' (type/content not allowed)" % original,
                   entity="compliance_record", entity_id=record_id)
        flash("That file type is not allowed, or the content does not match its extension. Allowed: %s."
              % ", ".join(sorted(EVIDENCE_TYPES)), "danger")
        return redirect(url_for("record_detail", record_id=record_id) + "#evidence")
    folder = os.path.join(DATA_DIR, "evidence")
    os.makedirs(folder, exist_ok=True)
    stored = "%s.%s" % (uuid.uuid4().hex, ext)
    with open(os.path.join(folder, stored), "wb") as fh:
        fh.write(data)
    try:
        cur = db.execute(
            """INSERT INTO evidence_files (record_id, kind, description, original_name, stored_name, mime, size,
                   sha256, uploaded_by) VALUES (?,?,?,?,?,?,?,?,?)""",
            (record_id, kind, (request.form.get("description") or "").strip()[:300] or None, original, stored,
             spec[0], len(data), hashlib.sha256(data).hexdigest(), g.user["id"]))
        rec.add_event(db, record_id, "evidence_added", g.user, comment="%s: %s" % (kind, original),
                      payload={"evidence_id": cur.lastrowid})
        log_action("evidence_upload", "compliance", "Added evidence '%s' to %s %s" % (
            original, rec.module_title(r["module"]), r["period_label"]), entity="evidence", entity_id=cur.lastrowid,
            commit=False)
        db.commit()
    except Exception:
        db.rollback()
        os.remove(os.path.join(folder, stored))
        raise
    flash("Evidence added.", "success")
    return redirect(url_for("record_detail", record_id=record_id) + "#evidence")


@app.route("/evidence/<int:evidence_id>")
@login_required
def evidence_download(evidence_id):
    import hashlib
    db = get_db()
    e = db.execute("SELECT * FROM evidence_files WHERE id=?", (evidence_id,)).fetchone()
    if e is None:
        abort(404)
    path = os.path.join(DATA_DIR, "evidence", os.path.basename(e["stored_name"]))
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        flash("The evidence file is missing. Restore it from backup.", "danger")
        return redirect(url_for("record_detail", record_id=e["record_id"]))
    if hashlib.sha256(data).hexdigest() != e["sha256"]:
        flash("The evidence file does not match its recorded fingerprint. Restore it from backup.", "danger")
        return redirect(url_for("record_detail", record_id=e["record_id"]))
    log_action("evidence_download", "compliance", "Downloaded evidence '%s'" % e["original_name"],
               entity="evidence", entity_id=evidence_id)
    return send_file(io.BytesIO(data), mimetype=e["mime"], as_attachment=True, download_name=e["original_name"])


# ─── Company profile ─────────────────────────────────────────────────────────

@app.route("/company", methods=["GET", "POST"])
@admin_required
def company():
    db = get_db()
    if request.method == "POST":
        try:
            pid, diff = save_profile(db, {k: request.form.get(k) for k, _l, _g in COMPANY_FIELDS if k in request.form},
                                     g.user["id"])
            if diff:
                log_action("company_update", "company", "Company profile updated (version %d): %s"
                           % (pid, ", ".join(sorted(diff))), entity="company_profile", entity_id=pid,
                           before={k: v[0] for k, v in diff.items()}, after={k: v[1] for k, v in diff.items()},
                           commit=False)
            db.commit()
            flash("Company profile saved as a new version. Reports already generated keep the version they used."
                  if diff else "No changes.", "success" if diff else "info")
        except ProfileError as e:
            db.rollback()
            flash(str(e), "danger")
        return redirect(url_for("company"))
    history = db.execute("""SELECT p.id, p.created_at, u.full_name AS by_name FROM company_profiles p
                            LEFT JOIN users u ON u.id=p.created_by ORDER BY p.id DESC LIMIT 20""").fetchall()
    groups = []
    for col, label, grp in COMPANY_FIELDS:
        if not groups or groups[-1][0] != grp:
            groups.append((grp, []))
        groups[-1][1].append((col, label))
    return render_template("company.html", profile=current_profile(db), groups=groups, history=history,
                           setas=SETAS)


# ─── Regulatory rules register ───────────────────────────────────────────────

@app.route("/rules")
@admin_required
def rules_register():
    import json
    db = get_db()
    sets = all_rule_sets(db)
    for s in sets:
        s["params_pretty"] = json.dumps(s["params"], indent=2, ensure_ascii=False)
        s["used"] = db.execute("SELECT COUNT(*) FROM report_versions WHERE rule_set_id=?", (s["id"],)).fetchone()[0]
    return render_template("rules.html", sets=sets, today=date.today().isoformat())


@app.route("/rules/<int:rule_id>/review", methods=["POST"])
@admin_required
def rules_review(rule_id):
    db = get_db()
    rs = rule_set_by_id(db, rule_id)
    if rs is None:
        abort(404)
    f = request.form
    vals = {}
    for k in ("last_reviewed", "next_review", "verified_at"):
        v = (f.get(k) or "").strip() or None
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                flash("Invalid date.", "danger")
                return redirect(url_for("rules_register"))
        vals[k] = v
    vals["verified_by"] = (f.get("verified_by") or "").strip()[:120] or None
    if vals["verified_at"] and not vals["verified_by"]:
        flash("Record who verified the rule set.", "danger")
        return redirect(url_for("rules_register"))
    vals["notes"] = (f.get("notes") or rs["notes"] or "").strip()[:4000]
    db.execute("UPDATE rule_sets SET last_reviewed=?, next_review=?, verified_at=?, verified_by=?, notes=? WHERE id=?",
               (vals["last_reviewed"], vals["next_review"], vals["verified_at"], vals["verified_by"], vals["notes"],
                rule_id))
    log_action("rule_review", "rules", "Reviewed rule set %s v%s%s" % (
        rs["code"], rs["version"], " — VERIFIED by %s" % vals["verified_by"] if vals["verified_at"] else ""),
        entity="rule_set", entity_id=rule_id,
        before={k: rs.get(k) for k in vals}, after=vals, commit=False)
    db.commit()
    flash("Rule set review saved.", "success")
    return redirect(url_for("rules_register"))


@app.route("/rules/<int:rule_id>/new-version", methods=["POST"])
@admin_required
def rules_new_version(rule_id):
    import json
    db = get_db()
    rs = rule_set_by_id(db, rule_id)
    if rs is None:
        abort(404)
    f = request.form
    version = (f.get("version") or "").strip()[:30]
    eff = (f.get("effective_from") or "").strip()
    try:
        date.fromisoformat(eff)
        params = json.loads(f.get("params_json") or "")
        if not isinstance(params, dict):
            raise ValueError
    except ValueError:
        flash("Give a valid effective date and valid JSON parameters.", "danger")
        return redirect(url_for("rules_register"))
    if not version:
        flash("Give the new version a name.", "danger")
        return redirect(url_for("rules_register"))
    try:
        cur = db.execute(
            """INSERT INTO rule_sets (module, code, version, regulator, source_document, source_url, effective_from,
                   params_json, notes) VALUES (?,?,?,?,?,?,?,?,?)""",
            (rs["module"], rs["code"], version, (f.get("regulator") or rs["regulator"]),
             (f.get("source_document") or rs["source_document"]), (f.get("source_url") or rs["source_url"]),
             eff, json.dumps(params), (f.get("notes") or "").strip() or None))
        db.execute("UPDATE rule_sets SET superseded_by=? WHERE id=?", (cur.lastrowid, rule_id))
        log_action("rule_new_version", "rules", "Added %s version %s effective %s (supersedes v%s)" % (
            rs["code"], version, eff, rs["version"]), entity="rule_set", entity_id=cur.lastrowid,
            before={"params": rs["params"]}, after={"params": params}, commit=False)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("That version name already exists for %s." % rs["code"], "danger")
        return redirect(url_for("rules_register"))
    flash("New rule-set version added. Periods ending on or after %s will use it for new reports; existing "
          "reports keep the version they were generated with." % eff, "success")
    return redirect(url_for("rules_register"))


# ─── Employees ───────────────────────────────────────────────────────────────

EMP_SORTS = {"name": "last_name, first_name", "number": "emp_number", "level": "occupational_level, last_name",
             "start": "employment_date DESC", "status": "status, last_name"}
EMP_AUDIT_FIELDS = ["emp_number", "first_name", "last_name", "id_number", "race", "gender", "disability",
                    "is_foreign_national", "nationality", "occupational_level", "employment_type", "department",
                    "job_title", "salary", "employment_date", "termination_date", "province", "coida_class_code",
                    "coida_subclass", "status"]


def _emp_form_raw(form):
    return {k: form.get(k) for k in ("emp_number", "first_name", "last_name", "id_number", "race", "gender",
                                     "disability", "is_foreign_national", "nationality", "occupational_level",
                                     "employment_type", "department", "job_title", "salary", "employment_date",
                                     "termination_date", "province", "coida_class_code", "coida_subclass")}


def _employee_page(db, edit_emp=None, form_values=None, errors=None):
    q = (request.args.get("q") or "").strip()[:100]
    status = request.args.get("status", "ACTIVE")
    level = request.args.get("level", "")
    sort = request.args.get("sort", "name")
    page = max(1, _int_or_none(request.args.get("page")) or 1)
    per = 50
    where, params = [], []
    if status in ("ACTIVE", "TERMINATED", "ARCHIVED"):
        where.append("status=?"); params.append(status)
    if level in OCC_LEVELS:
        where.append("occupational_level=?"); params.append(level)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(first_name LIKE ? ESCAPE '\\' OR last_name LIKE ? ESCAPE '\\' OR emp_number LIKE ? ESCAPE '\\' "
                     "OR department LIKE ? ESCAPE '\\' OR job_title LIKE ? ESCAPE '\\')")
        params += [like] * 5
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.execute("SELECT COUNT(*) FROM employees" + wsql, params).fetchone()[0]
    rows = db.execute("SELECT * FROM employees%s ORDER BY %s LIMIT ? OFFSET ?" % (wsql, EMP_SORTS.get(sort, EMP_SORTS["name"])),
                      params + [per, (page - 1) * per]).fetchall()
    counts = {s: c for s, c in db.execute("SELECT status, COUNT(*) FROM employees GROUP BY status")}
    return render_template(
        "employees.html", employees=rows, total=total, page=page, pages=max(1, (total + per - 1) // per),
        q=q, f_status=status, f_level=level, sort=sort, counts=counts, edit_emp=edit_emp,
        form_values=form_values or {}, form_errors=errors or [],
        races=RACES, genders=GENDERS, occ_levels=OCC_LEVELS, provinces=PROVINCES, employment_types=EMPLOYMENT_TYPES,
        import_result=session.pop("import_result", None),
    )


@app.route("/employees", methods=["GET", "POST"])
@login_required
def employees():
    db = get_db()
    if request.method == "POST":
        clean, errors, warnings = validate_employee(_emp_form_raw(request.form))
        if not errors and db.execute("SELECT 1 FROM employees WHERE emp_number=?", (clean["emp_number"],)).fetchone():
            errors.append("Employee number %s already exists." % clean["emp_number"])
        if errors:
            for e in errors:
                flash(e, "danger")
            return _employee_page(db, form_values=request.form, errors=errors), 400
        emp_id = insert_employee(db, clean, g.user["id"])
        log_action("employee_create", "employee", "Added employee %s" % clean["emp_number"], entity="employee",
                   entity_id=emp_id, after={k: clean.get(k) for k in EMP_AUDIT_FIELDS if k in clean}, commit=False)
        db.commit()
        for w in warnings:
            flash(w, "warning")
        flash("Employee added.", "success")
        return redirect(url_for("employees"))
    return _employee_page(db)


@app.route("/employees/upload", methods=["POST"])
@login_required
def employees_upload():
    import uuid
    db = get_db()
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("employees"))
    name = os.path.basename(f.filename.replace("\\", "/"))
    try:
        result = plan_import(db, name, f.read())
    except ImportFileError as e:
        log_action("employee_import_rejected", "employee", "Rejected import file '%s': %s" % (name[:80], e))
        flash("Import refused: %s Nothing was imported." % e, "danger")
        return redirect(url_for("employees"))
    summary = {k: result[k] for k in ("processed", "valid", "failed", "duplicates")}
    summary["warnings"] = len(result["warnings"])
    summary["unknown_columns"] = result["unknown_columns"][:10]
    if result["problems"] or result["warnings"]:
        folder = os.path.join(DATA_DIR, "import_reports")
        os.makedirs(folder, exist_ok=True)
        for old in os.listdir(folder):  # keep this folder small: reports older than a day go
            p = os.path.join(folder, old)
            if os.path.getmtime(p) < datetime.now().timestamp() - 86400:
                os.remove(p)
        token = uuid.uuid4().hex
        with open(os.path.join(folder, token + ".csv"), "wb") as fh:
            fh.write(error_report_csv(result))
        summary["report_token"] = token
        session["import_report_token"] = token
        summary["problems"] = [{"row": p["row"], "emp_number": p["emp_number"], "errors": p["errors"][:3]}
                               for p in result["problems"][:15]]
    if not result["ok"]:
        summary["imported"] = 0
        log_action("employee_import_failed", "employee", "Import of '%s' refused: %d of %d rows invalid" % (
            name[:80], result["failed"], result["processed"]), after=summary)
        session["import_result"] = summary
        flash("Import refused: %d of %d rows have problems. Nothing was imported. Fix the rows listed below and "
              "upload again." % (result["failed"], result["processed"]), "danger")
        return redirect(url_for("employees"))
    try:
        for clean in result["rows"]:
            insert_employee(db, clean, g.user["id"])
        summary["imported"] = len(result["rows"])
        log_action("employee_import", "employee", "Imported %d employees from '%s'" % (len(result["rows"]), name[:80]),
                   after=summary, commit=False)
        db.commit()
    except sqlite3.Error:
        db.rollback()
        log.exception("Employee import failed during insert")
        flash("Import failed while saving and was rolled back. Nothing was imported.", "danger")
        return redirect(url_for("employees"))
    session["import_result"] = summary
    flash("%d employee(s) imported." % len(result["rows"]), "success")
    return redirect(url_for("employees"))


@app.route("/employees/import-report/<token>")
@login_required
def employees_import_report(token):
    if token != session.get("import_report_token") or not token.isalnum():
        abort(404)
    path = os.path.join(DATA_DIR, "import_reports", token + ".csv")
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="text/csv", as_attachment=True, download_name="import_problems.csv")


@app.route("/employees/<int:emp_id>/edit", methods=["GET", "POST"])
@login_required
def edit_employee(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp is None:
        flash("Employee not found.", "danger")
        return redirect(url_for("employees"))
    if request.method == "POST":
        if emp["status"] == "ARCHIVED":
            flash("Archived employees cannot be edited. Reactivate first.", "danger")
            return redirect(url_for("edit_employee", emp_id=emp_id))
        raw = _emp_form_raw(request.form)
        if emp["status"] == "TERMINATED" and not raw.get("termination_date"):
            raw["termination_date"] = emp["termination_date"]
        clean, errors, warnings = validate_employee(raw)
        if not errors and clean["emp_number"] != emp["emp_number"] and db.execute(
                "SELECT 1 FROM employees WHERE emp_number=? AND id<>?", (clean["emp_number"], emp_id)).fetchone():
            errors.append("Employee number %s already exists." % clean["emp_number"])
        if errors:
            for e in errors:
                flash(e, "danger")
            return _employee_page(db, edit_emp=emp, form_values=request.form, errors=errors), 400
        clean["status"] = "TERMINATED" if clean.get("termination_date") else "ACTIVE"
        before = {k: emp[k] for k in EMP_AUDIT_FIELDS}
        changed = {k: clean[k] for k in clean if k in before and clean[k] != before[k]}
        if not changed:
            flash("No changes.", "info")
            return redirect(url_for("employees"))
        sets = ", ".join("%s=?" % k for k in changed)
        cur = db.execute("UPDATE employees SET %s, updated_at=datetime('now'), updated_by=?, row_version=row_version+1 "
                         "WHERE id=? AND row_version=?" % sets,
                         list(changed.values()) + [g.user["id"], emp_id, _int_or_none(request.form.get("row_version")) or -1])
        if cur.rowcount != 1:
            db.rollback()
            flash("Someone else changed this employee while you were editing. Your changes were NOT saved — "
                  "reload and apply them again.", "danger")
            return redirect(url_for("edit_employee", emp_id=emp_id))
        log_action("employee_update", "employee", "Edited employee %s: %s" % (emp["emp_number"], ", ".join(sorted(changed))),
                   entity="employee", entity_id=emp_id, before={k: before[k] for k in changed}, after=changed,
                   commit=False)
        db.commit()
        for w in warnings:
            flash(w, "warning")
        flash("Employee updated.", "success")
        return redirect(url_for("employees"))
    return _employee_page(db, edit_emp=emp)


@app.route("/employees/<int:emp_id>/lifecycle", methods=["POST"])
@login_required
def employee_lifecycle(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp is None:
        abort(404)
    action = request.form.get("action")
    rv = _int_or_none(request.form.get("row_version")) or -1
    if action == "terminate":
        if emp["status"] != "ACTIVE":
            flash("Only active employees can be terminated.", "danger")
            return redirect(url_for("employees"))
        d = (request.form.get("termination_date") or "").strip()
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            flash("Enter the termination date.", "danger")
            return redirect(url_for("edit_employee", emp_id=emp_id))
        if dd > date.today() or (emp["employment_date"] and d < emp["employment_date"]):
            flash("Termination date must be between the employment date and today.", "danger")
            return redirect(url_for("edit_employee", emp_id=emp_id))
        new = {"status": "TERMINATED", "termination_date": d,
               "termination_reason": (request.form.get("termination_reason") or "").strip()[:200] or None}
    elif action == "archive":
        if emp["status"] != "TERMINATED":
            flash("Terminate the employee before archiving.", "danger")
            return redirect(url_for("employees"))
        new = {"status": "ARCHIVED", "archived_at": datetime.now().isoformat(sep=" ", timespec="seconds")}
    elif action == "reactivate":
        if emp["status"] == "ACTIVE":
            return redirect(url_for("employees"))
        new = {"status": "ACTIVE", "termination_date": None, "termination_reason": None, "archived_at": None}
    else:
        abort(400)
    sets = ", ".join("%s=?" % k for k in new)
    cur = db.execute("UPDATE employees SET %s, updated_at=datetime('now'), updated_by=?, row_version=row_version+1 "
                     "WHERE id=? AND row_version=?" % sets, list(new.values()) + [g.user["id"], emp_id, rv])
    if cur.rowcount != 1:
        db.rollback()
        flash("This employee was changed by someone else. Reload and try again.", "danger")
        return redirect(url_for("employees"))
    log_action("employee_" + action, "employee", "%s employee %s" % (action.capitalize(), emp["emp_number"]),
               entity="employee", entity_id=emp_id, before={k: emp[k] for k in new}, after=new, commit=False)
    db.commit()
    flash("Employee %s %s." % (emp["emp_number"], {"terminate": "terminated", "archive": "archived",
                                                     "reactivate": "reactivated"}[action]), "success")
    return redirect(url_for("employees", status=new["status"]))


@app.route("/employees/<int:emp_id>/export")
@admin_required
def employee_export(emp_id):
    """Everything the system holds about one employee (POPIA access request):
    register data, training, COIDA earnings, the stored report versions whose frozen
    data includes them, and the audit trail of changes to their record."""
    import json
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp is None:
        abort(404)
    pattern = '%"id":' + str(emp_id) + ',%'
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "employee": dict(emp),
        "training_records": [dict(r) for r in db.execute("SELECT * FROM training_records WHERE employee_id=?", (emp_id,))],
        "coida_earnings": [dict(r) for r in db.execute("SELECT * FROM coida_earnings WHERE employee_id=?", (emp_id,))],
        "stored_report_versions_containing_employee": [
            dict(r) for r in db.execute(
                """SELECT rv.id, rv.report_type, rv.version, rv.generated_at, r.module, r.period_label
                   FROM report_versions rv JOIN compliance_records r ON r.id=rv.record_id
                   WHERE rv.snapshot_json LIKE ? AND rv.report_type IN ('eea2','eea4','was8','wsp','atr','wsp_docx')""",
                (pattern,))],
        "audit_trail": [dict(r) for r in db.execute(
            "SELECT ts, username, action, detail, before_json, after_json FROM audit_log "
            "WHERE entity='employee' AND entity_id=? ORDER BY id", (str(emp_id),))],
        "note": "Stored report versions are statutory records and are retained under the applicable retention "
                "periods even if the employee is deleted from the register.",
    }
    log_action("employee_export", "employee", "Exported all data held for employee %s (access request)" % emp["emp_number"],
               entity="employee", entity_id=emp_id)
    data = json.dumps(out, indent=2, default=str, ensure_ascii=False).encode("utf-8")
    return send_file(io.BytesIO(data), mimetype="application/json", as_attachment=True,
                     download_name="employee_%s_data.json" % "".join(c for c in (emp["emp_number"] or str(emp_id)) if c.isalnum()))


@app.route("/employees/<int:emp_id>/delete", methods=["POST"])
@admin_required
def delete_employee(emp_id):
    """Permanent deletion — administrators only, archived employees only, and refused
    while any training/COIDA record still refers to the employee. Stored report
    snapshots are unaffected either way."""
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if emp is None:
        abort(404)
    if emp["status"] != "ARCHIVED":
        flash("Only archived employees can be permanently deleted. Terminate and archive first.", "danger")
        return redirect(url_for("employees"))
    if (request.form.get("confirm_number") or "").strip() != (emp["emp_number"] or ""):
        flash("Type the employee number to confirm permanent deletion.", "danger")
        return redirect(url_for("employees", status="ARCHIVED"))
    try:
        db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
        log_action("employee_delete", "employee", "Permanently deleted archived employee %s" % emp["emp_number"],
                   entity="employee", entity_id=emp_id, before={"emp_number": emp["emp_number"], "status": emp["status"]},
                   commit=False)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("Employee %s is referenced by training or COIDA records and cannot be deleted. It stays archived."
              % emp["emp_number"], "warning")
        return redirect(url_for("employees", status="ARCHIVED"))
    flash("Employee permanently deleted.", "warning")
    return redirect(url_for("employees", status="ARCHIVED"))


@app.route("/employees/template")
@login_required
def employee_template():
    return send_file(io.BytesIO(template_csv()), mimetype="text/csv", as_attachment=True,
                     download_name="employee_import_template.csv")


# ─── EE — Employment Equity ──────────────────────────────────────────────────

def _module_records(db, module):
    return db.execute("SELECT * FROM compliance_records WHERE module=? AND deleted=0 ORDER BY period_year DESC, id DESC",
                      (module,)).fetchall()


def _selected_record(db, module):
    rows = _module_records(db, module)
    rid = _int_or_none(request.args.get("record"))
    sel = next((dict(r) for r in rows if r["id"] == rid), None) if rid else None
    if sel is None:
        sel = next((dict(r) for r in rows if r["status"] not in rec.DONE), None) or (dict(rows[0]) if rows else None)
    return rows, sel


@app.route("/ee")
@login_required
def ee():
    from modules import ee as eecalc
    db = get_db()
    rows, sel = _selected_record(db, "ee")
    analysis = matrix = checks = None
    if sel:
        snap, _company, _rs = reports_store.build_snapshot(db, sel, "eea2")
        analysis = eecalc.analyse(snap)
        if analysis.get("levels"):
            e2, e4 = eecalc.eea2_data(analysis), eecalc.eea4_data(analysis)
            checks = eecalc.reconcile(analysis, e2, e4)
            matrix = {"columns": e2["columns"], "rows": {lvl: {c: e2["permanent"][lvl][c] + e2["temporary"][lvl][c]
                                                              for c in e2["columns"]} for lvl in analysis["levels"]}}
    return render_template("ee.html", records=rows, sel=sel, analysis=analysis, matrix=matrix, checks=checks,
                           company_info=current_profile(db), status_labels=rec.STATUS_LABELS,
                           cl=checklist_bundle(db, sel) if sel else None,
                           total_active=db.execute("SELECT COUNT(*) FROM employees WHERE status='ACTIVE'").fetchone()[0])


# ─── GBS — Global Business Services Quarterly Claim Projects ─────────────────

QUARTERS = [
    ("Q1", "Q1 — 1 January"),
    ("Q2", "Q2 — 1 April"),
    ("Q3", "Q3 — 1 July"),
    ("Q4", "Q4 — 1 October"),
]


def _gbs_project_progress(db, project):
    from modules.checklists import get_checklist
    checklist = get_checklist("gbs")
    if not checklist or not project["record_id"]:
        return 0, 0, 0
    scope = "record:%d" % project["record_id"]
    return checklist_progress(checklist, get_checklist_states(db, scope), get_custom_tasks(db, scope),
                              get_hidden_tasks(db, scope))


@app.route("/gbs", methods=["GET"])
@login_required
def gbs():
    """Projects dashboard — all (non-trashed) quarterly claims grouped by year."""
    db = get_db()
    purge_expired_projects(db)
    rows = db.execute(
        """SELECT p.*, r.status AS wf_status FROM gbs_projects p LEFT JOIN compliance_records r ON r.id=p.record_id
           WHERE p.trashed=0 ORDER BY p.year DESC, p.quarter DESC, p.id DESC""").fetchall()
    by_year = {}
    for r in rows:
        proj = dict(r)
        proj["cl_done"], proj["cl_total"], proj["cl_pct"] = _gbs_project_progress(db, proj)
        proj["wf_label"] = rec.STATUS_LABELS.get(proj["wf_status"], "")
        by_year.setdefault(proj["year"], []).append(proj)
    quarter_order = {"Q4": 4, "Q3": 3, "Q2": 2, "Q1": 1}
    for yr in by_year:
        by_year[yr].sort(key=lambda p: quarter_order.get(p["quarter"], 0), reverse=True)
    grouped = sorted(by_year.items(), reverse=True)
    trash_count = db.execute("SELECT COUNT(*) AS c FROM gbs_projects WHERE trashed=1").fetchone()["c"]
    now = datetime.now()
    return render_template(
        "gbs_projects.html", grouped=grouped, total_projects=len(rows),
        submitted=sum(1 for r in rows if r["status"] == "Submitted"),
        in_progress=sum(1 for r in rows if r["status"] == "In Progress"),
        drafts=sum(1 for r in rows if r["status"] == "Draft"),
        trash_count=trash_count, current_year=now.year, current_quarter="Q%d" % ((now.month - 1) // 3 + 1),
        quarters=QUARTERS,
    )


@app.route("/gbs/new", methods=["POST"])
@login_required
def gbs_new():
    db = get_db()
    quarter = (request.form.get("quarter") or "").strip()
    year = _int_or_none(request.form.get("year"))
    if quarter not in {q[0] for q in QUARTERS}:
        flash("Please select a valid quarter.", "danger")
        return redirect(url_for("gbs"))
    if not year or not 2000 <= year <= 2100:
        flash("Please provide a valid year.", "danger")
        return redirect(url_for("gbs"))
    label = "%s %d" % (quarter, year)
    try:
        qn = int(quarter[1])
        start = date(year, (qn - 1) * 3 + 1, 1)
        end = (date(year + (qn == 4), (qn * 3) % 12 + 1, 1) - timedelta(days=1))
        rid = rec.create_record(db, "gbs", label, g.user, period_year=year, period_start=start.isoformat(),
                                period_end=end.isoformat(), official_source="https://www.thedtic.gov.za")
        cur = db.execute("INSERT INTO gbs_projects (quarter, year, status, record_id) VALUES (?, ?, 'Draft', ?)",
                         (quarter, year, rid))
        log_action("project_create", "project", f"Created GBS quarterly claim {label}", target=f"gbs:{cur.lastrowid}",
                   entity="compliance_record", entity_id=rid, commit=False)
        db.commit()
    except rec.WorkflowError:
        db.rollback()
        flash(f"A claim for {label} already exists. Open it from the list (or restore it from the trash).", "warning")
        return redirect(url_for("gbs"))
    flash(f"New GBS project created: {label}.", "success")
    return redirect(url_for("gbs_project", project_id=cur.lastrowid))


@app.route("/gbs/<int:project_id>", methods=["GET", "POST"])
@login_required
def gbs_project(project_id):
    db = get_db()
    proj = db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        flash("Project not found.", "danger")
        return redirect(url_for("gbs"))
    if proj["trashed"]:
        flash("That project is in the trash. An administrator must restore it first.", "warning")
        return redirect(url_for("gbs"))
    record = rec.get_record(db, proj["record_id"])
    readonly = record is None or record["status"] not in rec.EDITABLE

    if request.method == "POST":
        if readonly:
            flash("This claim is %s and cannot be edited. It must be sent back for changes first."
                  % rec.STATUS_LABELS.get(record["status"], "locked").lower(), "warning")
            return redirect(url_for("gbs_project", project_id=project_id))
        data = request.form
        errors = []
        nums = {}
        for k in ("num_campaigns", "num_qualifying_employees"):
            v = _int_or_none(data.get(k) or 0)
            if v is None or v < 0:
                errors.append("%s must be a whole number of 0 or more." % k.replace("_", " ").capitalize())
            nums[k] = v
        try:
            rev = float(str(data.get("export_revenue") or 0).replace(",", "").replace(" ", ""))
            if rev < 0 or rev != rev:
                raise ValueError
        except ValueError:
            errors.append("Export revenue must be a positive number.")
            rev = 0
        for k in ("dtic_notification_date", "submission_deadline"):
            if data.get(k):
                try:
                    date.fromisoformat(data[k])
                except ValueError:
                    errors.append("%s is not a valid date." % k.replace("_", " ").capitalize())
        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("gbs_project", project_id=project_id) + "#details")
        before = dict(proj)
        new_status = "In Progress" if proj["status"] == "Draft" else proj["status"]
        db.execute(
            """UPDATE gbs_projects SET company_name=?, dtic_notification_date=?,
               submission_deadline=?, num_campaigns=?, num_qualifying_employees=?,
               export_revenue=?, premises=?, auditor=?, notes=?, status=?,
               updated_at=datetime('now') WHERE id=?""",
            ((data.get("company_name") or "")[:200], data.get("dtic_notification_date") or None,
             data.get("submission_deadline") or None, nums["num_campaigns"], nums["num_qualifying_employees"],
             round(rev, 2), (data.get("premises") or "")[:300], (data.get("auditor") or "")[:200],
             (data.get("notes") or "")[:4000], new_status, project_id),
        )
        if data.get("submission_deadline") and data.get("submission_deadline") != record["due_date"]:
            db.execute("UPDATE compliance_records SET due_date=?, updated_at=datetime('now'), updated_by=?, "
                       "row_version=row_version+1 WHERE id=?", (data["submission_deadline"], g.user["id"], record["id"]))
        after = dict(db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone())
        changed = {k: after[k] for k in after if after[k] != before.get(k) and k not in ("updated_at",)}
        log_action("project_update", "project", f"Updated claim details for GBS {proj['quarter']} {proj['year']}",
                   target=f"gbs:{project_id}", entity="gbs_project", entity_id=project_id,
                   before={k: before.get(k) for k in changed}, after=changed, commit=False)
        db.commit()
        flash("Claim details saved.", "success")
        return redirect(url_for("gbs_project", project_id=project_id))

    cl = checklist_bundle(db, record) if record else None
    return render_template(
        "gbs_project.html", project=proj, record=record, scope=cl["scope"] if cl else "",
        readonly=readonly, checklist=cl["checklist"] if cl else None,
        checklist_states=cl["states"] if cl else {}, custom_tasks=cl["custom"] if cl else {},
        hidden_tasks=cl["hidden"] if cl else set(), cl_done=cl["done"] if cl else 0,
        cl_total=cl["total"] if cl else 0, cl_pct=cl["pct"] if cl else 0, cl_locked=cl["locked"] if cl else True,
        status_labels=rec.STATUS_LABELS, current_year=datetime.now().year,
    )


@app.route("/gbs/<int:project_id>/submit", methods=["POST"])
@login_required
def gbs_project_submit(project_id):
    """Kept for old links: submission now goes through the compliance workflow."""
    db = get_db()
    proj = db.execute("SELECT record_id FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        abort(404)
    flash("Submit the claim from its compliance record: it must be reviewed and approved first.", "info")
    return redirect(url_for("record_detail", record_id=proj["record_id"]))


@app.route("/gbs/<int:project_id>/reopen", methods=["POST"])
@login_required
def gbs_project_reopen(project_id):
    db = get_db()
    proj = db.execute("SELECT record_id FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        abort(404)
    flash("Reopening a submitted claim is an administrator action on the compliance record, with a reason.", "info")
    return redirect(url_for("record_detail", record_id=proj["record_id"]))


@app.route("/gbs/<int:project_id>/delete", methods=["POST"])
@login_required
def gbs_project_delete(project_id):
    """Soft-delete: move the project to the trash bin (recoverable by an admin).
    Submitted claims are compliance evidence and cannot be trashed."""
    db = get_db()
    proj = db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        flash("Project not found.", "danger")
        return redirect(url_for("gbs"))
    record = rec.get_record(db, proj["record_id"])
    if record and record["status"] not in ("DRAFT", "PREPARED", "REQUIRES_CHANGES"):
        flash("A claim that is %s cannot be moved to the trash." % rec.STATUS_LABELS[record["status"]].lower(), "danger")
        return redirect(url_for("gbs_project", project_id=project_id))
    if proj["trashed"]:
        return redirect(url_for("gbs"))
    db.execute(
        """UPDATE gbs_projects SET trashed=1, deleted_at=datetime('now'), deleted_by=?,
           purge_at=NULL, purge_approved_at=NULL, purge_approved_by=NULL,
           updated_at=datetime('now') WHERE id=?""",
        (g.user["username"], project_id),
    )
    if record:
        db.execute("UPDATE compliance_records SET deleted=1, updated_at=datetime('now'), updated_by=?, "
                   "row_version=row_version+1 WHERE id=?", (g.user["id"], record["id"]))
        rec.add_event(db, record["id"], "trashed", g.user, comment="Moved to the trash bin")
    log_action("project_delete", "project", f"Moved GBS {proj['quarter']} {proj['year']} to trash",
               target=f"gbs:{project_id}", commit=False)
    db.commit()
    flash(f"Project {proj['quarter']} {proj['year']} moved to the trash bin. "
          "An administrator can restore it or approve permanent deletion.", "warning")
    return redirect(url_for("gbs"))


# ─── Trash bin (admin only) ──────────────────────────────────────────────────

@app.route("/trash", methods=["GET"])
@admin_required
def trash():
    db = get_db()
    purge_expired_projects(db)

    rows = db.execute(
        "SELECT * FROM gbs_projects WHERE trashed=1 ORDER BY deleted_at DESC"
    ).fetchall()

    awaiting, pending = [], []
    now = datetime.now()
    for r in rows:
        p = dict(r)
        done, total, pct = _gbs_project_progress(db, p)
        p["cl_done"], p["cl_total"], p["cl_pct"] = done, total, pct
        if p["purge_at"]:
            try:
                import math
                purge_dt = datetime.fromisoformat(p["purge_at"])
                secs = (purge_dt - now).total_seconds()
                p["days_left"] = max(0, int(math.ceil(secs / 86400.0)))
                p["purge_date_str"] = purge_dt.strftime("%d %b %Y")
            except ValueError:
                p["days_left"] = 0
                p["purge_date_str"] = p["purge_at"]
            pending.append(p)
        else:
            awaiting.append(p)

    return render_template(
        "trash.html",
        awaiting=awaiting,
        pending=pending,
        hold_days=PURGE_HOLD_DAYS,
    )


@app.route("/trash/<int:project_id>/restore", methods=["POST"])
@admin_required
def trash_restore(project_id):
    db = get_db()
    proj = db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        flash("Project not found.", "danger")
        return redirect(url_for("trash"))
    if proj["record_id"]:
        r = db.execute("SELECT * FROM compliance_records WHERE id=?", (proj["record_id"],)).fetchone()
        if r and db.execute("SELECT 1 FROM compliance_records WHERE module='gbs' AND period_label=? AND deleted=0 "
                            "AND id<>?", (r["period_label"], r["id"])).fetchone():
            flash("Another active claim for %s exists, so this one cannot be restored." % r["period_label"], "danger")
            return redirect(url_for("trash"))
        db.execute("UPDATE compliance_records SET deleted=0, updated_at=datetime('now'), row_version=row_version+1 "
                   "WHERE id=?", (proj["record_id"],))
        db.execute("""INSERT INTO workflow_events (record_id, event, user_id, username, comment)
                      VALUES (?, 'restored', ?, ?, 'Restored from the trash bin')""",
                   (proj["record_id"], g.user["id"], g.user["username"]))
    db.execute(
        """UPDATE gbs_projects SET trashed=0, deleted_at=NULL, deleted_by=NULL,
           purge_at=NULL, purge_approved_at=NULL, purge_approved_by=NULL,
           updated_at=datetime('now') WHERE id=?""",
        (project_id,),
    )
    db.commit()
    log_action("trash_restore", "trash",
               f"Restored GBS {proj['quarter']} {proj['year']} from trash", target=f"gbs:{project_id}")
    flash(f"Project {proj['quarter']} {proj['year']} restored.", "success")
    return redirect(url_for("trash"))


@app.route("/trash/<int:project_id>/approve", methods=["POST"])
@admin_required
def trash_approve(project_id):
    """Admin approves permanent deletion — starts the 30-day hold countdown."""
    db = get_db()
    proj = db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        flash("Project not found.", "danger")
        return redirect(url_for("trash"))
    purge_at = (datetime.now() + timedelta(days=PURGE_HOLD_DAYS)).isoformat(sep=" ", timespec="seconds")
    db.execute(
        """UPDATE gbs_projects SET purge_at=?, purge_approved_at=datetime('now'),
           purge_approved_by=?, updated_at=datetime('now') WHERE id=?""",
        (purge_at, session.get("username", "admin"), project_id),
    )
    db.commit()
    log_action("trash_approve_delete", "trash",
               f"Approved permanent deletion of GBS {proj['quarter']} {proj['year']} — "
               f"{PURGE_HOLD_DAYS}-day hold begins (purges {purge_at[:10]})",
               target=f"gbs:{project_id}")
    flash(
        f"Permanent deletion approved for {proj['quarter']} {proj['year']}. "
        f"It will be purged in {PURGE_HOLD_DAYS} days unless restored.",
        "warning",
    )
    return redirect(url_for("trash"))


@app.route("/trash/<int:project_id>/cancel", methods=["POST"])
@admin_required
def trash_cancel(project_id):
    """Cancel a pending purge — the item stays in trash, countdown removed."""
    db = get_db()
    proj = db.execute("SELECT * FROM gbs_projects WHERE id=?", (project_id,)).fetchone()
    if not proj:
        flash("Project not found.", "danger")
        return redirect(url_for("trash"))
    db.execute(
        """UPDATE gbs_projects SET purge_at=NULL, purge_approved_at=NULL,
           purge_approved_by=NULL, updated_at=datetime('now') WHERE id=?""",
        (project_id,),
    )
    db.commit()
    log_action("trash_cancel_delete", "trash",
               f"Cancelled the deletion hold on GBS {proj['quarter']} {proj['year']}",
               target=f"gbs:{project_id}")
    flash("Deletion hold cancelled. The project remains in the trash bin.", "info")
    return redirect(url_for("trash"))


# ─── WSP / ATR ───────────────────────────────────────────────────────────────

TRAINING_STATUSES = ["Planned", "In Progress", "Completed", "Cancelled"]


@app.route("/wsp")
@login_required
def wsp():
    db = get_db()
    rows, sel = _selected_record(db, "wsp")
    training = db.execute("SELECT * FROM training_records WHERE record_id=? ORDER BY id DESC",
                          (sel["id"],)).fetchall() if sel else []
    unassigned = db.execute("SELECT COUNT(*) FROM training_records WHERE record_id IS NULL").fetchone()[0]
    employees = db.execute("SELECT id, emp_number, first_name, last_name FROM employees WHERE status='ACTIVE' "
                           "ORDER BY last_name, first_name").fetchall()
    return render_template("wsp.html", records=rows, sel=sel, training=training, employees=employees,
                           unassigned=unassigned, company_info=current_profile(db), statuses=TRAINING_STATUSES,
                           status_labels=rec.STATUS_LABELS, editable=bool(sel) and sel["status"] in rec.EDITABLE)


def _editable_record_or_flash(db, record_id, module):
    r = rec.get_record(db, record_id) if record_id else None
    if r is None or r["module"] != module or r["deleted"]:
        flash("Choose a valid %s period first." % rec.module_title(module), "danger")
        return None
    if r["status"] not in rec.EDITABLE:
        flash("%s %s is %s; its data is locked. It must be sent back for changes first." % (
            rec.module_title(module), r["period_label"], rec.STATUS_LABELS[r["status"]].lower()), "danger")
        return None
    return r


@app.route("/wsp/training", methods=["POST"])
@login_required
def wsp_training():
    db = get_db()
    f = request.form
    r = _editable_record_or_flash(db, _int_or_none(f.get("record_id")), "wsp")
    if r is None:
        return redirect(url_for("wsp"))
    errors = []
    emp = db.execute("SELECT * FROM employees WHERE id=?", (_int_or_none(f.get("employee_id")),)).fetchone()
    if emp is None:
        errors.append("Choose the employee.")
    status = f.get("status", "Planned")
    if status not in TRAINING_STATUSES:
        errors.append("Invalid status.")
    try:
        cost = float((f.get("cost") or "0").replace(",", "").replace(" ", ""))
        if cost < 0 or cost != cost or cost > 100_000_000:
            raise ValueError
    except ValueError:
        errors.append("Cost must be a positive amount.")
        cost = 0
    for k in ("target_date", "actual_date"):
        if f.get(k):
            try:
                date.fromisoformat(f[k])
            except ValueError:
                errors.append("%s is not a valid date." % k.replace("_", " ").capitalize())
    if status == "Completed" and not f.get("actual_date"):
        errors.append("Completed training needs the actual date.")
    if not (f.get("intervention_type") or "").strip():
        errors.append("Describe the intervention / programme.")
    for k in ("intervention_type", "provider", "certificate", "category", "skills_gap"):
        if (f.get(k) or "").lstrip().startswith(("=", "+", "@")):
            errors.append("%s looks like a spreadsheet formula, which is not allowed." % k.replace("_", " ").capitalize())
    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("wsp", record=r["id"]))
    cur = db.execute(
        """INSERT INTO training_records (record_id, employee_id, employee_number, employee_name, intervention_type,
               category, skills_gap, nqf_level, cost, provider, target_date, actual_date, certificate, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r["id"], emp["id"], emp["emp_number"], "%s %s" % (emp["first_name"], emp["last_name"]),
         f["intervention_type"].strip()[:200], (f.get("category") or "").strip()[:100] or None,
         (f.get("skills_gap") or "").strip()[:300] or None, (f.get("nqf_level") or "").strip()[:20] or None,
         round(cost, 2), (f.get("provider") or "").strip()[:200] or None, f.get("target_date") or None,
         f.get("actual_date") or None, (f.get("certificate") or "").strip()[:200] or None, status))
    log_action("training_create", "wsp", "Added training for %s (%s %s)" % (emp["emp_number"], "WSP/ATR",
               r["period_label"]), entity="training_record", entity_id=cur.lastrowid, commit=False)
    db.commit()
    flash("Training record added.", "success")
    return redirect(url_for("wsp", record=r["id"]))


@app.route("/wsp/assign-unlinked", methods=["POST"])
@login_required
def wsp_assign_unlinked():
    """Training captured before reporting periods existed is attached to a chosen period."""
    db = get_db()
    r = _editable_record_or_flash(db, _int_or_none(request.form.get("record_id")), "wsp")
    if r is None:
        return redirect(url_for("wsp"))
    n = db.execute("UPDATE training_records SET record_id=? WHERE record_id IS NULL", (r["id"],)).rowcount
    log_action("training_assign", "wsp", "Attached %d earlier training records to WSP/ATR %s" % (n, r["period_label"]),
               entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    flash("%d earlier training record(s) attached to %s." % (n, r["period_label"]), "success")
    return redirect(url_for("wsp", record=r["id"]))


# ─── WSP operational trackers (SDL, grants, learnerships, bursaries) ──────────

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _money_or_none(v, field="Amount"):
    """Parse a rand amount. Returns (value_or_None, error_or_None)."""
    s = (v or "").strip().replace(",", "").replace(" ", "").replace("R", "")
    if s == "":
        return None, None
    try:
        n = float(s)
    except ValueError:
        return None, "%s must be a number." % field
    if n != n or n < 0 or n > 1_000_000_000:
        return None, "%s must be a positive amount." % field
    return round(n, 2), None


def _tracker_years():
    y = date.today().year
    return list(range(y + 1, y - 5, -1))


@app.route("/wsp/sdl", methods=["GET", "POST"])
@login_required
def wsp_sdl():
    """Monthly SDL / EMP201 tracker — one row per calendar month, saved for a whole year."""
    db = get_db()
    year = _int_or_none(request.values.get("year")) or date.today().year
    if request.method == "POST":
        errors = []
        pending = []
        for m in range(1, 13):
            pm = "%04d-%02d" % (year, m)
            payable, e1 = _money_or_none(request.form.get("payable_%d" % m), "SDL payable (%s)" % MONTH_NAMES[m - 1])
            paid, e2 = _money_or_none(request.form.get("paid_%d" % m), "SDL paid (%s)" % MONTH_NAMES[m - 1])
            for e in (e1, e2):
                if e:
                    errors.append(e)
            rdate = (request.form.get("rdate_%d" % m) or "").strip() or None
            if rdate:
                try:
                    date.fromisoformat(rdate)
                except ValueError:
                    errors.append("%s received date is invalid." % MONTH_NAMES[m - 1])
                    rdate = None
            notes = (request.form.get("notes_%d" % m) or "").strip()[:300] or None
            received = 1 if request.form.get("recv_%d" % m) else 0
            pending.append((pm, received, payable, paid, rdate, notes))
        if errors:
            for e in errors[:8]:
                flash(e, "danger")
            return redirect(url_for("wsp_sdl", year=year))
        for pm, received, payable, paid, rdate, notes in pending:
            has_data = received or payable is not None or paid is not None or rdate or notes
            exists = db.execute("SELECT id FROM sdl_tracker WHERE period_month=?", (pm,)).fetchone()
            if exists:
                db.execute("""UPDATE sdl_tracker SET emp201_received=?, sdl_payable=?, sdl_paid=?, received_date=?,
                              notes=?, updated_at=datetime('now'), updated_by=? WHERE period_month=?""",
                           (received, payable, paid, rdate, notes, g.user["id"], pm))
            elif has_data:
                db.execute("""INSERT INTO sdl_tracker (period_month, emp201_received, sdl_payable, sdl_paid,
                              received_date, notes, updated_by) VALUES (?,?,?,?,?,?,?)""",
                           (pm, received, payable, paid, rdate, notes, g.user["id"]))
        log_action("sdl_update", "wsp", "Updated SDL/EMP201 tracker for %d" % year, commit=False)
        db.commit()
        flash("SDL / EMP201 tracker saved for %d." % year, "success")
        return redirect(url_for("wsp_sdl", year=year))

    stored = {r["period_month"]: r for r in db.execute(
        "SELECT * FROM sdl_tracker WHERE period_month LIKE ? ORDER BY period_month", ("%04d-%%" % year,)).fetchall()}
    months = [(m, MONTH_NAMES[m - 1], stored.get("%04d-%02d" % (year, m))) for m in range(1, 13)]
    tot_payable = sum((r["sdl_payable"] or 0) for r in stored.values())
    tot_paid = sum((r["sdl_paid"] or 0) for r in stored.values())
    return render_template("wsp_sdl.html", year=year, months=months, years=_tracker_years(),
                           tot_payable=tot_payable, tot_paid=tot_paid)


GRANT_QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
GRANT_STATUSES = ["Pending", "Received", "Partially received", "Not received", "Queried"]


@app.route("/wsp/grants", methods=["GET", "POST"])
@login_required
def wsp_grants():
    """Mandatory grant tracker — one row per quarter, saved per year."""
    db = get_db()
    year = _int_or_none(request.values.get("year")) or date.today().year
    gtype = "Mandatory"
    if request.method == "POST":
        errors = []
        pending = []
        for q in GRANT_QUARTERS:
            amount, e = _money_or_none(request.form.get("amount_%s" % q), "%s amount" % q)
            if e:
                errors.append(e)
            ddate = (request.form.get("date_%s" % q) or "").strip() or None
            if ddate:
                try:
                    date.fromisoformat(ddate)
                except ValueError:
                    errors.append("%s date received is invalid." % q)
                    ddate = None
            status = request.form.get("status_%s" % q, "Pending")
            if status not in GRANT_STATUSES:
                status = "Pending"
            notes = (request.form.get("notes_%s" % q) or "").strip()[:300] or None
            pending.append((q, amount, ddate, status, notes))
        if errors:
            for e in errors[:8]:
                flash(e, "danger")
            return redirect(url_for("wsp_grants", year=year))
        for q, amount, ddate, status, notes in pending:
            exists = db.execute("SELECT id FROM grants WHERE grant_type=? AND year=? AND quarter=?",
                                (gtype, year, q)).fetchone()
            if exists:
                db.execute("""UPDATE grants SET amount_received=?, date_received=?, status=?, notes=?,
                              updated_at=datetime('now'), updated_by=? WHERE id=?""",
                           (amount, ddate, status, notes, g.user["id"], exists["id"]))
            elif amount is not None or ddate or status != "Pending" or notes:
                db.execute("""INSERT INTO grants (grant_type, year, quarter, amount_received, date_received,
                              status, notes, updated_by) VALUES (?,?,?,?,?,?,?,?)""",
                           (gtype, year, q, amount, ddate, status, notes, g.user["id"]))
        log_action("grant_update", "wsp", "Updated mandatory grant tracker for %d" % year, commit=False)
        db.commit()
        flash("Mandatory grant tracker saved for %d." % year, "success")
        return redirect(url_for("wsp_grants", year=year))

    stored = {r["quarter"]: r for r in db.execute(
        "SELECT * FROM grants WHERE grant_type=? AND year=?", (gtype, year)).fetchall()}
    quarters = [(q, stored.get(q)) for q in GRANT_QUARTERS]
    total = sum((r["amount_received"] or 0) for r in stored.values())
    return render_template("wsp_grants.html", year=year, quarters=quarters, years=_tracker_years(),
                           statuses=GRANT_STATUSES, total=total)


# ── Shared operational document store (bursaries, training, learnerships) ──

WSP_DOC_TYPES = ["Attendance register", "ID copy", "Invoice", "Proof of payment", "Training material",
                 "Assessment", "Certificate", "Signed agreement", "Other"]


def _store_wsp_document(db, entity_type, entity_id, file, doc_type):
    """Securely store an uploaded operational document. Returns (doc_id, error)."""
    import hashlib
    import uuid
    if not file or not file.filename:
        return None, "Choose a file."
    original = os.path.basename(file.filename.replace("\\", "/"))[:200]
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    data = file.read()
    spec = EVIDENCE_TYPES.get(ext)
    if spec is None or not data or not spec[1](data):
        return None, ("That file type is not allowed, or its content does not match its extension. Allowed: %s."
                      % ", ".join(sorted(EVIDENCE_TYPES)))
    folder = os.path.join(DATA_DIR, "wsp_docs")
    os.makedirs(folder, exist_ok=True)
    stored = "%s.%s" % (uuid.uuid4().hex, ext)
    with open(os.path.join(folder, stored), "wb") as fh:
        fh.write(data)
    try:
        cur = db.execute(
            """INSERT INTO wsp_documents (entity_type, entity_id, doc_type, original_name, stored_name,
                   mime, size, sha256, uploaded_by) VALUES (?,?,?,?,?,?,?,?,?)""",
            (entity_type, entity_id, doc_type, original, stored, spec[0], len(data),
             hashlib.sha256(data).hexdigest(), g.user["id"]))
        return cur.lastrowid, None
    except Exception:
        os.remove(os.path.join(folder, stored))
        raise


def _wsp_docs(db, entity_type, entity_id):
    return db.execute("SELECT * FROM wsp_documents WHERE entity_type=? AND entity_id=? ORDER BY id DESC",
                      (entity_type, entity_id)).fetchall()


@app.route("/wsp/document/<int:doc_id>")
@login_required
def wsp_document_download(doc_id):
    import hashlib
    db = get_db()
    d = db.execute("SELECT * FROM wsp_documents WHERE id=?", (doc_id,)).fetchone()
    if d is None:
        abort(404)
    path = os.path.join(DATA_DIR, "wsp_docs", os.path.basename(d["stored_name"]))
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        flash("The document file is missing. Restore it from backup.", "danger")
        return redirect(request.referrer or url_for("wsp"))
    if hashlib.sha256(data).hexdigest() != d["sha256"]:
        flash("The document does not match its recorded fingerprint. Restore it from backup.", "danger")
        return redirect(request.referrer or url_for("wsp"))
    log_action("wsp_doc_download", "wsp", "Downloaded document '%s'" % d["original_name"],
               entity="wsp_document", entity_id=doc_id)
    return send_file(io.BytesIO(data), mimetype=d["mime"], as_attachment=True, download_name=d["original_name"])


@app.route("/wsp/document/<int:doc_id>/delete", methods=["POST"])
@login_required
def wsp_document_delete(doc_id):
    db = get_db()
    d = db.execute("SELECT * FROM wsp_documents WHERE id=?", (doc_id,)).fetchone()
    if d is None:
        abort(404)
    db.execute("DELETE FROM wsp_documents WHERE id=?", (doc_id,))
    log_action("wsp_doc_delete", "wsp", "Deleted document '%s'" % d["original_name"],
               entity="wsp_document", entity_id=doc_id, commit=False)
    db.commit()
    try:
        os.remove(os.path.join(DATA_DIR, "wsp_docs", os.path.basename(d["stored_name"])))
    except OSError:
        pass
    flash("Document removed.", "success")
    return redirect(request.referrer or url_for("wsp"))


@app.route("/wsp/dashboard")
@login_required
def wsp_dashboard():
    """Year-at-a-glance WSP / ATR dashboard aggregating the operational trackers."""
    db = get_db()
    year = _int_or_none(request.values.get("year")) or date.today().year
    yp = "%04d-%%" % year

    def one(sql, params=()):
        return db.execute(sql, params).fetchone()

    te_planned = one("SELECT COUNT(*) c FROM training_events WHERE training_date LIKE ? AND status IN ('Planned','In Progress')", (yp,))["c"]
    te_completed = one("SELECT COUNT(*) c FROM training_events WHERE training_date LIKE ? AND status='Completed'", (yp,))["c"]
    te_spend = one("SELECT COALESCE(SUM(cost),0) s FROM training_events WHERE training_date LIKE ?", (yp,))["s"]
    emp_trained = one("SELECT COUNT(DISTINCT employee_id) c FROM training_events WHERE training_date LIKE ? AND employee_id IS NOT NULL", (yp,))["c"]
    attendances = one("SELECT COALESCE(SUM(COALESCE(num_attendees,1)),0) c FROM training_events WHERE training_date LIKE ?", (yp,))["c"]
    skills_progs = one("SELECT COUNT(*) c FROM training_events WHERE training_date LIKE ? AND LOWER(COALESCE(category,'')) LIKE '%skills programme%'", (yp,))["c"]

    def grant(t):
        r = one("""SELECT COALESCE(SUM(amount_received),0) amt,
                          SUM(CASE WHEN status='Received' THEN 1 ELSE 0 END) rec,
                          COUNT(*) n FROM grants WHERE grant_type=? AND year=?""", (t, year))
        return {"amount": r["amt"], "received_quarters": r["rec"] or 0, "rows": r["n"] or 0}
    mandatory = grant("Mandatory")
    discretionary = grant("Discretionary")

    learn = one("SELECT COUNT(*) c, COALESCE(SUM(target_count),0) t FROM learnership_plans WHERE year=? AND programme_type='Learnership'", (year,))
    intern = one("SELECT COUNT(*) c, COALESCE(SUM(target_count),0) t FROM learnership_plans WHERE year=? AND programme_type='Internship'", (year,))

    cfg = one("SELECT budget_total, target_count FROM bursary_budgets WHERE year=?", (year,))
    bud_total = cfg["budget_total"] if cfg else DEFAULT_BURSARY_BUDGET
    bud_target = cfg["target_count"] if cfg else DEFAULT_BURSARY_COUNT
    bur = one("SELECT COUNT(*) c, COALESCE(SUM(amount_awarded),0) a FROM bursary_awards WHERE year=?", (year,))

    sdl = one("SELECT COALESCE(SUM(sdl_payable),0) payable, COALESCE(SUM(sdl_paid),0) paid FROM sdl_tracker WHERE period_month LIKE ?", (yp,))

    from modules.reminders import compute_wsp_reminders
    reminders = compute_wsp_reminders(db)

    return render_template(
        "wsp_dashboard.html", year=year, years=_tracker_years(), reminders=reminders,
        te_planned=te_planned, te_completed=te_completed, te_spend=te_spend,
        emp_trained=emp_trained, attendances=attendances, skills_progs=skills_progs,
        mandatory=mandatory, discretionary=discretionary,
        learn_count=learn["c"], learn_target=learn["t"], intern_count=intern["c"], intern_target=intern["t"],
        bud_total=bud_total, bud_target=bud_target, bur_count=bur["c"], bur_awarded=bur["a"],
        bur_remaining=bud_total - bur["a"], sdl_payable=sdl["payable"], sdl_paid=sdl["paid"])


def _doc_source_label(db, entity_type, entity_id):
    if entity_type == "training":
        r = db.execute("SELECT intervention_type, training_date FROM training_events WHERE id=?", (entity_id,)).fetchone()
        return ("Training — %s (%s)" % (r["intervention_type"], r["training_date"])) if r else "Training (removed)"
    if entity_type == "bursary":
        r = db.execute("SELECT student_name, year FROM bursary_awards WHERE id=?", (entity_id,)).fetchone()
        return ("Bursary — %s (%s)" % (r["student_name"], r["year"])) if r else "Bursary (removed)"
    if entity_type == "learnership":
        r = db.execute("SELECT title FROM learnership_plans WHERE id=?", (entity_id,)).fetchone()
        return ("Learnership — %s" % r["title"]) if r else "Learnership (removed)"
    return entity_type.capitalize()


@app.route("/wsp/documents")
@login_required
def wsp_documents_repo():
    """Audit repository: every WSP supporting document in one filterable list."""
    db = get_db()
    dtype = request.values.get("doc_type")
    etype = request.values.get("entity_type")
    q = "SELECT * FROM wsp_documents"
    conds, params = [], []
    if dtype in WSP_DOC_TYPES:
        conds.append("doc_type=?")
        params.append(dtype)
    if etype in ("training", "bursary", "learnership"):
        conds.append("entity_type=?")
        params.append(etype)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY uploaded_at DESC, id DESC"
    rows = db.execute(q, params).fetchall()
    names = {u["id"]: (u["full_name"] or u["username"]) for u in db.execute("SELECT id, username, full_name FROM users").fetchall()}
    docs = [{"d": d, "source": _doc_source_label(db, d["entity_type"], d["entity_id"]),
             "by": names.get(d["uploaded_by"], "—")} for d in rows]
    return render_template("wsp_documents.html", docs=docs, doc_types=WSP_DOC_TYPES, dtype=dtype, etype=etype,
                           total=len(rows))


# ── Bursaries ──

BURSARY_STATUSES = ["Awarded", "Active", "Completed", "Withdrawn", "Pending"]
DEFAULT_BURSARY_BUDGET = 50000.0
DEFAULT_BURSARY_COUNT = 10


def _formula_guard(f, fields):
    for k in fields:
        if (f.get(k) or "").lstrip().startswith(("=", "+", "@")):
            return "%s looks like a spreadsheet formula, which is not allowed." % k.replace("_", " ").capitalize()
    return None


@app.route("/wsp/bursaries")
@login_required
def wsp_bursaries():
    db = get_db()
    year = _int_or_none(request.values.get("year")) or date.today().year
    cfg = db.execute("SELECT * FROM bursary_budgets WHERE year=?", (year,)).fetchone()
    budget = cfg["budget_total"] if cfg else DEFAULT_BURSARY_BUDGET
    target = cfg["target_count"] if cfg else DEFAULT_BURSARY_COUNT
    awards = db.execute("SELECT * FROM bursary_awards WHERE year=? ORDER BY id DESC", (year,)).fetchall()
    awarded_total = sum((a["amount_awarded"] or 0) for a in awards)
    docs = {a["id"]: _wsp_docs(db, "bursary", a["id"]) for a in awards}
    return render_template("wsp_bursaries.html", year=year, years=_tracker_years(), budget=budget, target=target,
                           awards=awards, awarded_total=awarded_total, remaining=budget - awarded_total,
                           statuses=BURSARY_STATUSES, docs=docs, doc_types=WSP_DOC_TYPES)


@app.route("/wsp/bursaries/config", methods=["POST"])
@login_required
def wsp_bursaries_config():
    db = get_db()
    year = _int_or_none(request.form.get("year")) or date.today().year
    budget, e = _money_or_none(request.form.get("budget_total"), "Budget")
    target = _int_or_none(request.form.get("target_count"))
    if e or budget is None:
        flash(e or "Enter an annual budget.", "danger")
        return redirect(url_for("wsp_bursaries", year=year))
    db.execute("""INSERT INTO bursary_budgets (year, budget_total, target_count, updated_by) VALUES (?,?,?,?)
                  ON CONFLICT(year) DO UPDATE SET budget_total=excluded.budget_total,
                  target_count=excluded.target_count, updated_at=datetime('now'), updated_by=excluded.updated_by""",
               (year, budget, target if target is not None else DEFAULT_BURSARY_COUNT, g.user["id"]))
    log_action("bursary_config", "wsp", "Set bursary budget for %d to R%.2f / %s bursaries"
               % (year, budget, target), commit=False)
    db.commit()
    flash("Bursary budget saved.", "success")
    return redirect(url_for("wsp_bursaries", year=year))


@app.route("/wsp/bursaries/award", methods=["POST"])
@login_required
def wsp_bursary_award():
    db = get_db()
    f = request.form
    year = _int_or_none(f.get("year")) or date.today().year
    name = (f.get("student_name") or "").strip()[:200]
    if not name:
        flash("Student name is required.", "danger")
        return redirect(url_for("wsp_bursaries", year=year))
    amount, e = _money_or_none(f.get("amount_awarded"), "Amount awarded")
    guard = _formula_guard(f, ("student_name", "institution", "qualification", "notes"))
    if e or guard:
        flash(e or guard, "danger")
        return redirect(url_for("wsp_bursaries", year=year))
    status = f.get("status") if f.get("status") in BURSARY_STATUSES else "Awarded"
    cur = db.execute(
        """INSERT INTO bursary_awards (year, student_name, institution, qualification, amount_awarded,
               status, notes, updated_by) VALUES (?,?,?,?,?,?,?,?)""",
        (year, name, (f.get("institution") or "").strip()[:200] or None,
         (f.get("qualification") or "").strip()[:200] or None, amount or 0, status,
         (f.get("notes") or "").strip()[:300] or None, g.user["id"]))
    log_action("bursary_award", "wsp", "Added bursary for %s (%d)" % (name, year),
               entity="bursary_award", entity_id=cur.lastrowid, commit=False)
    db.commit()
    flash("Bursary award added.", "success")
    return redirect(url_for("wsp_bursaries", year=year))


@app.route("/wsp/bursaries/award/<int:award_id>/delete", methods=["POST"])
@login_required
def wsp_bursary_delete(award_id):
    db = get_db()
    a = db.execute("SELECT * FROM bursary_awards WHERE id=?", (award_id,)).fetchone()
    if a is None:
        abort(404)
    for d in _wsp_docs(db, "bursary", award_id):
        try:
            os.remove(os.path.join(DATA_DIR, "wsp_docs", os.path.basename(d["stored_name"])))
        except OSError:
            pass
    db.execute("DELETE FROM wsp_documents WHERE entity_type='bursary' AND entity_id=?", (award_id,))
    db.execute("DELETE FROM bursary_awards WHERE id=?", (award_id,))
    log_action("bursary_delete", "wsp", "Deleted bursary for %s" % a["student_name"],
               entity="bursary_award", entity_id=award_id, commit=False)
    db.commit()
    flash("Bursary award removed.", "success")
    return redirect(url_for("wsp_bursaries", year=a["year"]))


@app.route("/wsp/bursaries/award/<int:award_id>/document", methods=["POST"])
@login_required
def wsp_bursary_document(award_id):
    db = get_db()
    a = db.execute("SELECT * FROM bursary_awards WHERE id=?", (award_id,)).fetchone()
    if a is None:
        abort(404)
    doc_type = request.form.get("doc_type") if request.form.get("doc_type") in WSP_DOC_TYPES else "Other"
    doc_id, err = _store_wsp_document(db, "bursary", award_id, request.files.get("file"), doc_type)
    if err:
        flash(err, "danger")
        return redirect(url_for("wsp_bursaries", year=a["year"]))
    log_action("wsp_doc_upload", "wsp", "Added %s to bursary for %s" % (doc_type, a["student_name"]),
               entity="wsp_document", entity_id=doc_id, commit=False)
    db.commit()
    flash("Document added.", "success")
    return redirect(url_for("wsp_bursaries", year=a["year"]))


# ── Learnerships & internships ──

PROGRAMME_TYPES = ["Learnership", "Internship"]
LEARNERSHIP_STATUSES = ["Planned", "Recruiting", "Selection", "Contracting", "In progress",
                        "Completed", "Cancelled"]


def _parse_dates(f, keys, errors):
    out = {}
    for k in keys:
        v = (f.get(k) or "").strip() or None
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                errors.append("%s is not a valid date." % k.replace("_", " ").capitalize())
                v = None
        out[k] = v
    return out


@app.route("/wsp/learnerships")
@login_required
def wsp_learnerships():
    db = get_db()
    year = _int_or_none(request.values.get("year")) or date.today().year
    plans = db.execute("SELECT * FROM learnership_plans WHERE year=? ORDER BY programme_type, id", (year,)).fetchall()
    # Default cycle dates for the add form: recruit Oct, select Nov, contract Dec, commence Jan (next year)
    defaults = {"recruitment_start": "%d-10-01" % year, "selection_by": "%d-11-30" % year,
                "contracts_by": "%d-12-15" % year, "commence_date": "%d-01-15" % (year + 1)}
    return render_template("wsp_learnerships.html", year=year, years=_tracker_years(), plans=plans,
                           programme_types=PROGRAMME_TYPES, statuses=LEARNERSHIP_STATUSES, defaults=defaults)


@app.route("/wsp/learnerships/add", methods=["POST"])
@login_required
def wsp_learnership_add():
    db = get_db()
    f = request.form
    year = _int_or_none(f.get("year")) or date.today().year
    title = (f.get("title") or "").strip()[:200]
    errors = []
    if not title:
        errors.append("Give the programme a title.")
    guard = _formula_guard(f, ("title", "notes"))
    if guard:
        errors.append(guard)
    ptype = f.get("programme_type") if f.get("programme_type") in PROGRAMME_TYPES else "Learnership"
    status = f.get("status") if f.get("status") in LEARNERSHIP_STATUSES else "Planned"
    dates = _parse_dates(f, ("recruitment_start", "selection_by", "contracts_by", "commence_date"), errors)
    if errors:
        for e in errors[:6]:
            flash(e, "danger")
        return redirect(url_for("wsp_learnerships", year=year))
    cur = db.execute(
        """INSERT INTO learnership_plans (year, programme_type, title, target_count, recruitment_start,
               selection_by, contracts_by, commence_date, status, notes, updated_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (year, ptype, title, _int_or_none(f.get("target_count")), dates["recruitment_start"],
         dates["selection_by"], dates["contracts_by"], dates["commence_date"], status,
         (f.get("notes") or "").strip()[:300] or None, g.user["id"]))
    log_action("learnership_add", "wsp", "Added %s plan '%s' (%d)" % (ptype, title, year),
               entity="learnership_plan", entity_id=cur.lastrowid, commit=False)
    db.commit()
    flash("Programme plan added.", "success")
    return redirect(url_for("wsp_learnerships", year=year))


@app.route("/wsp/learnerships/<int:plan_id>/update", methods=["POST"])
@login_required
def wsp_learnership_update(plan_id):
    db = get_db()
    p = db.execute("SELECT * FROM learnership_plans WHERE id=?", (plan_id,)).fetchone()
    if p is None:
        abort(404)
    f = request.form
    errors = []
    status = f.get("status") if f.get("status") in LEARNERSHIP_STATUSES else p["status"]
    dates = _parse_dates(f, ("recruitment_start", "selection_by", "contracts_by", "commence_date"), errors)
    if errors:
        for e in errors[:6]:
            flash(e, "danger")
        return redirect(url_for("wsp_learnerships", year=p["year"]))
    db.execute("""UPDATE learnership_plans SET target_count=?, recruitment_start=?, selection_by=?,
                  contracts_by=?, commence_date=?, status=?, notes=?, updated_at=datetime('now'), updated_by=?
                  WHERE id=?""",
               (_int_or_none(f.get("target_count")), dates["recruitment_start"], dates["selection_by"],
                dates["contracts_by"], dates["commence_date"], status,
                (f.get("notes") or "").strip()[:300] or None, g.user["id"], plan_id))
    log_action("learnership_update", "wsp", "Updated %s plan '%s'" % (p["programme_type"], p["title"]),
               entity="learnership_plan", entity_id=plan_id, commit=False)
    db.commit()
    flash("Programme plan updated.", "success")
    return redirect(url_for("wsp_learnerships", year=p["year"]))


@app.route("/wsp/learnerships/<int:plan_id>/delete", methods=["POST"])
@login_required
def wsp_learnership_delete(plan_id):
    db = get_db()
    p = db.execute("SELECT * FROM learnership_plans WHERE id=?", (plan_id,)).fetchone()
    if p is None:
        abort(404)
    db.execute("DELETE FROM learnership_plans WHERE id=?", (plan_id,))
    log_action("learnership_delete", "wsp", "Deleted %s plan '%s'" % (p["programme_type"], p["title"]),
               entity="learnership_plan", entity_id=plan_id, commit=False)
    db.commit()
    flash("Programme plan removed.", "success")
    return redirect(url_for("wsp_learnerships", year=p["year"]))


# ── Monthly training log (internal + external, with documents) ──

FUNDING_TYPES = ["Internal", "External", "Paid"]
TRAINING_EVENT_STATUSES = ["Planned", "In Progress", "Completed", "Cancelled"]


@app.route("/wsp/training-log")
@login_required
def wsp_training_log():
    db = get_db()
    today = date.today()
    year = _int_or_none(request.values.get("year")) or today.year
    month = _int_or_none(request.values.get("month")) or today.month
    if not 1 <= month <= 12:
        month = today.month
    funding = request.values.get("funding") if request.values.get("funding") in FUNDING_TYPES else None
    prefix = "%04d-%02d%%" % (year, month)
    q = "SELECT * FROM training_events WHERE training_date LIKE ?"
    params = [prefix]
    if funding:
        q += " AND funding_type=?"
        params.append(funding)
    q += " ORDER BY training_date DESC, id DESC"
    events = db.execute(q, params).fetchall()
    docs = {e["id"]: _wsp_docs(db, "training", e["id"]) for e in events}
    summary = db.execute(
        """SELECT intervention_type, COUNT(*) AS n, COALESCE(SUM(cost),0) AS cost,
                  COALESCE(SUM(COALESCE(num_attendees,1)),0) AS attendees
           FROM training_events WHERE training_date LIKE ? GROUP BY intervention_type ORDER BY cost DESC""",
        (prefix,)).fetchall()
    month_total = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost),0) AS c FROM training_events WHERE training_date LIKE ?",
        (prefix,)).fetchone()
    employees = db.execute("SELECT id, emp_number, first_name, last_name FROM employees WHERE status='ACTIVE' "
                           "ORDER BY last_name, first_name").fetchall()
    return render_template("wsp_training_log.html", year=year, month=month, years=_tracker_years(),
                           months=list(enumerate(MONTH_NAMES, 1)), funding=funding, funding_types=FUNDING_TYPES,
                           events=events, docs=docs, summary=summary, month_total=month_total,
                           statuses=TRAINING_EVENT_STATUSES, employees=employees, doc_types=WSP_DOC_TYPES)


@app.route("/wsp/training-log/add", methods=["POST"])
@login_required
def wsp_training_event_add():
    db = get_db()
    f = request.form
    year = _int_or_none(f.get("year")) or date.today().year
    month = _int_or_none(f.get("month")) or date.today().month
    errors = []
    tdate = (f.get("training_date") or "").strip()
    d = None
    try:
        d = date.fromisoformat(tdate)
    except ValueError:
        errors.append("Enter a valid training date.")
    intervention = (f.get("intervention_type") or "").strip()[:200]
    if not intervention:
        errors.append("Describe the intervention / programme.")
    cost, e = _money_or_none(f.get("cost"), "Cost")
    if e:
        errors.append(e)
    guard = _formula_guard(f, ("intervention_type", "category", "provider", "notes"))
    if guard:
        errors.append(guard)
    funding = f.get("funding_type") if f.get("funding_type") in FUNDING_TYPES else "Internal"
    status = f.get("status") if f.get("status") in TRAINING_EVENT_STATUSES else "Completed"
    emp = db.execute("SELECT * FROM employees WHERE id=?", (_int_or_none(f.get("employee_id")),)).fetchone() \
        if _int_or_none(f.get("employee_id")) else None
    if errors:
        for er in errors[:6]:
            flash(er, "danger")
        return redirect(url_for("wsp_training_log", year=year, month=month))
    cur = db.execute(
        """INSERT INTO training_events (training_date, employee_id, employee_name, employee_number,
               intervention_type, category, nqf_level, provider, cost, funding_type, num_attendees,
               status, notes, updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tdate, emp["id"] if emp else None,
         ("%s %s" % (emp["first_name"], emp["last_name"])) if emp else None,
         emp["emp_number"] if emp else None, intervention,
         (f.get("category") or "").strip()[:100] or None, (f.get("nqf_level") or "").strip()[:20] or None,
         (f.get("provider") or "").strip()[:200] or None, cost or 0, funding,
         _int_or_none(f.get("num_attendees")), status, (f.get("notes") or "").strip()[:300] or None, g.user["id"]))
    log_action("training_event_add", "wsp", "Logged %s training '%s' on %s" % (funding.lower(), intervention, tdate),
               entity="training_event", entity_id=cur.lastrowid, commit=False)
    db.commit()
    flash("Training logged.", "success")
    return redirect(url_for("wsp_training_log", year=d.year, month=d.month))


@app.route("/wsp/training-log/<int:event_id>/delete", methods=["POST"])
@login_required
def wsp_training_event_delete(event_id):
    db = get_db()
    ev = db.execute("SELECT * FROM training_events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        abort(404)
    for d in _wsp_docs(db, "training", event_id):
        try:
            os.remove(os.path.join(DATA_DIR, "wsp_docs", os.path.basename(d["stored_name"])))
        except OSError:
            pass
    db.execute("DELETE FROM wsp_documents WHERE entity_type='training' AND entity_id=?", (event_id,))
    db.execute("DELETE FROM training_events WHERE id=?", (event_id,))
    log_action("training_event_delete", "wsp", "Deleted training event '%s'" % ev["intervention_type"],
               entity="training_event", entity_id=event_id, commit=False)
    db.commit()
    flash("Training event removed.", "success")
    return redirect(url_for("wsp_training_log", year=ev["training_date"][:4], month=int(ev["training_date"][5:7])))


@app.route("/wsp/training-log/<int:event_id>/document", methods=["POST"])
@login_required
def wsp_training_event_document(event_id):
    db = get_db()
    ev = db.execute("SELECT * FROM training_events WHERE id=?", (event_id,)).fetchone()
    if ev is None:
        abort(404)
    doc_type = request.form.get("doc_type") if request.form.get("doc_type") in WSP_DOC_TYPES else "Other"
    doc_id, err = _store_wsp_document(db, "training", event_id, request.files.get("file"), doc_type)
    if err:
        flash(err, "danger")
    else:
        log_action("wsp_doc_upload", "wsp", "Added %s to training '%s'" % (doc_type, ev["intervention_type"]),
                   entity="wsp_document", entity_id=doc_id, commit=False)
        db.commit()
        flash("Document added.", "success")
    return redirect(url_for("wsp_training_log", year=ev["training_date"][:4], month=int(ev["training_date"][5:7])))


# ─── B-BBEE ──────────────────────────────────────────────────────────────────

@app.route("/bbbee")
@login_required
def bbbee():
    import json
    from modules import bbbee as bb
    db = get_db()
    rows, sel = _selected_record(db, "bbbee")
    inputs = calc = None
    error = None
    params = {}
    if sel:
        row = db.execute("SELECT * FROM bbbee_inputs WHERE record_id=?", (sel["id"],)).fetchone()
        if row:
            inputs = dict(row)
            inputs["values"] = json.loads(row["inputs_json"] or "{}")
            rs = get_rule_set(db, bb.SCORECARD_CODES[row["scorecard_type"]], sel.get("period_end"))
            try:
                calc = bb.calculate(row["scorecard_type"], rs["params"], inputs["values"], row["black_owned_pct"] or 0)
            except bb.InputError as e:
                error = str(e)
    for st, code in bb.SCORECARD_CODES.items():
        rs = get_rule_set(db, code, sel.get("period_end") if sel else None)
        params[st] = rs["params"] if rs else {}
    return render_template("bbbee.html", records=rows, sel=sel, inputs=inputs, calc=calc, calc_error=error,
                           params=params, company_info=current_profile(db), status_labels=rec.STATUS_LABELS,
                           editable=bool(sel) and sel["status"] in rec.EDITABLE)


@app.route("/bbbee/save", methods=["POST"])
@login_required
def bbbee_save():
    import json
    from modules import bbbee as bb
    db = get_db()
    f = request.form
    r = _editable_record_or_flash(db, _int_or_none(f.get("record_id")), "bbbee")
    if r is None:
        return redirect(url_for("bbbee"))
    stype = f.get("scorecard_type", "")
    if stype not in bb.SCORECARD_CODES:
        flash("Choose a scorecard type.", "danger")
        return redirect(url_for("bbbee", record=r["id"]))
    rs = get_rule_set(db, bb.SCORECARD_CODES[stype], r.get("period_end"))
    keys = [e["key"] for e in rs["params"].get("elements", [])]
    keys += [e["key"] + "_bonus" for e in rs["params"].get("elements", [])]
    keys += [s["key"] for s in rs["params"].get("subminimums", [])]
    values = {k: (f.get(k) or "0").strip() for k in keys}
    try:
        turnover = bb.d((f.get("annual_turnover") or "0").replace(",", "").replace(" ", ""))
        bo = bb.d(f.get("black_owned_pct") or "0")
        bwo = bb.d(f.get("black_women_owned_pct") or "0")
        if turnover < 0:
            raise bb.InputError("Turnover cannot be negative.")
        if not (0 <= bwo <= bo <= 100):
            raise bb.InputError("Black women ownership must be between 0 and black ownership, which must be 0–100%.")
        calc = bb.calculate(stype, rs["params"], values, bo)
    except bb.InputError as e:
        flash(str(e), "danger")
        return redirect(url_for("bbbee", record=r["id"]))
    old = db.execute("SELECT * FROM bbbee_inputs WHERE record_id=?", (r["id"],)).fetchone()
    db.execute(
        """INSERT INTO bbbee_inputs (record_id, scorecard_type, annual_turnover, black_owned_pct, black_women_owned_pct,
               inputs_json, updated_at, updated_by) VALUES (?,?,?,?,?,?,datetime('now'),?)
           ON CONFLICT(record_id) DO UPDATE SET scorecard_type=excluded.scorecard_type,
               annual_turnover=excluded.annual_turnover, black_owned_pct=excluded.black_owned_pct,
               black_women_owned_pct=excluded.black_women_owned_pct, inputs_json=excluded.inputs_json,
               updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
        (r["id"], stype, float(turnover), float(bo), float(bwo), json.dumps(values), g.user["id"]))
    log_action("bbbee_update", "bbbee", "Saved B-BBEE inputs for %s (%s, %s)" % (
        r["period_label"], stype, "Non-compliant" if calc["level"] == "NC" else "Level %s" % calc["level"]),
        entity="compliance_record", entity_id=r["id"],
        before=json.loads(old["inputs_json"]) if old else None, after=values, commit=False)
    db.commit()
    lvl = "Non-compliant" if calc["level"] == "NC" else "Level %s" % calc["level"]
    flash("Inputs saved. Internal calculation: %s (%s points)." % (lvl, calc["total"]), "success")
    return redirect(url_for("bbbee", record=r["id"]))


@app.route("/api/bbbee/level", methods=["POST"])
@login_required
def api_bbbee_level():
    from modules import bbbee as bb
    db = get_db()
    data = request.get_json(silent=True) or {}
    stype = str(data.get("scorecard_type", "GENERIC")).upper()
    if stype not in bb.SCORECARD_CODES:
        return jsonify({"ok": False, "error": "Unknown scorecard type."}), 400
    rs = get_rule_set(db, bb.SCORECARD_CODES[stype])
    try:
        calc = bb.calculate(stype, rs["params"], data.get("inputs") or {}, data.get("black_owned_pct") or 0)
    except bb.InputError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "level": calc["level"], "base_level": calc["base_level"],
                    "total": str(calc["total"]), "recognition_pct": calc["recognition_pct"],
                    "discounted": calc["discounted"], "notes": calc["notes"]})


# ─── COIDA ───────────────────────────────────────────────────────────────────

@app.route("/coida")
@login_required
def coida():
    from modules import coida as cc
    db = get_db()
    rows, sel = _selected_record(db, "coida")
    calc = earnings = rates = None
    cap_info = None
    if sel:
        snap, _c, rs = reports_store.build_snapshot(db, sel, "was8")
        calc = cc.calculate(snap)
        earnings = db.execute("SELECT * FROM coida_earnings WHERE record_id=? ORDER BY class_code, employee_number",
                              (sel["id"],)).fetchall()
        rates = db.execute("SELECT * FROM coida_rates WHERE record_id=? ORDER BY class_code, subclass",
                           (sel["id"],)).fetchall()
        cap_info = {"rule": rs, "cap": calc["cap"]}
    logs = db.execute("SELECT * FROM logs_records ORDER BY expiry_date").fetchall()
    today = date.today()
    soon = (today + timedelta(days=30)).isoformat()
    logs_view = []
    for lg in logs:
        exp = lg["expiry_date"] or ""
        status = "Unknown" if not exp else "Expired" if exp < today.isoformat() else \
            "Expiring Soon" if exp <= soon else "Valid"
        logs_view.append({"log": lg, "status": status})
    legacy = db.execute("SELECT * FROM coida_classes ORDER BY class_code").fetchall()
    return render_template("coida.html", records=rows, sel=sel, calc=calc, earnings=earnings, rates=rates,
                           cap_info=cap_info, logs=logs_view, legacy=legacy, company_info=current_profile(db),
                           status_labels=rec.STATUS_LABELS, editable=bool(sel) and sel["status"] in rec.EDITABLE,
                           today=today.isoformat())


@app.route("/coida/rate", methods=["POST"])
@login_required
def coida_rate():
    db = get_db()
    f = request.form
    r = _editable_record_or_flash(db, _int_or_none(f.get("record_id")), "coida")
    if r is None:
        return redirect(url_for("coida"))
    code = (f.get("class_code") or "").strip()[:10]
    sub = (f.get("subclass") or "").strip()[:10]
    try:
        rate = float((f.get("rate") or "").replace(",", "."))
        if rate < 0 or rate > 100 or rate != rate:
            raise ValueError
    except ValueError:
        flash("Rate must be a number between 0 and 100 (per R100 of earnings).", "danger")
        return redirect(url_for("coida", record=r["id"]))
    if not code:
        flash("Class code is required.", "danger")
        return redirect(url_for("coida", record=r["id"]))
    old = db.execute("SELECT rate FROM coida_rates WHERE record_id=? AND class_code=? AND subclass=?",
                     (r["id"], code, sub)).fetchone()
    db.execute("""INSERT INTO coida_rates (record_id, class_code, subclass, description, rate, source_note)
                  VALUES (?,?,?,?,?,?) ON CONFLICT(record_id, class_code, subclass) DO UPDATE SET
                  description=excluded.description, rate=excluded.rate, source_note=excluded.source_note""",
               (r["id"], code, sub, (f.get("description") or "").strip()[:200] or None, rate,
                (f.get("source_note") or "").strip()[:200] or None))
    log_action("coida_rate", "coida", "Set class %s%s rate to %s for %s" % (code, "/" + sub if sub else "", rate,
               r["period_label"]), entity="compliance_record", entity_id=r["id"],
               before={"rate": old["rate"]} if old else None, after={"rate": rate}, commit=False)
    db.commit()
    flash("Rate saved.", "success")
    return redirect(url_for("coida", record=r["id"]))


@app.route("/coida/build-earnings", methods=["POST"])
@login_required
def coida_build_earnings():
    """(Re)build per-employee earnings for the period from the register:
    monthly salary x months employed within the assessment period."""
    from modules.ee import months_in_period, money
    db = get_db()
    r = _editable_record_or_flash(db, _int_or_none(request.form.get("record_id")), "coida")
    if r is None:
        return redirect(url_for("coida"))
    if not (r["period_start"] and r["period_end"]):
        flash("Set the assessment period start and end dates on the record first.", "danger")
        return redirect(url_for("coida", record=r["id"]))
    ps, pe = date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"])
    rows = db.execute("SELECT * FROM employees WHERE employment_date IS NOT NULL").fetchall()
    added, skipped = 0, []
    db.execute("DELETE FROM coida_earnings WHERE record_id=?", (r["id"],))
    for e in rows:
        try:
            start = date.fromisoformat(e["employment_date"])
            end = date.fromisoformat(e["termination_date"]) if e["termination_date"] else None
        except ValueError:
            skipped.append(e["emp_number"] or str(e["id"]))
            continue
        months = months_in_period(start, end, ps, pe)
        if months <= 0:
            continue
        if not e["coida_class_code"] or e["salary"] is None:
            skipped.append(e["emp_number"] or str(e["id"]))
            continue
        earn = money(__import__("decimal").Decimal(str(e["salary"])) * months)
        db.execute("""INSERT INTO coida_earnings (record_id, employee_id, employee_number, employee_name, class_code,
                          subclass, earnings, months_worked) VALUES (?,?,?,?,?,?,?,?)""",
                   (r["id"], e["id"], e["emp_number"], "%s %s" % (e["first_name"], e["last_name"]),
                    e["coida_class_code"], e["coida_subclass"] or "", float(earn), float(months)))
        added += 1
    log_action("coida_build", "coida", "Built COIDA earnings for %s: %d employees, %d skipped" % (
        r["period_label"], added, len(skipped)), entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    flash("Earnings built for %d employee(s)." % added, "success")
    if skipped:
        flash("Skipped %d employee(s) with no COIDA class or salary: %s" % (len(skipped), ", ".join(skipped[:15])),
              "warning")
    return redirect(url_for("coida", record=r["id"]))


@app.route("/coida/earning/<int:earning_id>", methods=["POST"])
@login_required
def coida_earning_edit(earning_id):
    db = get_db()
    row = db.execute("SELECT * FROM coida_earnings WHERE id=?", (earning_id,)).fetchone()
    if row is None:
        abort(404)
    r = _editable_record_or_flash(db, row["record_id"], "coida")
    if r is None:
        return redirect(url_for("coida"))
    try:
        earn = float((request.form.get("earnings") or "").replace(",", "").replace(" ", ""))
        if earn < 0 or earn != earn:
            raise ValueError
    except ValueError:
        flash("Earnings must be zero or more.", "danger")
        return redirect(url_for("coida", record=r["id"]))
    code = (request.form.get("class_code") or row["class_code"]).strip()[:10]
    db.execute("UPDATE coida_earnings SET earnings=?, class_code=?, notes=? WHERE id=?",
               (round(earn, 2), code, (request.form.get("notes") or "").strip()[:200] or None, earning_id))
    log_action("coida_earning_edit", "coida", "Adjusted COIDA earnings for %s in %s" % (row["employee_number"],
               r["period_label"]), entity="coida_earning", entity_id=earning_id,
               before={"earnings": row["earnings"], "class_code": row["class_code"]},
               after={"earnings": round(earn, 2), "class_code": code}, commit=False)
    db.commit()
    flash("Earnings updated.", "success")
    return redirect(url_for("coida", record=r["id"]))


@app.route("/coida/logs", methods=["POST"])
@login_required
def coida_logs():
    db = get_db()
    data = request.form
    for k in ("issue_date", "expiry_date"):
        if data.get(k):
            try:
                date.fromisoformat(data[k])
            except ValueError:
                flash("Invalid date.", "danger")
                return redirect(url_for("coida"))
    if data.get("issue_date") and data.get("expiry_date") and data["expiry_date"] < data["issue_date"]:
        flash("Expiry date must be after the issue date.", "danger")
        return redirect(url_for("coida"))
    rec_id = _int_or_none(data.get("record_id"))
    vals = ((data.get("insurer") or "").strip()[:200], (data.get("policy_number") or "").strip()[:100],
            data.get("issue_date") or None, data.get("expiry_date") or None)
    if rec_id:
        old = db.execute("SELECT * FROM logs_records WHERE id=?", (rec_id,)).fetchone()
        if old is None:
            abort(404)
        db.execute("UPDATE logs_records SET insurer=?, policy_number=?, issue_date=?, expiry_date=? WHERE id=?",
                   vals + (rec_id,))
        log_action("logs_update", "coida", "Updated Letter of Good Standing %s" % vals[1], entity="logs", entity_id=rec_id,
                   before=dict(old), after=dict(zip(("insurer", "policy_number", "issue_date", "expiry_date"), vals)),
                   commit=False)
    else:
        cur = db.execute("INSERT INTO logs_records (insurer, policy_number, issue_date, expiry_date) VALUES (?,?,?,?)",
                         vals)
        log_action("logs_create", "coida", "Added Letter of Good Standing %s" % vals[1], entity="logs",
                   entity_id=cur.lastrowid, commit=False)
    db.commit()
    flash("Letter of Good Standing saved.", "success")
    return redirect(url_for("coida"))


# ─── Legacy report URLs → the record workflow ────────────────────────────────

@app.route("/ee/generate", methods=["POST"])
@app.route("/wsp/generate", methods=["POST"])
@app.route("/bbbee/generate", methods=["POST"])
@app.route("/coida/generate", methods=["POST"])
@login_required
def legacy_generate():
    flash("Reports are now generated, versioned and stored on each compliance record.", "info")
    return redirect(url_for("compliance_list"))


# ─── Checklist API ───────────────────────────────────────────────────────────

def _safe_back(default):
    """Go back to the referring page only if it is on this site (no open redirect)."""
    from urllib.parse import urlparse
    ref = request.referrer
    if ref:
        u = urlparse(ref)
        if u.netloc == request.host and u.scheme in ("http", "https") and u.path.startswith("/"):
            return u.path + ("?" + u.query if u.query else "")
    return default


def _checklist_scope(db, scope, for_write=True):
    """Validate a checklist scope string ('record:<id>'). Returns (record, checklist)
    or aborts. Writes are refused once the record is submitted/archived."""
    from modules.checklists import get_checklist
    if not scope or not scope.startswith("record:"):
        abort(400, description="Unknown checklist.")
    rid = _int_or_none(scope.split(":", 1)[1])
    r = rec.get_record(db, rid) if rid else None
    if r is None or r["deleted"]:
        abort(404, description="Unknown checklist.")
    checklist = get_checklist(r["module"])
    if checklist is None:
        abort(400, description="This module has no checklist.")
    if for_write and r["status"] in rec.CHECKLIST_LOCKED:
        abort(409, description="The checklist was frozen when the record was submitted.")
    return r, checklist


def _checklist_progress_json(db, scope, checklist):
    done, total, pct = checklist_progress(checklist, get_checklist_states(db, scope), get_custom_tasks(db, scope),
                                          get_hidden_tasks(db, scope))
    return {"ok": True, "done": done, "total": total, "percent": pct}


def _valid_item_key(db, scope, checklist, item_key):
    if item_key.startswith("custom-"):
        cid = _int_or_none(item_key.split("-", 1)[1])
        return bool(cid) and db.execute("SELECT 1 FROM custom_tasks WHERE id=? AND module=?", (cid, scope)).fetchone()
    return any(it["key"] == item_key for st in checklist["steps"] for it in st["items"])


@app.route("/checklist/toggle", methods=["POST"])
@login_required
def checklist_toggle():
    db = get_db()
    data = request.get_json(silent=True) or {}
    scope = str(data.get("module", ""))
    item_key = str(data.get("item_key", ""))[:60]
    r, checklist = _checklist_scope(db, scope)
    if not _valid_item_key(db, scope, checklist, item_key):
        return jsonify({"ok": False, "error": "Unknown checklist item."}), 400
    checked = 1 if data.get("checked") else 0
    db.execute(
        """INSERT INTO checklist_state (module, item_key, checked, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(module, item_key)
           DO UPDATE SET checked=excluded.checked, updated_at=datetime('now')""",
        (scope, item_key, checked),
    )
    log_action("task_toggle", "task", "%s task '%s' in %s" % (
        "Completed" if checked else "Un-checked", resolve_item_text(db, scope, item_key), friendly_scope(db, scope)),
        target=scope, entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    return jsonify(_checklist_progress_json(db, scope, checklist))


@app.route("/checklist/reset/<path:module>", methods=["POST"])
@login_required
def checklist_reset(module):
    db = get_db()
    r, _cl = _checklist_scope(db, module)
    states = get_checklist_states(db, module)
    db.execute("DELETE FROM checklist_state WHERE module=?", (module,))
    rec.add_event(db, r["id"], "checklist_reset", g.user, comment="Checklist progress reset",
                  payload={"previous_states": states})
    log_action("task_reset", "task", "Reset all checklist progress for %s" % friendly_scope(db, module),
               target=module, entity="compliance_record", entity_id=r["id"], before=states, commit=False)
    db.commit()
    flash("Checklist reset (custom tasks kept; the previous progress is kept in the record's history).", "info")
    return redirect(_safe_back(url_for("record_detail", record_id=r["id"]) + "#checklist"))


@app.route("/checklist/task/add", methods=["POST"])
@login_required
def checklist_task_add():
    db = get_db()
    data = request.get_json(silent=True) or request.form
    scope = (data.get("module") or "").strip()
    step_key = (data.get("step_key") or "").strip()[:20]
    text = (data.get("text") or "").strip()[:300]
    r, checklist = _checklist_scope(db, scope)
    if not text or not any(st["key"] == step_key for st in checklist["steps"]):
        return jsonify({"ok": False, "error": "A valid step and task text are required."}), 400
    cur = db.execute("INSERT INTO custom_tasks (module, step_key, text) VALUES (?,?,?)", (scope, step_key, text))
    new_id = cur.lastrowid
    log_action("task_add", "task", "Added custom task '%s' in %s" % (text, friendly_scope(db, scope)), target=scope,
               entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    out = _checklist_progress_json(db, scope, checklist)
    out["task"] = {"id": new_id, "text": text, "key": "custom-%d" % new_id}
    return jsonify(out)


@app.route("/checklist/task/delete", methods=["POST"])
@login_required
def checklist_task_delete():
    db = get_db()
    data = request.get_json(silent=True) or request.form
    scope = (data.get("module") or "").strip()
    task_id = _int_or_none(data.get("task_id"))
    r, checklist = _checklist_scope(db, scope)
    if not task_id:
        return jsonify({"ok": False, "error": "task_id required"}), 400
    deleted_text = resolve_item_text(db, scope, "custom-%s" % task_id)
    db.execute("DELETE FROM custom_tasks WHERE id=? AND module=?", (task_id, scope))
    db.execute("DELETE FROM checklist_state WHERE module=? AND item_key=?", (scope, "custom-%s" % task_id))
    log_action("task_delete", "task", "Deleted custom task '%s' in %s" % (deleted_text, friendly_scope(db, scope)),
               target=scope, entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    return jsonify(_checklist_progress_json(db, scope, checklist))


@app.route("/checklist/task/hide", methods=["POST"])
@login_required
def checklist_task_hide():
    db = get_db()
    data = request.get_json(silent=True) or request.form
    scope = (data.get("module") or "").strip()
    item_key = (data.get("item_key") or "").strip()[:60]
    r, checklist = _checklist_scope(db, scope)
    if not _valid_item_key(db, scope, checklist, item_key) or item_key.startswith("custom-"):
        return jsonify({"ok": False, "error": "Unknown checklist item."}), 400
    db.execute("INSERT INTO hidden_tasks (module, item_key) VALUES (?, ?) ON CONFLICT(module, item_key) DO NOTHING",
               (scope, item_key))
    db.execute("DELETE FROM checklist_state WHERE module=? AND item_key=?", (scope, item_key))
    log_action("task_hide", "task", "Removed default task '%s' from %s" % (resolve_item_text(db, scope, item_key),
               friendly_scope(db, scope)), target=scope, entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    return jsonify(_checklist_progress_json(db, scope, checklist))


@app.route("/checklist/restore-defaults/<path:module>", methods=["POST"])
@login_required
def checklist_restore_defaults(module):
    db = get_db()
    r, _cl = _checklist_scope(db, module)
    db.execute("DELETE FROM hidden_tasks WHERE module=?", (module,))
    log_action("task_restore_defaults", "task", "Restored hidden default tasks for %s" % friendly_scope(db, module),
               target=module, entity="compliance_record", entity_id=r["id"], commit=False)
    db.commit()
    flash("Default tasks restored.", "info")
    return redirect(_safe_back(url_for("record_detail", record_id=r["id"]) + "#checklist"))


# ─── Audit log (admin only) ──────────────────────────────────────────────────

AUDIT_CATEGORIES = [
    ("auth", "Authentication"),
    ("project", "Projects"),
    ("task", "Tasks"),
    ("trash", "Trash bin"),
    ("user", "User management"),
    ("compliance", "Compliance records"),
    ("report", "Reports"),
    ("employee", "Employees"),
    ("company", "Company profile"),
    ("rules", "Regulatory rules"),
    ("wsp", "WSP / ATR"),
    ("coida", "COIDA"),
    ("bbbee", "B-BBEE"),
]


@app.route("/audit", methods=["GET"])
@admin_required
def audit():
    db = get_db()

    # Filters
    f_user = request.args.get("user", "").strip()
    f_category = request.args.get("category", "").strip()
    f_search = request.args.get("q", "").strip()
    f_from = request.args.get("from", "").strip()
    f_to = request.args.get("to", "").strip()

    where, params = [], []
    if f_user:
        where.append("username = ?")
        params.append(f_user)
    if f_category:
        where.append("category = ?")
        params.append(f_category)
    if f_search:
        where.append("(detail LIKE ? OR action LIKE ? OR target LIKE ?)")
        like = "%" + f_search + "%"
        params += [like, like, like]
    if f_from:
        where.append("date(ts) >= date(?)")
        params.append(f_from)
    if f_to:
        where.append("date(ts) <= date(?)")
        params.append(f_to)

    sql = "SELECT * FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 1000"
    entries = db.execute(sql, params).fetchall()

    # Distinct users for the filter dropdown
    users = [r["username"] for r in db.execute(
        "SELECT DISTINCT username FROM audit_log ORDER BY username"
    ).fetchall()]

    total_entries = db.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]

    return render_template(
        "audit.html",
        entries=entries,
        users=users,
        categories=AUDIT_CATEGORIES,
        total_entries=total_entries,
        shown=len(entries),
        f_user=f_user, f_category=f_category, f_search=f_search,
        f_from=f_from, f_to=f_to,
        any_filter=bool(where),
    )


# ─── User management (admin only) ────────────────────────────────────────────

@app.route("/users", methods=["GET"])
@admin_required
def users_admin():
    db = get_db()
    rows = db.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    return render_template("users.html", users=rows)


USERNAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-@")


@app.route("/users/add", methods=["POST"])
@admin_required
def users_add():
    db = get_db()
    username = (request.form.get("username") or "").strip()
    full_name = (request.form.get("full_name") or "").strip()[:120]
    email = (request.form.get("email") or "").strip()[:254]
    password = request.form.get("password") or ""
    role = request.form.get("role") or "user"
    if role not in ("admin", "user"):
        role = "user"
    if not username or not password:
        flash("Username and password are required.", "danger")
        return redirect(url_for("users_admin"))
    if len(username) > 64 or not set(username) <= USERNAME_OK:
        flash("Username may only contain letters, digits and . _ - @ (max 64).", "danger")
        return redirect(url_for("users_admin"))
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1] or " " in email):
        flash("That email address is not valid.", "danger")
        return redirect(url_for("users_admin"))
    problem = password_problem(password, username)
    if problem:
        flash(problem, "danger")
        return redirect(url_for("users_admin"))
    existing = db.execute("SELECT id FROM users WHERE lower(username)=lower(?)", (username,)).fetchone()
    if existing:
        flash("That username already exists.", "danger")
        return redirect(url_for("users_admin"))
    cur = db.execute(
        """INSERT INTO users (username, password_hash, role, full_name, email, must_change_password)
           VALUES (?,?,?,?,?,1)""",
        (username, generate_password_hash(password), role, full_name or username, email or None),
    )
    log_action("user_create", "user", f"Created {role} account '{username}'", target=username,
               entity="user", entity_id=cur.lastrowid,
               after={"username": username, "role": role, "full_name": full_name, "email": email},
               commit=False)
    db.commit()
    flash(f"User '{username}' created ({role}). They must change the password at first sign-in.", "success")
    return redirect(url_for("users_admin"))


@app.route("/users/<int:user_id>/reset", methods=["POST"])
@admin_required
def users_reset(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("users_admin"))
    new_password = request.form.get("password") or ""
    problem = password_problem(new_password, user["username"]) if new_password else "Please provide a new password."
    if problem:
        flash(problem, "danger")
        return redirect(url_for("users_admin"))
    db.execute(
        """UPDATE users SET password_hash=?, must_change_password=1, failed_logins=0, locked_until=NULL,
           password_changed_at=datetime('now'), session_version=session_version+1 WHERE id=?""",
        (generate_password_hash(new_password), user_id))
    log_action("user_reset_password", "user",
               f"Reset password for '{user['username']}' (signed out everywhere)", target=user["username"],
               entity="user", entity_id=user_id, commit=False)
    db.commit()
    flash(f"Password reset for '{user['username']}'. They must change it at next sign-in.", "success")
    return redirect(url_for("users_admin"))


def _active_admin_count(db):
    return db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]


@app.route("/users/<int:user_id>/toggle-role", methods=["POST"])
@admin_required
def users_toggle_role(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("users_admin"))
    if user["id"] == g.user["id"]:
        flash("You cannot change your own role. Ask another administrator.", "danger")
        return redirect(url_for("users_admin"))
    new_role = "user" if user["role"] == "admin" else "admin"
    if user["role"] == "admin" and user["active"] and _active_admin_count(db) <= 1:
        flash("Cannot demote the last administrator.", "danger")
        return redirect(url_for("users_admin"))
    db.execute("UPDATE users SET role=?, session_version=session_version+1 WHERE id=?", (new_role, user_id))
    log_action("user_role_change", "user",
               f"Changed role of '{user['username']}' to {new_role}", target=user["username"],
               entity="user", entity_id=user_id,
               before={"role": user["role"]}, after={"role": new_role}, commit=False)
    db.commit()
    flash(f"'{user['username']}' is now {new_role}.", "success")
    return redirect(url_for("users_admin"))


@app.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def users_toggle_active(user_id):
    """Disable/enable an account. Disabling keeps the user's history intact and is
    the normal way to remove access."""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("users_admin"))
    if user["id"] == g.user["id"]:
        flash("You cannot disable your own account.", "danger")
        return redirect(url_for("users_admin"))
    if user["active"] and user["role"] == "admin" and _active_admin_count(db) <= 1:
        flash("Cannot disable the last administrator.", "danger")
        return redirect(url_for("users_admin"))
    new_active = 0 if user["active"] else 1
    db.execute("UPDATE users SET active=?, session_version=session_version+1 WHERE id=?",
               (new_active, user_id))
    log_action("user_enable" if new_active else "user_disable", "user",
               f"{'Enabled' if new_active else 'Disabled'} account '{user['username']}'",
               target=user["username"], entity="user", entity_id=user_id,
               before={"active": user["active"]}, after={"active": new_active}, commit=False)
    db.commit()
    flash(f"'{user['username']}' {'enabled' if new_active else 'disabled'}.", "success")
    return redirect(url_for("users_admin"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def users_delete(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("users_admin"))
    if user["id"] == g.user["id"]:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("users_admin"))
    if user["role"] == "admin" and user["active"] and _active_admin_count(db) <= 1:
        flash("Cannot delete the last administrator.", "danger")
        return redirect(url_for("users_admin"))
    try:
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
        log_action("user_delete", "user", f"Deleted account '{user['username']}'", target=user["username"],
                   entity="user", entity_id=user_id,
                   before={"username": user["username"], "role": user["role"]}, commit=False)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"'{user['username']}' owns compliance history and cannot be deleted. Disable the account instead.",
              "warning")
        return redirect(url_for("users_admin"))
    flash(f"User '{user['username']}' deleted.", "warning")
    return redirect(url_for("users_admin"))


# ── Template context processor ───────────────────────────────

# ─── Navigation model (primary top nav + contextual secondary nav) ───────────

def _nav_sections(is_admin):
    """Primary sections; each carries the secondary (sub) nav shown when it is active."""
    compliance = [("Compliance Records", "compliance_list", "folder2")]
    if is_admin:  # Rules Register is admin-only (governance of statutory rule-sets)
        compliance.append(("Rules Register", "rules_register", "journal-check"))
    compliance += [
        ("Employment Equity", "ee", "bar-chart-steps"),
        ("WSP / ATR", "wsp", "mortarboard"),
        ("B-BBEE", "bbbee", "award"),
        ("COIDA", "coida", "shield-check"),
        ("GBS", "gbs", "globe2"),
        ("CIPC Beneficial Ownership", "compliance_list", "diagram-3"),
    ]
    sections = [
        {"key": "dashboard", "label": "Dashboard", "icon": "house-door", "endpoint": "dashboard",
         "secondary": [("Compliance Overview", "dashboard", "grid-1x2")] + compliance},
        {"key": "employees", "label": "Employees", "icon": "people", "endpoint": "employees",
         "secondary": [("All Employees", "employees", "people")]},
        {"key": "compliance", "label": "Compliance", "icon": "clipboard2-check", "endpoint": "compliance_list",
         "secondary": compliance},
        {"key": "reports", "label": "Reports", "icon": "file-earmark-bar-graph", "endpoint": "reports_hub",
         "secondary": [("Reports & Insights", "reports_hub", "file-earmark-bar-graph"),
                       ("Training Report", "wsp_dashboard", "mortarboard"),
                       ("Employee Demographics", "ee", "people"),
                       ("Submission History", "compliance_list", "clock-history")]},
        {"key": "documents", "label": "Documents", "icon": "folder2-open", "endpoint": "wsp_documents_repo",
         "secondary": [("Document Repository", "wsp_documents_repo", "folder2-open")]},
    ]
    if is_admin:
        sections.append({"key": "administration", "label": "Administration", "icon": "gear", "endpoint": "users_admin",
                         "secondary": [("Users", "users_admin", "people"), ("Audit Log", "audit", "clock-history"),
                                       ("Trash Bin", "trash", "trash3"), ("Company Profile", "company", "building")]})
    return sections


_SECTION_RULES = [
    ("administration", ("users_admin", "audit", "trash")),
    ("documents", ("wsp_documents_repo",)),
    ("reports", ("reports_hub",)),
    ("employees", ("employee", "company")),
    ("compliance", ("compliance", "record", "rules", "ee", "wsp", "bbbee", "coida", "gbs", "cipc")),
]


def _active_section(endpoint):
    e = endpoint or ""
    for key, names in _SECTION_RULES:
        if any(e == n or e.startswith(n) for n in names):
            return key
    return "dashboard"


@app.context_processor
def inject_now():
    import random
    from modules.quotes import COMPLIANCE_QUOTES
    is_admin = bool(g.get("user")) and g.user["role"] == "admin"
    sections = _nav_sections(is_admin)
    active = _active_section(request.endpoint if request else None)
    secondary = next((s["secondary"] for s in sections if s["key"] == active), [])
    return {
        "hero_quote": random.choice(COMPLIANCE_QUOTES),
        "now": datetime.now,
        "current_user": session.get("username", ""),
        "current_full_name": session.get("full_name", session.get("username", "")),
        "current_role": g.user["role"] if g.get("user") else "",
        "is_admin": is_admin,
        "session_remember": session.get("remember", False),
        "idle_warning_after": IDLE_WARNING_AFTER,
        "logout_countdown": LOGOUT_COUNTDOWN,
        "csrf_token": csrf_token,
        "nav_sections": sections,
        "active_section": active,
        "secondary_nav": secondary,
    }


@app.route("/healthz")
def healthz():
    """Container health check: the app is up and the database answers."""
    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return jsonify({"ok": False}), 503
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Local development only. Production runs under gunicorn (see Dockerfile).
    # Bound to localhost; the interactive debugger is never exposed to the network.
    logging.basicConfig(level=logging.INFO)
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="127.0.0.1", port=5000)
