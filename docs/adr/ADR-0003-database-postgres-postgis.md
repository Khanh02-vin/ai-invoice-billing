# ADR-0003: Database — PostgreSQL + PostGIS (prod) / SQLite (dev/test)

- **Status**: Accepted
- **Date**: 2026-11-07

## Decision

- Production: **PostgreSQL 15+ with PostGIS 3.4+** (spatial queries, H3 integration).
- Dev/Test: **SQLite** (giữ tốc độ iteration, CI không cần Postgres).
- Abstraction: SQLModel/SQLAlchemy; `DATABASE_URL` env chọn engine.
- Migrations: Alembic (đã config trong pyproject.toml) cho cả hai.

## Rationale

- PostGIS cần cho `ST_DWithin`, `ST_Distance`, spatial indexing.
- SQLite đủ cho dev/test và CI; không cần Postgres container local.
- SQLModel đã là abstraction → dual-mode khả thi.

## Consequences

- Cần dual-path migration (SQLite ALTER idempotent + Alembic cho Postgres).
- Một số SQL raw (như `list_shared_places`) cần port sang PostGIS.
- Test environment dùng SQLite; integration tests Postgres chạy trong CI Docker.

## Alternatives

- SQLite everywhere: rejected (no spatial functions).
- Postgres everywhere: rejected (dev friction).
