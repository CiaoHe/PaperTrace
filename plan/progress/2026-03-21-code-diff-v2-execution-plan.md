# Code Diff V2 Execution Plan

## Purpose

- This document is the execution baseline for the Code Diff V2 rewrite.
- It replaces the earlier exploratory suggestions with a decision-complete implementation plan.
- It is meant to be used as the reference document for later progress tracking and status alignment.

## Summary

- The implementation target is a `review artifact first` architecture.
- Backend builds a stable cross-repo review artifact before the web review UI renders anything.
- The main diff viewer is `react-diff-view`.
- `Monaco` is retained only for full-file deep read.
- `difftastic` is optional and only used as a semantic gate or annotation enhancer.
- `diff2html` is only used for prebuilt large-file fallback rendering.
- Existing `DiffCluster` and `ContributionMapping` remain as compatibility projections, not the primary source of truth.

## Scope

- This plan only covers Code Diff V2 after `source repo` and `current repo` are already known.
- This plan does not include further repo ancestry tracing work.
- This plan is additive and must not break:
  - `POST /api/v1/analyses`
  - `GET /api/v1/analyses/{id}`
  - `GET /api/v1/analyses/{id}/result`

## Locked Decisions

### Stable IDs

- Only two public stable IDs are persisted:
  - `file_id`
  - `hunk_id`
- `line_id` is not exposed publicly.
- All stable IDs must be derived from canonical structured serialization, never raw string concatenation.
- Canonical serialization is:
  - `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`
- Internal line anchors, when needed, use:
  - `line_anchor_id = sha256(canonical_json({"hunk_id": hunk_id, "side": side, "line_no": line_no}))[:20]`
- `file_id` is:
  - `sha256(canonical_json({
      "cache_key": cache_key,
      "source_rel_path": source_rel_path_or_devnull,
      "current_rel_path": current_rel_path_or_devnull,
      "diff_type": diff_type,
    }))[:24]`
- `hunk_id` is:
  - `sha256(canonical_json({
      "file_id": file_id,
      "old_start": old_start,
      "old_len": old_len,
      "new_start": new_start,
      "new_len": new_len,
      "content_hash": normalized_changed_lines_hash,
    }))[:24]`

### Contribution And Claim Identity

- `contribution.id` remains for existing summary UI compatibility.
- Stable contribution identity uses:
  - `contribution_key = sha256(canonical_json({
      "title": normalized_title,
      "section": normalized_section,
      "first_impl_hint": normalized_first_impl_hint,
    }))[:16]`
- Claims use both a stable machine ID and a display label:
  - `claim_id = sha256(canonical_json({
      "contribution_key": contribution_key,
      "claim_text": normalized_claim_text,
    }))[:20]`
  - `claim_label = {contribution.id}.S{ordinal}`
- Claim order is deterministic:
  - field priority order first
  - sentence index second

### Normalization Rules

- `normalized_title`, `normalized_section`, and `normalized_first_impl_hint` use:
  - Unicode `NFKC`
  - lowercase
  - replace punctuation and underscores with spaces
  - collapse repeated whitespace
  - trim
- `normalized_claim_text` uses:
  - Unicode `NFKC`
  - remove zero-width and soft-hyphen characters
  - repair PDF line-break hyphenation where a line ends with `-` followed by a lowercase continuation
  - collapse repeated whitespace
  - trim
  - lowercase
- `normalized_changed_lines_hash` is computed from a canonical JSON array of changed lines only:
  - include only added and removed lines, never hunk headers
  - strip unified diff line prefixes `+` and `-`
  - normalize line endings to `\n`
  - apply Unicode `NFKC`
  - preserve leading whitespace
  - strip trailing whitespace
  - preserve internal and blank lines
- If a rule changes, it must change the corresponding algorithm version digest so cache keys do not silently reuse stale artifacts.

### Review Build Lifecycle

- Review artifact build is eager and asynchronous.
- After the main analysis succeeds, the system automatically enqueues:
  - `build_review_artifact(job_id)`
- Optional async refinement is separate:
  - `refine_review_links(job_id)`
