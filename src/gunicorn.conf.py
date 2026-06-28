"""Gunicorn configuration for production / staging deployments.

Tuned for low-throughput CPU-bound workloads (most requests dispatch a
Celery task and return immediately). Increase 'workers' for IO-heavy
traffic or pin via environment when running under Docker.
"""

bind = "0.0.0.0:8000"
worker_class = "sync"
workers = 2
accesslog = "-"
timeout = 120
