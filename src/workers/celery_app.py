"""Celery application configuration for background workers.

Environment variables:
- REDIS_URL: Redis broker URL (default: redis://localhost:6379/0)
- CELERY_RESULT_BACKEND: Result backend (default: redis://localhost:6379/1)
- CELERY_TASK serializer: JSON serialization

Features:
- Task retry with exponential backoff
- Dead letter queue for failed tasks
- Rate limiting per task type
- Task priority queue
"""
from __future__ import annotations

import os
try:
    from celery import Celery
    from celery.signals import task_failure
except ImportError:  # Optional dependency for API-only/local test installs.
    Celery = None
    task_failure = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
WORKER_ENABLED = os.getenv("WORKER_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

if Celery is not None:
    app = Celery("invoice_workers")
else:
    class _UnavailableTask:
        def delay(self, *args, **kwargs):
            raise RuntimeError("Celery is not installed; configure the worker image first")

        def __call__(self, fn):
            return fn

    class _UnavailableCelery:
        def task(self, *args, **kwargs):
            return _UnavailableTask()

        def autodiscover_tasks(self, *args, **kwargs):
            return None

        @property
        def conf(self):
            return {}

    app = _UnavailableCelery()

if Celery is not None:
    app.conf.update(
    # Broker
    broker_url=REDIS_URL,
    result_backend=RESULT_BACKEND,

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timeouts
    task_soft_time_limit=300,  # 5 minutes soft limit
    task_time_limit=600,       # 10 minutes hard limit

    # Retry policy
    task_default_retry_delay=60,  # 1 minute
    task_max_retries=3,

    # Worker settings
    worker_prefetch_multiplier=1,  # Fair scheduling
    worker_max_tasks_per_child=100,  # Recycle workers

    # Task routes — separate queues for different task types
    task_routes={
        "src.workers.extraction_worker.*": {"queue": "extraction"},
        "src.workers.billing_worker.*": {"queue": "billing"},
        "src.workers.pdf_worker.*": {"queue": "pdf"},
    },

    # Default queue
    task_default_queue="default",

    # Result expiration
    result_expires=3600,  # 1 hour

    # Beat schedule (for periodic tasks)
    beat_schedule={
        "cleanup-expired-tasks": {
            "task": "src.workers.cleanup_worker.cleanup_expired",
            "schedule": 3600.0,  # Every hour
        },
    },
)

# ---------------------------------------------------------------------------
# Auto-discover tasks
# ---------------------------------------------------------------------------
app.autodiscover_tasks(["src.workers"])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
if task_failure is not None:
    @task_failure.connect
    def handle_task_failure(sender, task_id, exception, traceback, **kwargs):
        """Log task failures and optionally send alerts."""
        import logging
        logger = logging.getLogger(__name__)
        logger.error(
            f"Task {sender.name}[{task_id}] failed: {exception}",
            exc_info=True,
        )
