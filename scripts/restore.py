"""Restore a backup made by scripts/backup.py.

Usage (with the app STOPPED):
    python scripts/restore.py path/to/hrcs-backup-YYYYmmdd-HHMMSS.tar.gz [--yes]

Steps: verify every file against MANIFEST.json and run an SQLite integrity check
in a scratch directory first; only if everything verifies is the current data moved
aside to DATA_DIR/backups/pre-restore-<time>/ and the backup put in place. Nothing
is deleted, so a bad restore can be undone by moving that folder back.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_members(tar, root):
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(root, m.name))
        if not target.startswith(os.path.realpath(root) + os.sep) or m.issym() or m.islnk():
            sys.exit("Refusing unsafe path in archive: %s" % m.name)
        yield m


def main():
    data_dir = os.path.abspath(os.environ.get("HR_DATA_DIR")
                               or os.path.join(os.path.dirname(__file__), "..", "data"))
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--passphrase", default=os.environ.get("HR_BACKUP_PASSPHRASE"),
                    help="passphrase for an encrypted backup (default: HR_BACKUP_PASSPHRASE env)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        import _crypto
        archive = args.archive
        if _crypto.is_encrypted(archive):
            if not args.passphrase:
                sys.exit("This backup is encrypted. Provide --passphrase or set HR_BACKUP_PASSPHRASE.")
            archive = os.path.join(tmp, "decrypted.tar.gz")
            try:
                _crypto.decrypt_file(args.archive, archive, args.passphrase)
            except ValueError as e:
                sys.exit(str(e))
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp, members=list(safe_members(tar, tmp)))
        mpath = os.path.join(tmp, "MANIFEST.json")
        if not os.path.exists(mpath):
            sys.exit("Not a valid backup: MANIFEST.json missing.")
        manifest = json.load(open(mpath, encoding="utf-8"))
        for arc, digest in manifest["files"].items():
            p = os.path.join(tmp, arc)
            if not os.path.exists(p) or sha256(p) != digest:
                sys.exit("Backup is incomplete or corrupt: %s does not match its checksum." % arc)
        db = sqlite3.connect(os.path.join(tmp, "hr_audit.db"))
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                sys.exit("Database in backup fails integrity check.")
        finally:
            db.close()
        print("Backup verified: %d files, created %s." % (len(manifest["files"]), manifest["created"]))

        if not args.yes and input("Replace the current data in %s? Type RESTORE: " % data_dir) != "RESTORE":
            sys.exit("Cancelled.")

        aside = os.path.join(data_dir, "backups", "pre-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(aside, exist_ok=True)
        for name in os.listdir(data_dir):
            if name in ("backups", ".secret_key"):
                continue
            shutil.move(os.path.join(data_dir, name), os.path.join(aside, name))

        shutil.copy2(os.path.join(tmp, "hr_audit.db"), os.path.join(data_dir, "hr_audit.db"))
        files_root = os.path.join(tmp, "files")
        if os.path.isdir(files_root):
            for item in os.listdir(files_root):
                src = os.path.join(files_root, item)
                dst = os.path.join(data_dir, item)
                if item == ".secret_key" and os.path.exists(dst):
                    continue
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
    print("Restored. Previous data kept in %s. Start the app now." % aside)


if __name__ == "__main__":
    main()
