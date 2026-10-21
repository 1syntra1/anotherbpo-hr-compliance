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
# Trust X-Forwarded-* only from the proxy. Default "*" is safe while the container port is
# reachable only from Coolify's internal network; if the port is ever exposed directly, set
# FORWARDED_ALLOW_IPS to the proxy IP/CIDR so clients cannot spoof the scheme or client IP.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
# Bound request sizes to blunt header-based resource exhaustion.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190
