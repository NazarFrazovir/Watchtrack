#!/bin/sh
set -e

echo "[entrypoint] Applying database migrations..."
alembic upgrade head

if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  echo "[entrypoint] Seeding demo data (skipped automatically if catalog is not empty)..."
  python -m scripts.seed
fi

echo "[entrypoint] Starting API with ${WEB_CONCURRENCY:-2} worker(s)..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}" --proxy-headers
