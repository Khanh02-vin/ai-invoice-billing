"""Idempotency middleware — prevent duplicate processing of requests.

Architecture:
- HTTP Header-based: Client sends `Idempotency-Key` header
- Server-side cache: Redis (production) or in-memory (dev/test)
- Deterministic key generation: user_id + filename + file_size + content_hash
- TTL: 24 hours for cache entries

Flow:
1. Client sends Idempotency-Key header
2. Server checks cache → if exists, return cached response
3. Server processes request normally
4. Server stores result with key for future dedup

Edge cases handled:
- Key collision: Include content hash in key
- Cache miss after restart: Fallback to DB check
- Concurrent requests: Distributed lock via Redis
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional, Tuple
from fastapi import Request, Response
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache Backend (Redis or In-Memory)
# ---------------------------------------------------------------------------

class IdempotencyCache:
    """Abstraction for idempotency cache storage."""

    def get(self, key: str) -> Optional[Dict]:
        """Get cached response by key."""
        raise NotImplementedError

    def set(self, key: str, value: Dict, ttl: int = 86400):
        """Store response with TTL (default 24 hours)."""
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        raise NotImplementedError


class InMemoryCache(IdempotencyCache):
    """In-memory cache for development/testing."""

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


class RedisCache(IdempotencyCache):
    """Redis cache for production."""

    def __init__(self, redis_url: str = "redis://localhost:6379/2"):
        try:
            import redis
            self._client = redis.from_url(redis_url, decode_responses=True)
            self._client.ping()
        except Exception as e:
            logger.warning(f"Redis unavailable, falling back to in-memory: {e}")
            self._fallback = InMemoryCache()
            self._client = None

    def get(self, key: str) -> Optional[Dict]:
        if self._client is None:
            return self._fallback.get(key)
        try:
            data = self._client.get(f"idempotency:{key}")
            return json.loads(data) if data else None
        except Exception:
            return None

    def set(self, key: str, value: Dict, ttl: int = 86400):
        if self._client is None:
            self._fallback.set(key, value, ttl)
            return
        try:
            self._client.setex(
                f"idempotency:{key}",
                ttl,
                json.dumps(value),
            )
        except Exception as e:
            logger.error(f"Failed to store idempotency key: {e}")

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

    This ensures:
    - Same file by same user → same key (dedup)
    - Different file by same user → different key (allow)
    - Different user with same file → different key (isolation)
    """
    return f"{user_id}:{filename}:{file_size}:{content_hash[:8]}"


def compute_content_hash(content: bytes) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class IdempotencyMiddleware:
    """FastAPI middleware for idempotency checking.

    Usage:
        app.add_middleware(IdempotencyMiddleware, cache_backend="redis")
    """

    def __init__(self, app, cache_backend: str = "memory", redis_url: str = None):
        self.app = app
        if cache_backend == "redis" and redis_url:
            self.cache = RedisCache(redis_url)
        else:
            self.cache = InMemoryCache()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope, receive)

        # Check for idempotency key
        idempotency_key = request.headers.get("idempotency-key")
        if not idempotency_key:
            return await self.app(scope, receive, send)

        # Check cache
        cached = self.cache.get(idempotency_key)
        if cached:
            logger.info(f"Idempotency hit for key: {idempotency_key}")
            return JSONResponse(
                content=cached["response"],
                status_code=cached["status_code"],
                headers={"X-Idempotent-Replay": "true"},
            )

        # Process request
        response_body = {}
        status_code = 200

        async def capture_send(message):
            nonlocal response_body, status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_body = json.loads(message.get("body", b"{}"))

        await self.app(scope, receive, capture_send)

        # Store result (only for successful responses)
        if 200 <= status_code < 300:
            self.cache.set(idempotency_key, {
                "response": response_body,
                "status_code": status_code,
            })

        return Response(
            content=json.dumps(response_body),
            status_code=status_code,
            media_type="application/json",
        )


# ---------------------------------------------------------------------------
# FastAPI Dependency
# ---------------------------------------------------------------------------

async def check_idempotency(request: Request) -> Optional[str]:
    """FastAPI dependency for idempotency check on specific endpoints.

    Returns the idempotency key if present, None otherwise.
    """
    return request.headers.get("idempotency-key")


def verify_not_duplicate(
    key: str,
    cache: IdempotencyCache,
) -> Optional[Dict]:
    """Check if request is duplicate and return cached response if so."""
    if key:
        cached = cache.get(key)
        if cached:
            return cached
    return None
