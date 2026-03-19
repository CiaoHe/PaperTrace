#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/Users/kakusou/micromamba/envs/agent311/bin/python}"
PYTHONPATH_VALUE="apps/api/src:apps/worker/src:packages/analysis-core/src"

PYTHONPATH="$PYTHONPATH_VALUE" "$PYTHON_BIN" infra/scripts/dump_openapi.py
pnpm exec openapi-typescript .cache/openapi.json -o packages/contracts/src/openapi.ts
