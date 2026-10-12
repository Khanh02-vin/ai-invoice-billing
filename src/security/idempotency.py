"""Idempotency helpers — deterministic key gen + in-memory dedup cache.

RedisCache/IdempotencyMiddleware removed: no redis dep in requirements and
middleware never add_middleware'd in app.py (Ponytail audit sweep).
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# In-Memory Cache (production uses app-level dedup via DB rows)
# ---------------------------------------------------------------------------

class InMemoryCache:
    """Thread-safe in-memory cache for dev/test idempotency dedup."""

    def __init__(self):
        self._store: Dict[str, Tuple[float, Dict]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Dict]:
        with self._lock:
            if key in self._store:
                expires, data = self._store[key]
                if time.time() < expires:
                    return data
                del self._store[key]
        return None

    def set(self, key: str, value: Dict, ttl: int = 86400):
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None


# ---------------------------------------------------------------------------
# Key Generation
# ---------------------------------------------------------------------------

def generate_idempotency_key(
    user_id: str,
    filename: str,
    file_size: int,
    content_hash: str,
) -> str:
    """Generate deterministic idempotency key.

    Format: {user_id}:{filename}:{file_size}:{content_hash[:8]}
    """
    return f"{user_id}:{filename}:{file_size}:{content_hash[:8]}"


def compute_content_hash(content: bytes) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# FastAPI Dependency
# ---------------------------------------------------------------------------

from fastapi import Request


async def check_idempotency(request: Request) -> Optional[str]:
    """FastAPI dependency — return Idempotency-Key header if present."""
    return request.headers.get("idempotency-key")
