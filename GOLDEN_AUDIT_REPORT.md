# 🏆 AUDIT GOLDEN — Báo Cáo Kiến Trúc Vàng

- **Ngày audit:** 2026-09-12
- **Phiên bản hệ thống:** v3.0.0
- **Phạm vi:** Toàn bộ source code, tests, Docker/Compose, Kubernetes, CI/CD
- **Tiêu chuẩn:** Bộ 10 Tiêu Chuẩn Chuẩn Vàng

---

## 📊 BẢNG CHẤM ĐIỂM HIỆN TRẠNG

| # | Tiêu chuẩn | Điểm | Nhận xét ngắn |
|---|-----------|------|---------------|
| 1 | **Khả năng chịu tải & Bền bỉ** | 4/10 | Xử lý đồng bộ trong request path, không có queue/worker, ThreadPoolExecutor cục bộ |
| 2 | **Phân định quyền sở hữu rõ ràng** | 6/10 | Có separation API/routers/store, nhưng business logic lẫn lộn trong app.py |
| 3 | **Cấu trúc phẳng, trực quan** | 7/10 | Cấu trúc thư mục tốt (3 tầng), nhưng một số module quá lớn (app.py ~400 dòng) |
| 4 | **Thân thiện với AI Debugger** | 5/10 | Có request_id, nhưng thiếu snapshot state, không có replay mechanism |
| 5 | **Hiệu năng cao & Đa ngôn ngữ** | 4/10 | Python thuần, không có I/O vs Compute separation, chưa sẵn sàng plug module Rust/C++ |
| 6 | **Mở rộng linh hoạt** | 7/10 | Có Adapter Pattern cho billing/LLM, nhưng OCR chưa abstract hoàn toàn |
| 7 | **Bảo mật & Cách ly dữ liệu** | 6/10 | Multi-tenant với user_id, PII redaction có, nhưng chưa có data retention auto-delete |
| 8 | **Quản trị chi phí & Quan sát** | 6/10 | Có Prometheus metrics, rate limiting, nhưng chưa có token/GPU cost tracking |
| 9 | **Kiểm thử chất lượng & Chống suy thoái AI** | 5/10 | Có benchmark scripts, nhưng chưa có automated CI pipeline, thiếu WER/F1/Hallucination metrics |
| 10 | **Tương thích ngược & Nâng cấp an toàn** | 4/10 | Schema versioning đơn giản (IF NOT EXISTS), không có Alembic, zero-downtime migration chưa có |

**ĐIỂM TRUNG BÌNH: 5.4/10**

---

## 🔴 PHÂN TÍCH LỖ HỔNG NGHIÊM TRỌNG

### P0 — CÓ THỂ LÀM GÃY HỆ THỐNG

#### 1. SQLite + Local Filesystem trong Production Kubernetes
```
Rủi ro: Dữ liệu mất khi pod thay thế (Recreate strategy)
Vị trí: k8s/deployment.yaml, src/store/db.py
```
- **Hậu quả:** Mất toàn bộ dữ liệu hóa đơn khi pod crash/restart
- **Nguyên nhân:** Không có PersistentVolumeClaim, không có backup strategy tự động
- **Độ ưu tiên:** 🔴 P0 — CẦN SỬA NGAY

#### 2. Không có Queue/Worker — Request Path Overload
```python
# src/extract/batch_extractor.py
def extract_batch(files, repo, user_id, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Chạy ngay trong request, không có async queue
```
- **Hậu quả:** Request timeout khi upload batch lớn, chiếm hết worker uvicorn
- **Nguyên nhân:** OCR/LLM/PDF chạy đồng bộ trong request path
- **Độ ưu tiên:** 🔴 P0 — CẦN SỬA NGAY

#### 3. Non-Idempotent Upload — Duplicate Processing
```python
# src/app.py:208-253
@app.post("/upload", response_model=Invoice)
async def upload_invoice(file: UploadFile, user: User):
    # Không có idempotency key check
    # Upload lại = xử lý lại + tốn token LLM
```
- **Hậu quả:** User upload lại = chi phí LLM nhân đôi, dữ liệu duplicate
- **Độ ưu tiên:** 🔴 P0 — CẦN SỬA NGAY

