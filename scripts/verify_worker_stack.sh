#!/usr/bin/env bash
# Verify the single-backend + Redis + Celery worker topology.
set -euo pipefail

if [[ ! -f .env ]]; then
  printf '%s\n' 'Missing .env. Copy .env.example to .env and set a production JWT_SECRET.' >&2
  exit 2
fi

if [[ -z "${JWT_SECRET:-}" ]]; then
  export JWT_SECRET="$(grep -E '^JWT_SECRET=' .env | cut -d= -f2- || true)"
fi
if [[ -z "${JWT_SECRET}" ]]; then
  printf '%s\n' 'JWT_SECRET is required.' >&2
  exit 2
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ai-invoice-billing}"
docker compose config --quiet

docker compose ps --status running redis backend worker

docker compose exec -T redis redis-cli ping | grep -qx PONG

docker compose exec -T worker celery -A src.workers.celery_app inspect ping

printf '%s\n' 'Worker stack is healthy: Redis reachable, backend singleton declared, worker responding.'
