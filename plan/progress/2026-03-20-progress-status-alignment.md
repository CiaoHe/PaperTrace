# 2026-03-20 Progress Status Alignment

## Purpose

- This document aligns the current implementation status against:
  - `AGENTS.md`
  - `plan/01-mvp-spec.md`
  - `plan/02-architecture.md`
  - `plan/03-engineering-standards.md`
  - `plan/04-quality-and-delivery.md`
- It also reconciles the current codebase with the workstreams defined in [2026-03-20 Next Stage Plan](/Users/kakusou/work/code/project/PaperTrace/plan/progress/2026-03-20-next-stage-plan.md).

## Verification Snapshot

- Verified locally during this review:
  - `make lint` passed
  - `make test` passed with `79 passed, 6 deselected`
  - `make e2e` passed with `3 passed`
- Previously verified in the current implementation cycle:
  - `make lint` passed
  - `make e2e` passed with `3 passed`

## Overall Progress

- Platform and delivery baseline: `mostly completed`
- MVP web submission and result rendering: `mostly completed`
- Real paper ingestion path: `completed`
- Repo tracing and live diff backbone: `mostly completed`
- Contribution mapping intelligence: `mostly completed`
- Presentation layer beyond summary cards: `mostly completed`

## Requirement Alignment

### Product Input Requirements

- Status: `completed`
- Implemented:
  - `arXiv` URL input is supported.
  - `PDF URL` input is supported.
  - `PDF upload` is supported via `multipart/form-data`.
  - GitHub repository URL validation is implemented.
  - The JSON API boundary now uses a structured `paper_input` union keyed by `source_kind` instead of the previous mixed free-form request shape.
  - Web submission now uses the structured `paper_input` contract for all non-file submissions.
  - Live arXiv fetch now attempts `e-print` LaTeX-source ingestion first and falls back to metadata-only content with explicit warnings.
  - Multipart upload now accepts a structured `paper_input` envelope alongside the binary PDF instead of relying only on loose legacy form fields.
- Evidence:
  - [main.py](/Users/kakusou/work/code/project/PaperTrace/apps/api/src/papertrace_api/main.py)
  - [schemas.py](/Users/kakusou/work/code/project/PaperTrace/apps/api/src/papertrace_api/schemas.py)
  - [uploads.py](/Users/kakusou/work/code/project/PaperTrace/apps/api/src/papertrace_api/uploads.py)
  - [paper_sources.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/paper_sources.py)
  - [test_paper_sources.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/tests/test_paper_sources.py)
  - [test_api.py](/Users/kakusou/work/code/project/PaperTrace/apps/api/tests/test_api.py)

### Job Lifecycle And Tracking

- Status: `completed`
- Implemented:
  - Public job states match the plan: `queued`, `running`, `succeeded`, `failed`.
  - Stage progression includes `paper_fetch`, `paper_parse`, `repo_fetch`, `ancestry_trace`, `diff_analyze`, `contribution_map`, and `persist_result`.
  - API exposes create, status, result, and health endpoints.
  - Job status payloads now expose `stage_progress`, `stage_detail`, and a timestamped `timeline` so the UI can observe real execution telemetry instead of only a coarse stage label.
  - Worker execution now updates job state from live pipeline callbacks rather than pre-advancing stages in a fixed sequential loop.
  - Storage initialization performs additive schema upgrades for telemetry columns, so existing local databases can pick up the new fields without a manual reset.
- Evidence:
  - [models.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/models.py)
  - [pipeline.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/pipeline.py)
  - [storage.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/storage.py)
  - [main.py](/Users/kakusou/work/code/project/PaperTrace/apps/api/src/papertrace_api/main.py)

### Paper Parsing

- Status: `mostly completed`
- Implemented:
  - Live arXiv fetch produces title, abstract, and normalized text.
  - Live arXiv fetch now prefers source-backed LaTeX extraction when `e-print` source is available.
  - Live PDF fetch and local PDF parsing produce extracted text and section-like groupings.
  - Parser produces normalized `PaperContribution` records with IDs, titles, sections, keywords, and hints.
  - Parser now works from fetched `PaperDocument` instead of only fixture paper text.
  - Parser now classifies section intent more explicitly through headings such as contribution and method sections.
  - Parser now performs section-specialized extraction across contribution, method, experiment, and appendix-like sections.
  - Parser now merges structured extraction with generic extraction, enriches contributions with problem, baseline-difference, reference, and complexity fields, and emits parser gap warnings when the document structure is weak.
  - Parser now performs a cross-section refinement pass that clusters repeated findings across abstract, method, experiment, and appendix evidence before emitting final contributions.
  - Optional LLM parsing now uses a section-aware structured extraction prompt instead of a single raw paper-text dump.
  - LLM contribution payloads are normalized into the richer `PaperContribution` schema and then merged with heuristic findings so implementation hints and reference markers are preserved.
  - LLM parsing input is now budgeted by prioritized sections, per-section truncation, and total prompt size caps to stay practical on local development setups.
  - LLM parsing now supports section-batched multi-pass extraction so longer papers can be covered across several prioritized windows instead of a single truncated request.
