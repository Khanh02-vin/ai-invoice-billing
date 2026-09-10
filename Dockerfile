# Dockerfile for ai-invoice-billing backend (Phase 3-4 / v3.0.0).
# Multi-stage build: compile deps in builder, run slim runtime.
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Install build-time system deps (for reportlab / pdfplumber wheels if needed)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --prefix=/install -r requirements.txt

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app

# Runtime system deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Application code
COPY src ./src
COPY main.py ./
COPY requirements.txt ./

# Non-root user
RUN useradd --create-home appuser && mkdir -p /app/data/storage /app/data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Fail-fast: require JWT_SECRET in production. docker-compose sets it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
