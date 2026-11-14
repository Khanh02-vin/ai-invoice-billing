# Dockerfile backend Quanh Ta (Rust/Axum) — binary tĩnh thay cho image Python.
#
# Build-time: sqlx macro kiểm tra SQL lúc compile cần một DB có schema — tạo
# fixtures/schema.db từ fixtures/schema.sql (DDL nguyên văn từ sqlite_master,
# giữ đồng bộ với TABLE_DDL/INDEX_DDL trong src/db.rs).

FROM rust:1-slim AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY . .

# .env local có DATABASE_URL tuyệt đối của máy dev — bỏ đi, dùng fixture
RUN rm -f .env \
 && sqlite3 fixtures/schema.db < fixtures/schema.sql \
 && DATABASE_URL=sqlite:///build/fixtures/schema.db cargo build --release

# --- Runtime ---
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home appuser \
 && mkdir -p /app/data \
 && chown -R appuser:appuser /app

COPY --from=build /build/target/release/quanh-ta-backend /usr/local/bin/quanh-ta-backend

USER appuser
WORKDIR /app
EXPOSE 8000

ENV APP_ENV=production \
    PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["quanh-ta-backend"]