- Evidence:
  - [paper_sources.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/paper_sources.py)
  - [heuristics.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/heuristics.py)
  - [services.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/services.py)
  - [llm.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/llm.py)
- Remaining:
  - LLM parsing is still optional and not benchmark-tuned against a real paper evaluation set.
  - The parser now spans longer papers through section batches, but it still does not build a fuller document graph or iterative global refinement pass.

### Repo Ancestry Tracing

- Status: `mostly completed`
- Implemented:
  - GitHub fork metadata lookup exists.
  - README declaration matching exists.
  - Paper text mention extraction exists.
  - Code-reference and simple fingerprint strategies exist.
  - Framework-signature detection now exists as a distinct strategy.
  - Repository metadata URL tracing now extracts ancestry hints from `CITATION.cff`, `CITATION.bib`, `pyproject.toml`, `setup.py`, and `.git/config`.
  - Dependency archaeology now scans dependency files and submodule config for ancestry hints.
  - Fossil detection now inspects first-commit evidence.
  - GitHub code search for unique symbols is now implemented as an optional remote strategy.
  - Citation-graph search now exists as an optional remote strategy through GitHub repository search over arXiv IDs and paper-title citation signals.
  - Author-graph search now exists as an optional remote strategy through GitHub repository search over paper-author surnames plus paper-topic phrases.
  - Temporal-topic GitHub repository search now exists as an optional remote strategy that uses paper-topic phrases and paper-time priors.
  - Directory and dependency shape similarity now exists as a distinct strategy.
  - Optional LLM ancestry reasoning now acts as a late-stage cascade ring that can propose additional upstream candidates from paper context, repo metadata, and current heuristic candidates.
  - Ranked candidates with confidence and evidence are returned.
- Evidence:
  - [repo_metadata.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/repo_metadata.py)
  - [services.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/services.py)
  - [llm.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/llm.py)
- Remaining:
  - Strategy quality still depends on a small built-in known-upstream alias map.
  - Initial commit ancestry analysis is still lightweight rather than a fuller ancestry diff.
  - The current fingerprint approach is still a lightweight overlap heuristic, not the fuller fingerprint library described in `AGENTS.md`.
  - The cascade now reaches an LLM reasoning ring, but still lacks richer graph-based search rings before that final inference step.

### Diff Analysis

- Status: `mostly completed`
- Implemented:
  - Live shallow-clone analysis exists.
  - Noise filtering exists through include/exclude directories, filenames, file-size caps, and extension filters.
  - Diff clusters are labeled with the required change-type enums.
  - Cluster summaries now include bucketing rationale.
  - Diff clusters now carry semantic tags.
  - Clustering now uses graph-based connected components over semantic tags, local imports, labels, and directory affinity instead of only flat file buckets.
  - Cluster summaries now surface the semantic links that caused files to aggregate into a shared functional change set.
  - Diff clusters now include line-anchored code evidence snippets extracted from live changed hunks.
  - Code evidence anchors now carry old/new snippet payloads and old/new line ranges suitable for a diff viewer.
  - Live diff output now carries stable `patch_id` values for both clusters and code anchors so review surfaces can preserve identity beyond positional ordering.
  - Related clusters are surfaced through `related_cluster_ids`.
  - Live diff analysis now returns empty cluster sets with explicit warnings on clone failure or no-op results instead of injecting fixture diff clusters into real runs.
- Evidence:
  - [services.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/services.py)
  - [models.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/models.py)
  - [settings.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/settings.py)
- Remaining:
  - The analyzer still stops short of richer diff metadata such as inline token spans.

### Contribution Mapping

- Status: `mostly completed`
- Implemented:
  - Mappings include confidence and readable evidence.
  - Unmatched contributions and unmatched diff clusters are preserved.
  - Heuristic mapping is the primary path; optional LLM mapping is supported.
  - Completeness semantics now include implementation coverage, coverage type, missing aspects, engineering divergences, learning entry point, and reading order.
  - Mapping now performs explicit contribution-step tracing against diff-cluster summaries and semantic tags, and records manual review gaps when steps remain untraced.
  - Mapping now scores snippet-grounded fidelity from matched diff anchors rather than relying only on cluster summaries and file paths.
  - Mapping now tracks `snippet_fidelity`, `formula_fidelity`, `fidelity_notes`, and `matched_anchor_patch_ids` to ground review flows in concrete changed snippets.
  - The web review panel now surfaces these fidelity signals and prioritizes matched anchors during code inspection.
