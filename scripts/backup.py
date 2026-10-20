"""Back up everything the app stores: the SQLite database plus all files under DATA_DIR.

Usage (inside the container, or locally with HR_DATA_DIR set):
    python scripts/backup.py [--dest DIR] [--keep N]

Produces DIR/hrcs-backup-YYYYmmdd-HHMMSS.tar.gz containing:
    hr_audit.db        consistent snapshot taken with SQLite's online backup API
    files/...          uploads, stored reports, evidence (everything else in DATA_DIR)
    MANIFEST.json      file list with SHA-256 hashes, used by restore to verify
The archive is written to a temporary name and renamed only when complete, so an
interrupted run never leaves a file that looks like a good backup.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime

SKIP = {"hr_audit.db", "hr_audit.db-wal", "hr_audit.db-shm", "backups", ".secret_key",
        "initial_admin_password.txt", "import_reports"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    data_dir = os.environ.get("HR_DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "data")
    data_dir = os.path.abspath(data_dir)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.path.join(data_dir, "backups"))
    ap.add_argument("--keep", type=int, default=14, help="number of archives to keep (0 = all)")
    ap.add_argument("--include-secret", action="store_true",
                    help="also back up .secret_key (sessions survive a restore)")
    args = ap.parse_args()

    db_path = os.path.join(data_dir, "hr_audit.db")
    if not os.path.exists(db_path):
        sys.exit("No database at %s" % db_path)
    os.makedirs(args.dest, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = os.path.join(args.dest, "hrcs-backup-%s.tar.gz" % stamp)

    with tempfile.TemporaryDirectory() as tmp:
        snap = os.path.join(tmp, "hr_audit.db")
        src, dst = sqlite3.connect(db_path), sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        check = sqlite3.connect(snap)
        try:
            result = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if result != "ok":
            sys.exit("Database integrity check failed: %s" % result)

        manifest = {"created": stamp, "files": {"hr_audit.db": sha256(snap)}}
        members = [(snap, "hr_audit.db")]
        backup_dir_abs = os.path.abspath(args.dest)
        for root, dirs, files in os.walk(data_dir):
            if os.path.abspath(root).startswith(backup_dir_abs):
                dirs[:] = []
                continue
            rel_root = os.path.relpath(root, data_dir)
            if rel_root == ".":
                dirs[:] = [d for d in dirs if d not in SKIP]
            for name in files:
                if rel_root == "." and name in SKIP and not (args.include_secret and name == ".secret_key"):
                    continue
                full = os.path.join(root, name)
                arc = os.path.normpath(os.path.join("files", rel_root, name)).replace("\\", "/")
                manifest["files"][arc] = sha256(full)
                members.append((full, arc))

        mpath = os.path.join(tmp, "MANIFEST.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1)

        partial = final + ".partial"
        with tarfile.open(partial, "w:gz") as tar:
            for full, arc in members:
                tar.add(full, arcname=arc)
            tar.add(mpath, arcname="MANIFEST.json")
        os.replace(partial, final)

    if args.keep:
        archives = sorted(f for f in os.listdir(args.dest) if f.startswith("hrcs-backup-") and f.endswith(".tar.gz"))
        for old in archives[:-args.keep]:
            os.remove(os.path.join(args.dest, old))

    print("Backup written: %s (%d files)" % (final, len(manifest["files"])))


if __name__ == "__main__":
    main()