### P1 — RỦI RO CHI PHÍ PHÌNH TO

#### 4. LLM Fallback Không Có Cost Cap
```python
# src/llm/base.py
class OpenAIProvider(LLMProvider):
    def complete(self, system, user):
        # Không có:
        # - Token limit check
        # - Cost tracking
        # - Rate limit per user
        # - Budget quota
```
- **Hậu quả:** Runaway LLM costs khi regex fail nhiều
- **Độ ưu tiên:** 🟠 P1 — CHI PHÍ AN TOÀN

#### 5. Embedding Vector Store Brute-Force
```python
# src/search/vector_store.py:95-105
def search(self, query_vec, user_id, org_id, top_k=10):
    rows = conn.execute("SELECT * FROM invoice_embeddings WHERE ...").fetchall()
    scored = [(row, cosine_similarity(query_vec, vec)) for row, vec in ...]
    scored.sort(key=lambda x: x[1], reverse=True)
    # O(N) scan, không có ANN index (HNSW/IVF)
```
- **Hậu quả:** Performance down khi có >10K invoices
- **Độ ưu tiên:** 🟠 P1 — SCALE LIMIT

#### 6. Multi-Tenant Data Leak Risk
```python
# src/store/repository.py
def list_all(self, user_id: str = ""):
    if user_id:
        return [i for i in self._invoices.values() if i.user_id == user_id]
    return list(self._invoices.values())  # ← Trả TẤT CẢ nếu không filter
```
- **Hậu quả:** Potential data leak giữa tenants
- **Độ ưu tiên:** 🟠 P1 — SECURITY

---

## 🏗 BẢN THIẾT KẾ HÀNH ĐỘNG

### Cây Thư Mục Mục Tiêu

```
ai-invoice-billing/
├── src/
│   ├── api/                          # API Layer (Thin Controllers)
│   │   ├── __init__.py
│   │   ├── app.py                    # FastAPI app setup, middleware
│   │   ├── deps.py                   # Dependencies injection
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── invoices.py
│   │       ├── billing.py
│   │       └── reports.py
│   │
│   ├── domain/                       # Business Logic (Pure Functions)
│   │   ├── __init__.py
│   │   ├── models.py                 # Pydantic models
│   │   ├── invoice_service.py        # Invoice business logic
│   │   ├── billing_service.py        # Billing business logic
│   │   └── extraction_service.py     # Extraction orchestration
│   │
│   ├── workers/                      # Background Workers (NEW)
│   │   ├── __init__.py
│   │   ├── celery_app.py             # Celery configuration
│   │   ├── extraction_worker.py      # OCR/LLM async processing
│   │   └── billing_worker.py         # Webhook processing
│   │
│   ├── adapters/                     # External Adapters (Adapter Pattern)
│   │   ├── __init__.py
│   │   ├── llm/
│   │   │   ├── base.py               # Abstract LLM provider
│   │   │   ├── openai.py
│   │   │   └── ollama.py
│   │   ├── ocr/
│   │   │   ├── base.py               # Abstract OCR provider
│   │   │   ├── paddle.py
│   │   │   └── tesseract.py
│   │   ├── storage/
│   │   │   ├── base.py               # Abstract object storage
│   │   │   ├── local.py
│   │   │   └── s3.py
│   │   └── billing/
│   │       ├── base.py
│   │       ├── stripe.py
│   │       └── mock.py
│   │
│   ├── store/                        # Data Access Layer
│   │   ├── __init__.py
│   │   ├── db.py                     # Database abstraction
│   │   ├── repositories/
│   │   │   ├── invoice_repo.py
│   │   │   ├── user_repo.py
│   │   │   └── billing_repo.py
│   │   └── migrations/               # Alembic migrations (NEW)
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── security/                     # Security & Rate Limiting
│   │   ├── rate_limit.py
│   │   ├── idempotency.py            # NEW: Idempotency key
│   │   └── malware.py
│   │
│   ├── observability/                # Monitoring & Tracing
│   │   ├── metrics.py
│   │   ├── tracing.py                # NEW: OpenTelemetry
│   │   └── cost_tracker.py           # NEW: Token/GPU cost
│   │
│   └── config.py                     # Configuration
│
├── workers/                          # Standalone Worker Process (NEW)
│   ├── Dockerfile
│   └── celery_worker.py
│
├── alembic/                          # Database Migrations (NEW)
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── benchmark/                    # AI Quality Metrics
│       ├── wer_metrics.py
│       ├── f1_score.py
│       └── hallucination_check.py
│
└── docker-compose.yml                # Updated with worker service
```

