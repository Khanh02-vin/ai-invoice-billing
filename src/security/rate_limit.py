"""Rate limiting — sliding window, thread-safe, in-memory hoặc SQLite.

Nguyên tắc (PLAN mục 3 — bảo mật):
- Giới hạn số request theo client (IP + path) hoặc user_id nếu đã auth.
- Sliding window: chính xác hơn fixed window, không bị burst ở ranh giới.
- Thread-safe bằng threading.Lock.
- SQLite backend tùy chọn cho persistence giữa các worker (dùng khi RATE_LIMIT_STORAGE=sqlite).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple


# ---------- In-memory sliding window ----------

class _MemoryBackend:
    """In-memory backend cho sliding window — nhanh, không persistence."""

    def __init__(self) -> None:
        self._hits: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def prune(self, key: str, window_seconds: float, now: float) -> None:
        cutoff = now - window_seconds
        hits = self._hits[key]
        # Xóa các hit cũ hơn window
        self._hits[key] = [t for t in hits if t > cutoff]

    def add(self, key: str, now: float) -> None:
        self._hits[key].append(now)

    def count(self, key: str) -> int:
        return len(self._hits.get(key, []))

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)

    def clear(self) -> None:
        self._hits.clear()

    def keys(self) -> List[str]:
        return list(self._hits.keys())


# ---------- SQLite backend (optional persistence) ----------

class _SqliteBackend:
    """SQLite backend cho rate limit — dùng chung giữa worker/process."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self._db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._ensure_schema(self._mem_conn)
            return self._mem_conn
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            if self._db_path != ":memory:":
                conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_hits (
                key TEXT NOT NULL,
                ts REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rate_limit_key_ts
            ON rate_limit_hits(key, ts)
        """)

    def prune(self, key: str, window_seconds: float, now: float) -> None:
        cutoff = now - window_seconds
        with self._lock, self._cursor() as cur:
            cur.execute("DELETE FROM rate_limit_hits WHERE key = ? AND ts <= ?", (key, cutoff))

    def add(self, key: str, now: float) -> None:
        with self._lock, self._cursor() as cur:
            cur.execute("INSERT INTO rate_limit_hits(key, ts) VALUES (?, ?)", (key, now))

    def count(self, key: str) -> int:
        with self._lock, self._cursor() as cur:
            row = cur.execute("SELECT COUNT(*) FROM rate_limit_hits WHERE key = ?", (key,)).fetchone()
        return int(row[0]) if row else 0

    def reset(self, key: str) -> None:
        with self._lock, self._cursor() as cur:
            cur.execute("DELETE FROM rate_limit_hits WHERE key = ?", (key,))

    def clear(self) -> None:
        with self._lock, self._cursor() as cur:
            cur.execute("DELETE FROM rate_limit_hits")

    def keys(self) -> List[str]:
        with self._lock, self._cursor() as cur:
            rows = cur.execute("SELECT DISTINCT key FROM rate_limit_hits").fetchall()
        return [r[0] for r in rows]


# ---------- RateLimiter ----------

class RateLimiter:
    """Sliding-window rate limiter, thread-safe.

    Dùng:
        limiter = RateLimiter()
        if limiter.is_allowed("client:127.0.0.1:/auth/login", max_requests=5, window_seconds=60):
            ... cho phép ...
    """

    def __init__(self, backend: Optional[str] = None, db_path: Optional[str] = None) -> None:
        """
        Args:
            backend: 'memory' (default) hoặc 'sqlite'.
            db_path: chỉ dùng khi backend='sqlite'. Mặc định ':memory:'.
        """
        if backend is None:
            backend = os.getenv("RATE_LIMIT_BACKEND", "memory")
        if backend == "sqlite":
            self._backend = _SqliteBackend(db_path or os.getenv("RATE_LIMIT_DB", ":memory:"))
        else:
            self._backend = _MemoryBackend()
        self._lock = threading.Lock()

    @staticmethod
    def make_key(client_ip: str, path: str, user_id: Optional[str] = None) -> str:
        """Tạo key chuẩn: ưu tiên user_id nếu có, không thì IP + path."""
        if user_id:
            return f"uid:{user_id}:{path}"
        return f"ip:{client_ip}:{path}"

    def is_allowed(self, key: str, max_requests: int, window_seconds: float) -> bool:
        """Kiểm tra request có được cho phép không.

        Returns:
            True nếu còn trong giới hạn, False nếu vượt quá.
        """
        now = time.time()
        with self._lock:
            self._backend.prune(key, window_seconds, now)
            current = self._backend.count(key)
            if current >= max_requests:
                return False
            self._backend.add(key, now)
            return True

    def get_remaining(self, key: str, max_requests: int, window_seconds: float) -> int:
        """Số request còn lại trong window hiện tại."""
        now = time.time()
        with self._lock:
            self._backend.prune(key, window_seconds, now)
            current = self._backend.count(key)
        return max(0, max_requests - current)

    def reset(self, key: str) -> None:
        """Xóa toàn bộ hit cho key."""
        with self._lock:
            self._backend.reset(key)

    def clear(self) -> None:
        """Xóa toàn bộ state — dùng cho test."""
        with self._lock:
            self._backend.clear()

    def peek(self, key: str, window_seconds: float) -> int:
        """Đếm số hit hiện tại trong window (không thêm hit mới)."""
        now = time.time()
        with self._lock:
            self._backend.prune(key, window_seconds, now)
            return self._backend.count(key)

    def status(self, window_seconds: float) -> Dict[str, int]:
        """Trả về dict {key: count} cho toàn bộ key — dùng cho admin."""
        now = time.time()
        result: Dict[str, int] = {}
        with self._lock:
            for key in self._backend.keys():
                self._backend.prune(key, window_seconds, now)
                cnt = self._backend.count(key)
                if cnt > 0:
                    result[key] = cnt
        return result


# ---------- Middleware / helper ----------

# Global singleton — main agent có thể import và wire vào middleware.
_default_limiter: Optional[RateLimiter] = None
_default_limiter_lock = threading.Lock()


def get_default_limiter() -> RateLimiter:
    """Lấy hoặc tạo global RateLimiter singleton."""
    global _default_limiter
    if _default_limiter is None:
        with _default_limiter_lock:
            if _default_limiter is None:
                _default_limiter = RateLimiter()
    return _default_limiter


def check_rate_limit(
    client_ip: str,
    path: str,
    max_requests: int,
    window_seconds: float,
    user_id: Optional[str] = None,
    limiter: Optional[RateLimiter] = None,
) -> Tuple[bool, int]:
    """Helper: kiểm tra rate limit cho một request.

    Returns:
        (is_allowed, remaining)
    """
    rl = limiter or get_default_limiter()
    key = rl.make_key(client_ip, path, user_id)
    allowed = rl.is_allowed(key, max_requests, window_seconds)
    remaining = rl.get_remaining(key, max_requests, window_seconds)
    return allowed, remaining


class RateLimitMiddleware:
    """FastAPI/Starlette middleware cho rate limiting toàn cục.

    Dùng:
        app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
    """

    def __init__(self, app, max_requests: int = 100, window_seconds: float = 60) -> None:
        # BaseHTTPMiddleware-compatible: lưu app nếu cần
        self.app = app
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.limiter = get_default_limiter()

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Lấy client_ip tỐT: dùng x-forwarded-for nếu có, không thì client host
        headers = dict(scope.get("headers") or [])
        client_host = ""
        client_port = ""
        client = scope.get("client")
        if client:
            client_host, client_port = client[0], str(client[1])
        xff = headers.get(b"x-forwarded-for")
        if xff:
            client_host = xff.decode().split(",")[0].strip()
        path = scope.get("path", "/")
        key = self.limiter.make_key(client_host, path)
        allowed = self.limiter.is_allowed(key, self.max_requests, self.window_seconds)
        remaining = self.limiter.get_remaining(key, self.max_requests, self.window_seconds)
        if not allowed:
            body = b'{"error":{"code":"RATE_LIMITED","message":"Qua nhieu request. Vui long thu lai sau."}}'
            response = JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Qua nhieu request. Vui long thu lai sau.",
                    }
                },
                headers={
                    "Retry-After": str(int(self.window_seconds)),
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": str(remaining),
                },
            )
            await response(scope, receive, send)
            return

        # Inject header cho response
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(self.max_requests).encode()))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
