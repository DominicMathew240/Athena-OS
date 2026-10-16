"""Gunicorn settings for the Athena console (every value can be overridden by env)."""

import multiprocessing
import os

bind = os.environ.get("ATHENA_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("ATHENA_WEB_WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("ATHENA_WEB_THREADS", "2"))
# Image batches are up to 100 MB; give slow uploads room before a worker is recycled.
timeout = int(os.environ.get("ATHENA_WEB_TIMEOUT", "120"))
graceful_timeout = 30
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = os.environ.get("ATHENA_FORWARDED_ALLOW_IPS", "127.0.0.1")
