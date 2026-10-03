"""Background workers for async processing (OCR, LLM, PDF).

Architecture:
- Celery app with Redis broker
- Tasks: extraction, billing webhook, PDF generation
- Result backend: Redis or RPC (for simple status tracking)
"""
from .celery_app import app as celery_app

__all__ = ["celery_app"]
