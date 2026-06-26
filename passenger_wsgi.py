"""
WSGI entry point for cPanel / Phusion Passenger.

cPanel's "Setup Python App" looks for this file and an `application` callable.
It imports the Flask app from app.py and disables debug mode for production.

Local development is unaffected — keep using `python app.py` (or start.bat),
which still runs the built-in server with debug on.
"""
import os
import sys

# Ensure the app directory is importable
sys.path.insert(0, os.path.dirname(__file__))

from app import app as application  # noqa: E402

# Production hardening
application.debug = False

# Use a secret key from the environment if provided (set this in cPanel)
_secret = os.environ.get("HR_AUDIT_SECRET")
if _secret:
    application.secret_key = _secret
