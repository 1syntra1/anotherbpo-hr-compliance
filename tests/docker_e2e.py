"""End-to-end container test (Stage 13). Not collected by pytest; run directly:

    python tests/docker_e2e.py [image]

Builds nothing: expects the image (default hrcs:test) to exist. Uses a throwaway
named volume and port 18000, and removes both afterwards.
"""
import http.cookiejar
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

IMAGE = sys.argv[1] if len(sys.argv) > 1 else "hrcs:test"
VOL = "hrcs_e2e_data"
PORT = 18000
BASE = "http://127.0.0.1:%d" % PORT
results = []
EMP = {"last_name": "Check", "race": "African", "gender": "Female", "occupational_level": "Unskilled",
       "employment_date": "2021-01-01", "salary": "9000", "employment_type": "Permanent", "is_foreign_national": "No"}


def sh(*args, check=True):
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError("docker %s failed: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s  %s %s" % ("PASS" if ok else "FAIL", name, detail))


def start(name):
    sh("run", "-d", "--name", name, "-p", "%d:8000" % PORT, "-e", "HR_COOKIE_SECURE=0",
       "-v", "%s:/data" % VOL, IMAGE)
    for _ in range(60):
        try:
            if urllib.request.urlopen(BASE + "/healthz", timeout=2).status == 200:
                return
        except Exception:
            time.sleep(1)
    raise RuntimeError("container did not become healthy:\n" + sh("logs", name, check=False))


class Browser:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                              NoRedirect())

    def get(self, path):
        try:
            r = self.op.open(BASE + path, timeout=10)
            return r.status, r.read().decode("utf-8", "replace"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers

    def post(self, path, data, token_from="/"):
        _, html, _ = self.get(token_from)
        m = re.search(r'name="_csrf" value="([^"]+)"', html)
        data = dict(data, _csrf=m.group(1) if m else "")
        req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(data).encode())
        try:
            r = self.op.open(req, timeout=10)
            return r.status, r.read().decode("utf-8", "replace"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), e.headers


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def cleanup():
    for n in ("hrcs_e2e_1", "hrcs_e2e_2", "hrcs_e2e_3", "hrcs_e2e_up"):
        sh("rm", "-f", n, check=False)
    sh("volume", "rm", "-f", VOL, check=False)


def main():
    cleanup()
    sh("volume", "create", VOL)
    try:
        start("hrcs_e2e_1")
        record("container starts and /healthz answers", True)

        uid = sh("exec", "hrcs_e2e_1", "id", "-u")
        record("runs as non-root", uid != "0", "uid=%s" % uid)

        w = subprocess.run(["docker", "exec", "hrcs_e2e_1", "sh", "-c", "touch /app/x"], capture_output=True)
        record("application code is not writable", w.returncode != 0)

        status = sh("inspect", "-f", "{{.State.Health.Status}}", "hrcs_e2e_1", check=False)
        record("Docker HEALTHCHECK defined", status in ("healthy", "starting"), status)

        creds = sh("exec", "hrcs_e2e_1", "cat", "/data/initial_admin_password.txt")
        pw = re.search(r"Password: (\S+)", creds).group(1)
        record("first admin password generated into volume (not in image)", bool(pw))

        b = Browser()
        st, _, hdr = b.post("/login", {"username": "admin", "password": pw}, token_from="/login")
        record("login with generated password", st == 302 and "/account/password" in hdr.get("Location", ""))
        newpw = "Container-Test-Passw0rd-1"
        st, _, _ = b.post("/account/password", {"current_password": pw, "new_password": newpw,
                                                "confirm_password": newpw}, token_from="/account/password")
        record("forced password change", st == 302)
        st, _, _ = b.post("/employees", dict(EMP, first_name="Persist", emp_number="E2E-1"),
                          token_from="/employees")
        st, html, _ = b.get("/employees")
        record("employee created", "Persist" in html)

        static_status = b.get("/static/vendor/bootstrap/bootstrap.min.css")[0]
        record("static assets served locally", static_status == 200)

        out = sh("exec", "hrcs_e2e_1", "python", "scripts/backup.py")
        record("backup script runs in container", "Backup written" in out, out)
        backup_name = re.search(r"(hrcs-backup-\S+\.tar\.gz)", out).group(1)

        sh("restart", "hrcs_e2e_1")
        for _ in range(30):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=2); break
            except Exception:
                time.sleep(1)
        st, html, _ = b.get("/employees")
        record("container restart: data and session survive", st == 200 and "Persist" in html)

        sh("rm", "-f", "hrcs_e2e_1")
        start("hrcs_e2e_2")
        st, html, _ = b.get("/employees")
        record("container replaced (redeploy): data and session survive", st == 200 and "Persist" in html)
        b2 = Browser()
        st, _, _ = b2.post("/login", {"username": "admin", "password": newpw}, token_from="/login")
        record("changed password survives redeploy", st == 302)

        b.post("/employees", dict(EMP, first_name="AfterBackup", emp_number="E2E-2"),
               token_from="/employees")
        sh("stop", "hrcs_e2e_2")
        sh("run", "--rm", "-v", "%s:/data" % VOL, IMAGE, "python", "scripts/restore.py",
           "/data/backups/" + backup_name, "--yes")
        sh("rm", "-f", "hrcs_e2e_2")
        start("hrcs_e2e_3")
        b3 = Browser()
        b3.post("/login", {"username": "admin", "password": newpw}, token_from="/login")
        st, html, _ = b3.get("/employees")
        record("restore brings back backed-up state", "Persist" in html and "AfterBackup" not in html)

        logs = sh("logs", "hrcs_e2e_3", check=False)
        record("no passwords in container logs", newpw not in logs and pw not in logs)

        # Upgrade path: a pre-versioning (POC) database is migrated in place, with a backup first
        legacy = sys.argv[2] if len(sys.argv) > 2 else None
        if legacy:
            sh("rm", "-f", "hrcs_e2e_3")
            sh("volume", "rm", "-f", VOL)
            sh("volume", "create", VOL)
            sh("create", "--name", "hrcs_e2e_up", "-v", "%s:/data" % VOL, IMAGE)
            sh("cp", legacy, "hrcs_e2e_up:/data/hr_audit.db")
            sh("rm", "-f", "hrcs_e2e_up")
            sh("run", "--rm", "-u", "0", "-v", "%s:/data" % VOL, IMAGE, "chown", "-R", "10001:10001", "/data")
            start("hrcs_e2e_3")
            out = sh("exec", "hrcs_e2e_3", "python", "-c",
                     "import sqlite3,os;d=sqlite3.connect('/data/hr_audit.db');"
                     "print(d.execute('select max(version) from schema_migrations').fetchone()[0],"
                     "d.execute('select count(*) from compliance_records').fetchone()[0],"
                     "d.execute('pragma integrity_check').fetchone()[0],"
                     "len([f for f in os.listdir('/data/backups') if f.startswith('hr_audit-pre-migration')]))")
            ver, recs, integ, backups = out.split()
            record("legacy POC database upgraded in place", ver == "3" and int(recs) > 0 and integ == "ok",
                   "schema v%s, %s records, integrity %s" % (ver, recs, integ))
            record("pre-migration backup written before upgrade", int(backups) >= 1)
    finally:
        cleanup()

    failed = [r for r in results if not r[1]]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
