"""Local development server: plain HTTP on 127.0.0.1:5000.

    python scripts/dev_server.py [DATA_DIR]

Uses DATA_DIR (default ./data) and disables the Secure cookie flag, which is
needed for http://localhost. Never use this for a deployment; production runs
under gunicorn in Docker.
"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.chdir(ROOT)
if len(sys.argv) > 1:
    os.environ["HR_DATA_DIR"] = sys.argv[1]
os.environ["HR_COOKIE_SECURE"] = "0"

import logging  # noqa: E402

import app  # noqa: E402

logging.basicConfig(level=logging.INFO)
app.app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
