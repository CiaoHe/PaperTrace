#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/Users/kakusou/micromamba/envs/agent311/bin/python}"
PYTHONPATH_VALUE="apps/api/src:apps/worker/src:packages/analysis-core/src"
API_URL="${API_URL:-http://127.0.0.1:8100}"
WEB_URL="${WEB_URL:-http://127.0.0.1:3100}"
API_PORT="${API_PORT:-8100}"
WEB_PORT="${WEB_PORT:-3100}"

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then kill "$API_PID" >/dev/null 2>&1 || true; fi
  if [[ -n "${WEB_PID:-}" ]]; then kill "$WEB_PID" >/dev/null 2>&1 || true; fi
}

trap cleanup EXIT

mkdir -p .local
cp -n .env.example .env >/dev/null 2>&1 || true

PYTHONPATH="$PYTHONPATH_VALUE" CELERY_TASK_ALWAYS_EAGER=true DATABASE_URL="sqlite+pysqlite:///$PWD/.local/e2e.db" \
  "$PYTHON_BIN" -m uvicorn papertrace_api.main:app --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

NEXT_PUBLIC_API_BASE_URL="$API_URL" pnpm --filter @papertrace/web exec next dev --hostname 127.0.0.1 --port "$WEB_PORT" &
WEB_PID=$!

pnpm exec wait-on "http-get://127.0.0.1:${API_PORT}/api/v1/health" "http-get://127.0.0.1:${WEB_PORT}"
PLAYWRIGHT_BASE_URL="$WEB_URL" pnpm --filter @papertrace/web e2e
