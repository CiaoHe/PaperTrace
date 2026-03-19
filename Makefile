PYTHON := /Users/kakusou/micromamba/envs/agent311/bin/python
PYTHONPATHS := apps/api/src:apps/worker/src:packages/analysis-core/src
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
WEB_PORT ?= 3000

.PHONY: bootstrap dev lint test smoke e2e down contracts playwright-install

bootstrap:
	@test -x "$(PYTHON)" || (echo "Missing Python interpreter: $(PYTHON)" && exit 1)
	@cp -n .env.example .env >/dev/null 2>&1 || true
	@$(PYTHON) infra/scripts/check_env.py
	@$(PYTHON) -m uv pip install --python "$(PYTHON)" -r pyproject.toml --group dev
	@pnpm install
	@docker compose up -d postgres redis

dev:
	@docker compose up -d postgres redis
	@PYTHONPATH="$(PYTHONPATHS)" pnpm exec concurrently -k -n api,worker,web -c blue,magenta,green \
		"$(PYTHON) -m uvicorn papertrace_api.main:app --reload --host $(API_HOST) --port $(API_PORT)" \
		"$(PYTHON) -m celery -A papertrace_worker.celery_app.celery_app worker --loglevel=info" \
		"pnpm --filter @papertrace/web exec next dev --hostname $(API_HOST) --port $(WEB_PORT)"

lint:
	@PYTHONPATH="$(PYTHONPATHS)" $(PYTHON) -m ruff check .
	@PYTHONPATH="$(PYTHONPATHS)" $(PYTHON) -m ruff format --check .
	@PYTHONPATH="$(PYTHONPATHS)" $(PYTHON) -m mypy apps packages infra
	@pnpm exec biome check .
	@pnpm --filter @papertrace/contracts lint
	@pnpm --filter @papertrace/web typecheck

test:
	@PYTHONPATH="$(PYTHONPATHS)" CELERY_TASK_ALWAYS_EAGER=true DATABASE_URL="sqlite+pysqlite:///$(PWD)/.local/test.db" $(PYTHON) -m pytest -m "not smoke"

smoke:
	@PYTHONPATH="$(PYTHONPATHS)" $(PYTHON) -m pytest -m smoke

e2e:
	@cp -n .env.example .env >/dev/null 2>&1 || true
	@mkdir -p .local
	@docker compose up -d postgres redis
	@bash infra/scripts/run_e2e.sh

playwright-install:
	@pnpm --filter @papertrace/web exec playwright install

down:
	@docker compose down

contracts:
	@bash infra/scripts/generate_contracts.sh