- `GET /review` must never synchronously build the artifact.
- `GET /review` may lazily re-enqueue if:
  - the review row does not exist
  - the artifact is missing
  - a rebuild was explicitly requested
- `POST /review/rebuild` always means a full rebuild in v1.
- A rebuild:
  - resets `build_status` to `pending`
  - clears `build_error`
  - writes a new temp artifact directory
  - atomically replaces the old ready artifact on success
  - resets `refinement_status` to `disabled` or `queued` depending on LLM availability
- If LLM refinement is enabled, successful rebuild automatically re-enqueues `refine_review_links(job_id)`.
- There is no `refine_only` public API in v1.

### Review Endpoint Semantics

- `GET /api/v1/analyses/{job_id}/review`
  - `200`: review artifact ready
  - `202`: analysis still running, or review build is pending/building
  - `409`: analysis failed and review cannot be built
  - `404`: job does not exist
- `POST /api/v1/analyses/{job_id}/review/rebuild`
  - enqueue retry only
  - never perform synchronous rebuild work

### Cache Key And Diff Fingerprint

- `cache_key` must include:
  - `paper_source_hash`
  - `source_repo_url`
  - `current_repo_url`
  - `source_revision`
  - `current_revision`
  - `diff_settings_fingerprint`
- `diff_settings_fingerprint` is the `sha256` of sorted JSON over:
  - `file_mapper_version`
  - `claim_splitter_version`
  - `link_retrieval_version`
  - `context_lines`
  - `repo_analysis_extensions`
  - `repo_analysis_exclude_dirs`
  - `repo_analysis_exclude_filenames`
  - `repo_max_file_size_bytes`
  - `repo_max_files`
  - `semantic_gate_enabled`
  - `semantic_gate_timeout_seconds`
  - `large_file_line_threshold`
  - `large_file_diff_bytes_threshold`
  - `ambiguous_match_margin`
  - `review_primary_languages`
- `file_mapper_version`, `claim_splitter_version`, and `link_retrieval_version` are not manual semver strings.
- They are source digests of their implementation modules:
  - `sha256(module_source_text)[:8]`
- This is required so algorithm changes automatically invalidate cache keys.

### Revision Identity

- `source_revision` and `current_revision` use:
  - git `HEAD` commit SHA when available
  - otherwise a content fingerprint fallback
- The content fingerprint fallback is:
  - `sha256(canonical_json([{relative_path, content_sha256}, ...sorted...]))[:16]`
- The included file set for this fingerprint must be the same filtered tracked-file set used by review build input selection.

### Claim Splitting

- Claim splitting is deterministic and offline.
- It does not depend on a live LLM.
- Input sources are consumed in priority order:
  - `title`
  - `problem_solved`
  - `baseline_difference`
  - each `impl_hint`
  - future parser-provided `evidence_sentences`
- Prose sentence splitting uses `syntok`.
- Protected academic abbreviations include:
  - `Eq.`
  - `Fig.`
  - `Sec.`
  - `Tab.`
  - `e.g.`
  - `i.e.`
  - `et al.`
- Top-level bullets and numbered items are treated as claim groups first, then sentence-split within the group.
- Pseudocode blocks, code blocks, and display formula blocks are not primary claims.
- They may be retained as supporting metadata only.
- Pure citations, duplicate claims, and fragments shorter than 12 characters are dropped.
- `claim_index` in the review manifest is intentionally lightweight.
- It only includes render-critical fields:
  - `claim_id`
  - `claim_label`
  - `contribution_key`
  - `contribution_id`
  - `section`
  - `claim_text`
  - `status`
- It must not inline heavy supporting metadata such as raw evidence blocks, full implementation hint arrays, or parser trace payloads.

### Artifact Root

- Review artifact storage is configurable.
- New setting:
  - `REVIEW_ARTIFACT_BASE_DIR`
- Default:
  - `LOCAL_DATA_DIR / "review-cache"`
- The database stores absolute artifact paths.

### Review Queue And Buckets

- Primary lane only contains:
  - `modified`
  - `comparable`
  - `non-ambiguous`
- Default sort order is:
  - linked claims present first
  - significance `high > medium > low`
  - changed line count descending
  - current path ascending