### Mã Nguồn Adapter Mẫu

#### 1. Abstract LLM Provider (Adapter Pattern)
```python
# src/adapters/llm/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResponse:
    content: str
    tokens_used: int
    cost_usd: float
    latency_ms: float
    model: str


class LLMProvider(ABC):
    """Abstract LLM provider — all implementations must conform."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Send prompt and return structured response."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if provider is available."""
        ...

    @abstractmethod
    def estimate_cost(self, tokens: int) -> float:
        """Estimate cost in USD for given token count."""
        ...
```

#### 2. OpenAI Implementation
```python
# src/adapters/llm/openai.py
import os
from openai import AsyncOpenAI
from .base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider (works with Qwen, Ollama, etc.)."""

    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            api_key=os.getenv("LLM_API_KEY", ""),
        )
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.cost_per_1k = float(os.getenv("LLM_COST_PER_1K_TOKENS", "0.00015"))

    async def complete(self, system: str, user: str, max_tokens: int = 4096, temperature: float = 0.0) -> LLMResponse:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        tokens = response.usage.total_tokens
        return LLMResponse(
            content=response.choices[0].message.content,
            tokens_used=tokens,
            cost_usd=self.estimate_cost(tokens),
            latency_ms=0,  # TODO: track
            model=self.model,
        )

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False

    def estimate_cost(self, tokens: int) -> float:
        return (tokens / 1000) * self.cost_per_1k
```

#### 3. Idempotency Middleware
```python
# src/security/idempotency.py
import hashlib
import time
from typing import Optional
from fastapi import Request, HTTPException


class IdempotencyStore:
    """In-memory idempotency store (replace with Redis in production)."""

    def __init__(self, ttl_seconds: int = 86400):
        self._store: dict[str, tuple[float, dict]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[dict]:
        if key in self._store:
            timestamp, data = self._store[key]
            if time.time() - timestamp < self._ttl:
                return data
            del self._store[key]
        return None

    def set(self, key: str, data: dict):
        self._store[key] = (time.time(), data)


idempotency_store = IdempotencyStore()


def generate_idempotency_key(user_id: str, file_content: bytes) -> str:
    """Generate deterministic idempotency key from user + file hash."""
    content_hash = hashlib.sha256(file_content).hexdigest()[:16]
    return f"{user_id}:{content_hash}"


async def check_idempotency(request: Request) -> Optional[dict]:
    """FastAPI dependency for idempotency check."""
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return None  # No key = process normally (optional enforcement)

    cached = idempotency_store.get(idempotency_key)
    if cached:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "DUPLICATE_REQUEST",
                "message": "Request already processed",
                "cached_result": cached,
            },
        )
    return None
```

#### 4. Cost Tracker
```python
# src/observability/cost_tracker.py
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


@dataclass
class CostRecord:
    user_id: str
    operation: str  # "llm_extraction", "ocr", "embedding"
    tokens: int = 0
    cost_usd: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CostTracker:
    """Track AI/LLM costs per user for budget enforcement."""

    def __init__(self):
        self._records: List[CostRecord] = []
        self._lock = threading.Lock()
        self._budgets: Dict[str, float] = {}  # user_id -> max_usd

    def record(self, user_id: str, operation: str, tokens: int, cost_usd: float):
        with self._lock:
            self._records.append(CostRecord(user_id, operation, tokens, cost_usd))

    def get_user_cost(self, user_id: str, period_hours: int = 24) -> float:
        cutoff = datetime.now(timezone.utc).timestamp() - (period_hours * 3600)
        with self._lock:
            return sum(
                r.cost_usd for r in self._records
                if r.user_id == user_id and r.timestamp.timestamp() > cutoff
            )

    def check_budget(self, user_id: str, estimated_cost: float) -> bool:
        if user_id not in self._budgets:
            return True  # No budget set = unlimited
        current = self.get_user_cost(user_id)
        return (current + estimated_cost) <= self._budgets[user_id]


cost_tracker = CostTracker()
```

