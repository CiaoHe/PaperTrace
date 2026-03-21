# PaperTrace

PaperTrace is a local-first paper-to-code lineage workbench for Python ML repositories.
It takes an arXiv link, PDF URL, or uploaded PDF, resolves the implementation repository, traces the likely upstream base repository, computes semantic diffs, and maps code changes back to paper claims.

## What It Does

- Resolve the paper's current implementation repository from paper text, project sites, and LLM-assisted extraction
- Trace the most likely upstream/base repository behind the implementation
- Build semantic diff clusters instead of raw file churn
- Map diff clusters to structured paper contributions
- Provide a web workbench and evidence review workspace for inspection
- Cache analyses by paper source and support forced re-analysis

## Stack

- Backend: Python 3.11, FastAPI, Celery, SQLAlchemy 2.0, Pydantic v2
- Worker queue: Redis
- Persistence: PostgreSQL
- Frontend: Next.js 14, TypeScript, Biome
- Testing: pytest, Playwright
- Runtime assumption: local macOS / Apple Silicon / no GPU

## Repository Layout

```text
.
├─ apps/
│  ├─ api/
│  ├─ web/
│  └─ worker/
├─ packages/
│  ├─ analysis-core/
│  └─ contracts/
├─ fixtures/
├─ infra/
├─ Makefile
└─ docker-compose.yml
```

## Local Requirements

- macOS
- Docker
- `pnpm`
- Python env at `/Users/kakusou/micromamba/envs/agent311`

## Install

```bash
make bootstrap
```

This will:

- validate the local Python runtime
- install Python and Node dependencies
- copy `.env.example` to `.env` if needed
- start PostgreSQL and Redis via Docker Compose

## Run

```bash
make dev
```

Default local endpoints:

- Web: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000`

If port `3000` is occupied, override it:

```bash
WEB_PORT=3100 make dev
```

## Quality Gates

```bash
make lint
make test
make e2e
```

## Current MVP Flow

1. Submit an arXiv URL, PDF URL, or PDF file
2. Parse paper content and extract contribution hypotheses
3. Resolve the implementation repository
4. Trace the most plausible upstream/base repository
5. Compute semantic diff clusters
6. Map code changes to paper contributions
7. Review results in the shell and evidence workspace

## Notes

- Local development is CPU-only
- LLM usage is remote-API-first; tests do not require live external services
- Analysis is currently optimized for Python repositories
- Re-running the same paper reuses cached analysis unless `force_reanalysis=true`

## Commands

```bash
make bootstrap
make dev
make lint
make test
make e2e
make down
make contracts
```