- Secondary buckets include:
  - `Added`
  - `Deleted`
  - `Ambiguous`
  - `Low Confidence`
  - `Other Languages`
  - `Large Files`

### Ambiguous Match Handling

- `match_type="ambiguous"` is explicit.
- It applies when:
  - top1 similarity `>= 0.55`
  - top2 similarity `>= 0.55`
  - `top1 - top2 < 0.08`
- Ambiguous matches do not enter the primary review lane.
- `match_type="content_moved"` applies when:
  - top1 similarity `>= 0.55`
  - and `top2` is missing or `top1 - top2 >= 0.08`
- `match_type="low_confidence"` applies when:
  - `0.40 <= top1 similarity < 0.55`
- `low_confidence` entries do not enter the primary review lane and are shown under the `Low Confidence` secondary bucket.
- When `top1 similarity < 0.40`, the target file is treated as unmatched and falls back to `added`.

### LLM Refinement

- LLM adjudication does not block the main review build.
- Main build only produces deterministic candidate links.
- If LLM is configured, refinement runs later as a separate task.
- `refinement_status` enum:
  - `disabled`
  - `queued`
  - `running`
  - `ready`
  - `failed`
- Provider failures or rate limits only affect refinement, not base review availability.
- If base review build is `ready` but refinement later fails, the artifact stays usable and the UI shows deterministic links plus `refining failed`.

### Large File Fallback

- Large-file fallback is decided during build, not in the browser.
- Trigger thresholds:
  - `line_count > 5000`
  - or `raw_unified_diff_bytes > 524288`
- Worker pre-renders fallback HTML through:
  - `infra/scripts/render_diff2html.mjs`
- This helper requires `Node.js >= 18` on the same machine as the worker.
- If Node.js is unavailable or helper execution fails:
  - the review build must not fail
  - the file falls back to `fallback_mode="raw_diff_only"`
  - `fallback_html_path` is `null`
- API returns:
  - `fallback_mode="diff2html_prebuilt"`
  - `fallback_html_path`
- `fallback_html_path` is a fetchable API-relative URL path, never a filesystem path.

### Language Policy

- Primary linking coverage is only for:
  - `.py`
  - `.pyi`
- Secondary visible-but-not-linked review files include:
  - `.cu`
  - `.cuh`
  - `.cc`
  - `.cpp`
  - `.h`
  - `.rs`
- Triton `.py` files still count as Python and remain eligible for primary review.

### Unmapped Contributions

- Unmapped contributions must not create fake `ContributionMapping` rows.
- Existing summary compatibility remains through:
  - `AnalysisResult.unmatched_contribution_ids`
- Review manifest adds a separate contribution status layer:
  - `mapped`
  - `partially_mapped`
  - `unmapped`
  - `refining`

## Data Model And Interfaces

### New Storage Table

- Add `analysis_review_sessions` with:
  - `id UUID PRIMARY KEY`
  - `analysis_job_id TEXT UNIQUE NOT NULL`
  - `cache_key TEXT NOT NULL`
  - `paper_source_hash TEXT NOT NULL`
  - `source_repo_url TEXT NOT NULL`
  - `current_repo_url TEXT NOT NULL`
  - `source_revision TEXT NOT NULL`
  - `current_revision TEXT NOT NULL`
  - `artifact_dir TEXT NOT NULL`
  - `manifest_summary_json JSON NOT NULL`
  - `build_status TEXT NOT NULL`
  - `build_phase TEXT NOT NULL`
  - `build_progress FLOAT NOT NULL`
  - `files_total INTEGER NOT NULL`
  - `files_done INTEGER NOT NULL`
  - `current_file TEXT NULL`
  - `build_error TEXT NULL`
  - `artifact_size_kb INTEGER NULL`
  - `difftastic_used BOOLEAN NOT NULL`
  - `refinement_status TEXT NOT NULL`
  - `created_at`
  - `updated_at`

### Build Status Enums

- `build_status`:
  - `pending`
  - `building`
  - `ready`
  - `failed`
