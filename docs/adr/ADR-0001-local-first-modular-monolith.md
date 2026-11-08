# ADR-0001: Local-First + Event-Driven Architecture (Modular Monolith MVP)

- **Status**: Accepted
- **Date**: 2026-11-07
- **Context**: Dự án đang là web app Invoice & Billing (FastAPI + React). Thiết kế mới đề xuất Local-First + Event-Driven platform cho expense intelligence (mobile capture, GPS/POI, community). Cần chốt kiến trúc MVP khả thi.

## Decision

Chọn **Local-First Modular Monolith** cho MVP, không triển khai microservices đầy đủ:

- **Mobile client**: React Native (Expo cho MVP) + local SQLite (expo-sqlite) + offline outbox.
- **Backend**: FastAPI modular monolith (giữ codebase hiện tại, refactor boundaries).
- **DB**: PostgreSQL + PostGIS cho production; SQLite cho dev/test (dual-mode qua `DATABASE_URL`).
- **Queue**: giữ arq + Redis cho MVP; RabbitMQ chỉ khi tải tăng.
- **Storage**: S3-compatible với fallback local.
- **Spatial**: PostGIS + H3 (phase 3).
- **OCR/Vision**: server-side (giữ pipeline hiện tại), tách worker rõ ràng.

## Rationale

- Team nhỏ, MVP cần ship nhanh; microservices premature.
- PostgreSQL+PostGIS đủ cho spatial needs ban đầu; Elasticsearch/TimescaleDB chưa cần.
- arq/Redis đã có, tránh migration queue không cần thiết.
- SQLite dev/test giữ tốc độ iteration; Postgres prod cho spatial features.

## Consequences

- Mobile client phải implement sync protocol (outbox + cursor).
- Location model phải refactor để tách payment vs merchant.
- DB migration từ SQLite → Postgres cần abstraction (SQLModel/SQLAlchemy đã hỗ trợ).
- iOS không có auto notification capture → UX manual-first trên iOS.

## Alternatives considered

- Microservices ngay: rejected (premature complexity).
- SQLite everywhere: rejected (không có PostGIS/H3).
- RabbitMQ ngay: rejected (arq đủ cho MVP).