- Evidence:
  - [heuristics.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/heuristics.py)
  - [services.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/services.py)
  - [models.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/models.py)
  - [analysis-evidence-panel.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-evidence-panel.tsx)
- Remaining:
  - Formula fidelity is still heuristic and token-overlap driven rather than derived from a true symbolic or AST-level implementation check.
  - The mapper still does not verify mathematical equivalence between paper equations and code.

### Web Result Presentation

- Status: `mostly completed`
- Implemented:
  - Web UI can submit JSON and multipart PDF jobs.
  - Web UI renders base repo candidates, contributions, diff clusters, mappings, runtime provenance, warnings, and unmatched IDs.
  - Health/runtime configuration is exposed in the UI, including the currently observed API endpoint.
  - Result rendering has been refactored into a workbench-style layout rather than a single long summary stack.
  - The workbench now includes a lineage graph for selected and alternative ancestry candidates.
  - The lineage view now acts as a lineage explorer with hypothesis-path and signal-ring modes, grouping ancestry candidates by evidence depth instead of showing only a single selected edge plus a flat fallback list.
  - The workbench now includes an annotation panel that ties paper claims, mapping evidence, semantic tags, line-anchored code snippets, and file reading order into a reviewer flow.
  - The annotation panel now includes a Monaco-based diff viewer backed by real old/new code anchors.
  - The Monaco reviewer now supports both focused anchor inspection and an aggregated full-cluster patch view, so reviewers can inspect an entire diff cluster without hopping snippet-by-snippet.
- Evidence:
  - [analysis-form.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-form.tsx)
  - [analysis-results-workbench.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-results-workbench.tsx)
  - [analysis-lineage-graph.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-lineage-graph.tsx)
  - [analysis-evidence-panel.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-evidence-panel.tsx)
  - [analysis-monaco-diff-viewer.tsx](/Users/kakusou/work/code/project/PaperTrace/apps/web/components/analysis-monaco-diff-viewer.tsx)
  - [api.ts](/Users/kakusou/work/code/project/PaperTrace/apps/web/lib/api.ts)
- Remaining:
  - The lineage explorer still infers hop depth from evidence rings rather than from a true fetched ancestor graph.
  - There is still no temporal ancestry timeline sourced from repository history or release metadata.

## Architecture Alignment

### Monorepo Shape

- Status: `completed`
- The repository structure matches the planned `apps`, `packages`, `fixtures`, `infra`, and `.github/workflows` layout.

### Domain Boundaries

- Status: `mostly completed`
- `apps/api` owns HTTP concerns.
- `apps/worker` owns queue orchestration.
- `packages/analysis-core` owns fetch, parse, trace, diff, and mapping logic.
- `packages/contracts` owns generated OpenAPI contracts.
- Remaining:
  - `AnalysisRepository` is still effectively implemented through the storage helpers rather than a more explicit repository interface abstraction.

### Adapter Boundaries

- Status: `mostly completed`
- There are distinct boundaries for paper fetching, repo metadata, repo mirroring, parsing, diff analysis, and contribution mapping.
- Evidence:
  - [interfaces.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/interfaces.py)

## Engineering Standards Alignment

### Tooling And Commands

- Status: `completed`
- Root commands required by plan exist:
  - `make bootstrap`
  - `make dev`
  - `make lint`
  - `make test`
  - `make e2e`
  - `make down`
  - `make smoke`
- Evidence:
  - [Makefile](/Users/kakusou/work/code/project/PaperTrace/Makefile)

### Typed Config And Boundaries

- Status: `mostly completed`
- Typed settings objects are used.
- Environment access is centralized through settings.
- Pydantic models define API and core boundaries.
- Evidence:
  - [settings.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/settings.py)
  - [models.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/src/papertrace_core/models.py)

### Contract Generation

- Status: `completed`
- OpenAPI contracts are generated into `packages/contracts`.
- Web consumes shared contract types rather than hand-maintained duplicates.
- Evidence:
  - [packages/contracts/src/index.ts](/Users/kakusou/work/code/project/PaperTrace/packages/contracts/src/index.ts)
  - [generate_contracts.sh](/Users/kakusou/work/code/project/PaperTrace/infra/scripts/generate_contracts.sh)

## Quality And Delivery Alignment

### Unit And Integration Coverage

- Status: `mostly completed`
- Current tests cover:
  - input normalization
  - paper source fetching
  - repo metadata fetch and fallback
  - parser merge and gap-detection behavior
  - repo ancestry strategies including framework signature, dependency archaeology, fossil evidence, and shape similarity
  - diff filtering, semantic tagging, and grouping behavior
  - API create/status/result endpoints
  - worker persistence path