- `build_phase`:
  - `waiting_for_analysis`
  - `file_mapping`
  - `diff_generation`
  - `fallback_render`
  - `claim_extraction`
  - `deterministic_linking`
  - `persisting`
  - `done`

### Progress Transport

- `analysis_jobs.stage_detail` remains a plain human-readable string.
- Structured review progress must come from `analysis_review_sessions` and the `/review` `202` payload.
- No JSON schema is added to `stage_detail`.

### New API Endpoints

- `GET /api/v1/analyses/{job_id}/review`
  - `200`: `ReviewManifest`
  - `202`: `ReviewBuildStatusResponse`
  - `409`: `ReviewUnavailableResponse`
- `GET /api/v1/analyses/{job_id}/review/files/{file_id}`
  - `200`: `ReviewFilePayload`
- `GET /api/v1/analyses/{job_id}/review/files/{file_id}/rendered`
  - `200`: prebuilt fallback HTML with `Content-Type: text/html`
- `POST /api/v1/analyses/{job_id}/review/rebuild`
  - `202`: re-enqueued

### ReviewBuildStatusResponse

- Fields:
  - `analysis_status`
  - `build_status`
  - `build_phase`
  - `build_progress`
  - `files_total`
  - `files_done`
  - `current_file`
  - `refinement_status`
  - `detail`

### ReviewManifest

- Fields:
  - `source_repo`
  - `current_repo`
  - `source_revision`
  - `current_revision`
  - `file_tree`
  - `review_queue`
  - `secondary_buckets`
  - `claim_index`
  - `contribution_status`
  - `summary_counts`
  - `artifact_version`
  - `cache_key`
  - `refinement_status`
- `claim_index` is a lightweight list, not a dump of full parser evidence.
- `secondary_buckets` is a fixed object shape:
  - `added`
  - `deleted`
  - `ambiguous`
  - `low_confidence`
  - `other_languages`
  - `large_files`
- Each secondary bucket contains:
  - `label`
  - `count`
  - `files`

### ReviewFilePayload

- Fields:
  - `file_id`
  - `source_path`
  - `current_path`
  - `diff_type`
  - `match_type`
  - `semantic_status`
  - `stats`
  - `raw_unified_diff`
  - `hunks`
  - `linked_claim_ids`
  - `linked_cluster_ids`
  - `fallback_mode`
  - `fallback_html_path`
- `fallback_html_path`, when present, is the API-relative path returned by:
  - `GET /api/v1/analyses/{job_id}/review/files/{file_id}/rendered`
- `hunks` only contains metadata:
  - `hunk_id`
  - `old_start`
  - `old_length`
  - `new_start`
  - `new_length`
  - `added_count`
  - `removed_count`
  - `semantic_kind`
  - `linked_claim_ids`
  - `linked_contribution_keys`
- `semantic_status` enum is fixed:
  - `enhanced`
  - `fallback_text`
  - `unsupported_language`
  - `equivalent`
  - `new_file`
  - `deleted_file`
  - `large_file`

### Manifest Storage Boundary

- `manifest.json` stored on disk is the source of truth for a ready review artifact.
- `manifest_summary_json` stored in `analysis_review_sessions` is a query-friendly subset only.
- `manifest_summary_json` contains:
  - revision metadata
  - bucket counts
  - queue counts
  - claim counts
  - artifact version
  - refinement status
- `manifest_summary_json` must not duplicate full file tree, full claim index, raw diffs, or per-file hunk payloads.
- `GET /review` reads full `manifest.json` from disk when `build_status=ready`.
- If the DB row says `ready` but `manifest.json` is missing, the review session is treated as stale and must be re-enqueued.

## Runtime And Concurrency

### Build Trigger Rules

- When main analysis succeeds:
  - enqueue `build_review_artifact(job_id)`
- `GET /review` behavior:
  - analysis still running -> `202 waiting_for_analysis`
  - no review row -> upsert `pending`, enqueue build, return `202`
  - `pending` or `building` -> `202`
  - `ready` -> `200`
  - `failed` -> `409`, unless rebuild was just requested

### Concurrency Protection

- Use Redis lock:
  - key `review-build:{cache_key}`
  - TTL 15 minutes
