"""Gunicorn settings for the container.

One worker process with threads: SQLite allows a single writer, and the app's
start-up migration must not race between several worker processes.
"""
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 120          # large report generation
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
# Do not log query strings: they could carry employee data in future filters.
access_log_format = '%(h)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(L)ss'
forwarded_allow_ips = "*"   # Coolify's proxy is the only route in