### File Cấu Hình Tối Thiểu

#### docker-compose.yml (Worker Service)
```yaml
services:
  worker:
    build:
      context: .
      dockerfile: workers/Dockerfile
    command: celery -A workers.celery_app worker -l info -c 4
    volumes:
      - ./data:/app/data
    environment:
      - DATABASE_URL=sqlite:///data/invoices.db
      - REDIS_URL=redis://redis:6379/0
      - LLM_API_KEY=${LLM_API_KEY}
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

#### alembic.ini
```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite:///invoices.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

---

## 🗓 LỘ TRÌNH TRIỂN KHAI

### 🔥 NGAY TRONG 24-48H (Quick Wins)

| # | Việc làm | Effort | Impact |
|---|---------|--------|--------|
| 1 | **Thêm Idempotency-Key header cho /upload** | 2h | Tránh duplicate processing |
| 2 | **Tách business logic ra domain/** | 4h | Clean separation, dễ test |
| 3 | **Thêm LLM cost cap per user** | 2h | Ngăn runaway costs |
| 4 | **Fix multi-tenant data leak** (luôn filter user_id) | 1h | Security hardening |
| 5 | **Thêm OpenTelemetry tracing** (basic span) | 3h | Observability |

### 📅 TUẦN 1-2 (Foundation)

| # | Việc làm | Effort | Impact |
|---|---------|--------|--------|
| 6 | **Setup Celery + Redis** cho background workers | 8h | Async processing |
| 7 | **Tách extraction worker** ra独立 process | 6h | Request path lightweight |
| 8 | **Setup Alembic** cho DB migrations | 4h | Safe schema evolution |
| 9 | **Implement S3 adapter** cho file storage | 6h | Production-ready storage |
| 10 | **Thêm automated benchmark CI** (WER/F1) | 8h | AI quality gate |

### 📅 THÁNG 1 (Production Readiness)

| # | Việc làm | Effort | Impact |
|---|---------|--------|--------|
| 11 | **PostgreSQL migration** (từ SQLite) | 16h | Concurrent writes, ACID |
| 12 | **HNSW index cho vector search** | 8h | Sub-linear search |
| 13 | **OpenTelemetry full tracing** | 8h | End-to-end visibility |
| 14 | **Cost dashboard + alerts** | 8h | Financial control |
| 15 | **Load testing + capacity planning** | 8h | Sizing validation |

### 📅 THÁNG 2-3 (Scale & Harden)

| # | Việc làm | Effort | Impact |
|---|---------|--------|--------|
| 16 | **Multi-region deployment** | 24h | High availability |
| 17 | **Advanced security** (WAF, DDoS protection) | 16h | Threat mitigation |
| 18 | **ML pipeline** (model versioning, A/B testing) | 24h | AI innovation |
| 19 | **Compliance** (SOC2, GDPR audit) | 40h | Enterprise readiness |

---

## 📈 KẾT LUẬN

### Điểm mạnh hiện tại:
✅ Cấu trúc thư mục rõ ràng (3 tầng)
✅ Adapter Pattern cho billing/LLM
✅ Request ID middleware cho tracing
✅ Rate limiting có sẵn
✅ PII redaction functions

### Cần cải thiện gấp:
❌ Queue/worker cho async processing
❌ Idempotency cho upload
❌ Cost tracking cho LLM
❌ Production-ready storage (S3)
❌ Database migrations (Alembic)

### Khuyến nghị:
1. **Tuần này:** Fix P0 issues (idempotency, multi-tenant leak)
2. **Tuần sau:** Setup Celery workers
3. **Tháng này:** Migration to PostgreSQL + S3
4. **Quý này:** Full observability + cost dashboard

---

*Báo cáo được tạo bởi Audit Golden System*
*Phiên bản: 1.0.0 | Ngày: 2026-09-12*