- Use temp directory write pattern:
  - `artifact_dir.tmp.<uuid>`
  - atomic rename to final path after build success
- Use DB upsert keyed on:
  - `analysis_job_id`

### Shared Artifact Reuse

- Multiple jobs may point to the same final artifact if they share the same `cache_key`.
- Locking happens per `cache_key`.
- Session tracking remains per `analysis_job_id`.

### Artifact Layout

- `manifest.json`
- `files/{file_id}.json`
- `raw/{file_id}.diff`
- `rendered/{file_id}.html`
- `meta/build.json`
- Optional future-heavy claim metadata may live under:
  - `claims/{claim_id}.json`
- V1 public API does not require a dedicated claim-details endpoint.

## UI And Mapping

- Keep route:
  - `/analyses/[jobId]/evidence`
- Replace its internals with the review shell.

### File Tree Filters

- Left pane must expose:
  - `Primary`
  - `Added`
  - `Deleted`
  - `Ambiguous`
  - `Low Confidence`
  - `Other Languages`
  - `Large Files`
- Default selection:
  - first item in `Primary`
- If `Primary` is empty:
  - show explicit empty state
  - never leave the diff pane blank without explanation

### Diff Pane

- Main renderer:
  - `react-diff-view`
- Rendering source:
  - `raw_unified_diff`
- Mode:
  - split view

### Claim Pane

- Right pane shows sentence-level claims.
- Claim statuses include:
  - `mapped`
  - `refining`
  - `unmapped`
  - `skipped_non_primary_language`

### Deterministic Retrieval

- `top-k = 5`
- Scoring features:
  - path token overlap
  - symbol overlap
  - semantic tag overlap
  - impl hint overlap
  - equation and citation marker overlap
  - changed identifier overlap

### LLM Adjudication

- LLM may only classify existing retrieved candidates as:
  - `supports`
  - `partial`
  - `unrelated`
- LLM may not invent:
  - new file matches
  - new hunk candidates
  - new repo relationships

### Projection Rules

- Accepted claim-hunk links aggregate upward to contributions.
- Coverage outcomes:
  - accepted links covering major claims -> `mapped`
  - accepted links covering only some claims -> `partially_mapped`
  - no accepted links -> contribution enters `unmatched_contribution_ids`
- Summary pages continue using:
  - `unmatched_contribution_ids`
  - `unmatched_diff_cluster_ids`

## Phase Gates

### Phase 1: Backend Foundation

- Deliver:
  - `diff_review/models.py`
  - `diff_review/file_mapper.py`
  - `diff_review/unified_diff.py`
  - `REVIEW_ARTIFACT_BASE_DIR`
  - `diff_settings_fingerprint`
  - stable ID rules
- Required tests:
  - file mapper unit tests
  - ambiguous match tests
  - low-confidence match tests
  - unified diff tests
  - ID stability tests
  - normalization tests
  - revision fingerprint fallback tests
- Status:
  - [x] core deliverables implemented
  - [x] review backend unit coverage added for mapper, claim normalization, revision fallback, and stable hunk IDs

### Phase 2: Artifact Store And Review Status API

- Deliver:
  - `analysis_review_sessions`
  - Redis lock
  - atomic rename flow
  - `/review` status protocol
- Required tests:
  - concurrency protection
  - re-enqueue behavior
  - cache hit reuse
  - build failure handling
  - manifest summary vs manifest file boundary
  - rebuild resets refinement state
- Status:
  - [x] review session table, Redis lock, atomic artifact swap, and `/review` endpoints implemented
  - [ ] remaining hardening coverage still needed for concurrency, cache-hit reuse, and rebuild/refinement edge cases

### Phase 3: Review File Payload And Large-File Fallback

- Deliver:
  - `ReviewFilePayload`
  - metadata-only hunk payloads
  - Node helper prebuilt `diff2html`
- Required tests:
  - payload size guard
  - fallback path correctness
  - prebuilt HTML retrieval
  - Node unavailable -> `raw_diff_only`
  - semantic status enum coverage