- Remaining:
  - Default integration coverage still uses `sqlite` plus eager Celery.
  - PostgreSQL and Redis coverage live in smoke tests rather than default test runs.

### End-To-End Coverage

- Status: `mostly completed`
- Playwright covers:
  - shell rendering
  - JSON analysis submission
  - multipart PDF upload submission
  - result rendering and provenance display
- Evidence:
  - [home.spec.ts](/Users/kakusou/work/code/project/PaperTrace/apps/web/tests/home.spec.ts)

### Smoke Coverage

- Status: `mostly completed`
- Smoke tests cover:
  - live arXiv fetch
  - live GitHub metadata fetch
  - optional LLM extraction path
  - one real golden case on the live analysis path
  - PostgreSQL + Redis + non-eager Celery lane
- Evidence:
  - [test_smoke.py](/Users/kakusou/work/code/project/PaperTrace/packages/analysis-core/tests/test_smoke.py)

## Workstream Status Alignment With Previous Plan

### 1. Real Paper Input Path

- Previous plan status: `mostly completed`
- Current alignment: `confirmed`
- Notes:
  - This workstream is now complete for the planned MVP input surface.
  - Remaining parser limitations now sit in parsing quality, not in missing source-ingestion or upload contract paths.

### 2. Real Paper Parser MVP

- Previous plan status: `mostly completed`
- Current alignment: `confirmed with caveat`
- Notes:
  - The parser is no longer just a thin heuristic layer over fetched text.
  - Section-specialized extraction, merge logic, and gap detection are now present.
  - The remaining gap is now a stronger global refinement layer and better non-golden quality, not single-window truncation.

### 3. Repo Tracer De-Fixturing

- Previous plan status: `mostly completed`
- Current alignment: `confirmed with caveat`
- Notes:
  - The main path is no longer just golden-fixture candidate replay.
  - The tracer now includes framework-signature, dependency-archaeology, fossil, citation-graph, author-graph, temporal-topic, shape-similarity, code-reference, and fingerprint layers.
  - It is still not a mature cascade-search engine because deeper graph ranking and broader registries are still missing.

### 4. Live Diff As The Default Analysis Path

- Previous plan status: `mostly completed`
- Current alignment: `confirmed with caveat`
- Notes:
  - Default local config now prefers live analysis through `ENABLE_LIVE_BY_DEFAULT=true` in `.env.example`.
  - The existence of a live path is real.
  - The analyzer now has semantic tags, lightweight cross-file grouping, and honest empty-result handling, but it still falls short of a true semantic diff engine.

### 5. Evidence-Oriented Contribution Mapping

- Previous plan status: `mostly completed`
- Current alignment: `confirmed with caveat`
- Notes:
  - Output is now inspectable and preserves unmatched entities.
  - Explicit completeness semantics now exist in the model and output.
  - It still falls short of the formula-to-code explanation quality described in `AGENTS.md`.

### 6. Realistic Validation Layer

- Previous plan status: `mostly completed`
- Current alignment: `confirmed`
- Notes:
  - Smoke coverage now exercises the realistic lane.
  - CI still does not run the smoke lane by default.

## Current Stage Conclusion

- Relative to the original `AGENTS.md` vision, PaperTrace is no longer at the pure scaffold stage.
- Relative to the formal `plan/` MVP, the project is now in a late MVP-backbone stage:
  - infrastructure, API, worker, contracts, local UI, and validation baseline are largely in place
  - the real analysis path exists and can run
  - the largest remaining gaps are ancestry search depth, parser quality, semantic diff quality, mapping completeness semantics, and result presentation depth

## Next Stage Recommendation

- Recommended name: `Stage 3 - Analysis Quality And Presentation`
- Recommended focus:
  - improve generic paper parsing quality
  - strengthen ancestry scoring beyond the small alias map
  - improve semantic diff clustering
  - improve mapping completeness and evidence quality
  - add the first richer code presentation layer such as annotated diff views

## Priority Gap List

### P0

- Improve non-golden paper parsing quality.
- Replace the current narrow repo-tracing search space with a broader cascade-search design.
- Reduce repo tracer dependence on the built-in upstream alias list.
- Improve live diff clustering beyond the current lightweight semantic heuristics.

### P1

- Add parser global refinement and stronger non-golden extraction quality.
- Improve mapping algorithm-to-code evidence quality.
- Introduce richer UI presentation for code evidence.

### P2

- Add LaTeX-source ingestion.
- Deepen initial-commit ancestry analysis.
- Add GitHub code search, citation graph, and author graph ancestry strategies.
- Expand fingerprint coverage across more upstream repositories.