- Status:
  - [x] metadata-only review payloads implemented
  - [x] `infra/scripts/render_diff2html.mjs` wired into build-time large-file fallback
  - [x] backend tests added for payload guard, rendered HTML retrieval, semantic status coverage, and Node-unavailable degradation

### Phase 4: Web Review Shell Replacement

- Deliver:
  - three-pane review layout
  - file tree buckets
  - `react-diff-view` integration
  - Monaco deep-read fallback
- Required tests:
  - file switching
  - claim switching
  - secondary bucket visibility
  - low-confidence bucket visibility
  - empty state behavior
- Status:
  - [x] `/analyses/[jobId]/evidence` switched to the review shell
  - [x] three-pane review layout, bucket tabs, `react-diff-view`, and raw/HTML fallbacks are live
  - [x] web e2e covers evidence route entry, claim interaction, file interaction, and bucket visibility
  - [x] review file payloads now include source/current full-file content and the diff pane supports `diff` / `source` / `current` deep-read mode through Monaco

### Phase 5: Claim Extraction And Deterministic Linking

- Deliver:
  - `claims.py`
  - claim status model
  - deterministic top-k retrieval
  - projection layer
- Required tests:
  - claim split stability
  - lightweight claim index shape
  - mapped status
  - partially mapped status
  - unmapped status
- Status:
  - [x] deterministic claim splitting and lightweight claim index are in place
  - [x] hunk-level deterministic retrieval is wired into review artifact build with stable `hunk_id` joins
  - [x] claim, hunk, file, and contribution projection are derived from accepted deterministic links
  - [x] result payload compatibility fields (`mappings`, `unmatched_contribution_ids`, `unmatched_diff_cluster_ids`) are now projected back from review links

### Phase 6: LLM Refinement

- Deliver:
  - `refine_review_links(job_id)`
  - refinement state machine
  - rate-limit-safe fallback
- Required tests:
  - `disabled`
  - `queued`
  - `running`
  - `failed`
  - `ready`
  - rebuild after failed refinement
- Status:
  - [x] `refine_review_links(job_id)` runs as a separate Celery task and never blocks the base review artifact
  - [x] refinement state transitions `disabled | queued | running | ready | failed` are persisted and surfaced through the review manifest/status flow
  - [x] provider failures degrade to deterministic review availability and leave the artifact usable

### Phase 7: Regression And Progress Alignment

- Deliver:
  - `make lint`
  - `make test`
  - `make e2e`
  - updated progress status docs
- Status:
  - [x] `make lint` passes
  - [x] `make test` passes
  - [x] `make e2e` passes on isolated local ports
  - [x] progress status doc updated to match the implemented review v2 state

## Tracking Checklist

- [x] Stable IDs implemented
- [x] Review session table implemented
- [x] Review build Celery task wired
- [x] Review status endpoints implemented
- [x] Artifact store and cache reuse implemented
- [x] Large-file fallback pre-render implemented
- [x] Review shell switched to `react-diff-view`
- [x] Claim extraction implemented
- [x] Deterministic claim-hunk linking implemented
- [x] LLM refinement implemented
- [x] Projection back to summary models implemented
- [x] Status docs updated after rollout

## Acceptance Criteria

- A succeeded analysis automatically schedules review artifact build.
- `GET /review` is safe under concurrent access and never does synchronous heavy work.
- A ready review session exposes stable `file_id` and `hunk_id`.
- Large files never force client-side heavy diff rendering.
- If Node.js is unavailable, large files still remain reviewable through `raw_diff_only`.
- Primary review lane never shows ambiguous or non-comparable files by default.
- Unmapped contributions remain explicit and are not hidden behind synthetic mappings.
- Existing summary and result APIs remain compatible.

## Assumptions

- This plan assumes source repo and current repo are already known.
- This plan does not change repo ancestry tracing behavior.
- `analysis_jobs.stage_detail` remains a string.
- Structured review progress is surfaced only through the new review status API.
- Worker and API share the same local machine and can access the same absolute artifact directory.
- `Node.js >= 18` is required for prebuilt `diff2html` fallback; when absent, the system degrades to `raw_diff_only` instead of failing the build.
