from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from papertrace_core.diff_review.claims import split_contribution_claims
from papertrace_core.diff_review.common import stable_digest
from papertrace_core.diff_review.file_mapper import FileMapper, FilePair
from papertrace_core.diff_review.locks import review_build_lock
from papertrace_core.diff_review.models import (
    ReviewBucket,
    ReviewBucketKind,
    ReviewBuildPhase,
    ReviewBuildStatus,
    ReviewClaimIndexEntry,
    ReviewContributionStatus,
    ReviewDiffType,
    ReviewFallbackMode,
    ReviewFileEntry,
    ReviewFileTreeNode,
    ReviewManifest,
    ReviewMatchType,
    ReviewRefinementStatus,
    ReviewSemanticStatus,
    ReviewSummaryCounts,
)
from papertrace_core.diff_review.projection import project_contribution_status
from papertrace_core.diff_review.rendering import render_prebuilt_diff2html
from papertrace_core.diff_review.retrieval import build_file_candidates, retrieve_claim_file_links
from papertrace_core.diff_review.revision import module_source_digest, resolve_repo_revision
from papertrace_core.diff_review.unified_diff import (
    build_file_payload,
    build_raw_diff_only_payload,
    generate_raw_unified_diff,
)
from papertrace_core.models import AnalysisResult, JobStatus
from papertrace_core.repos import ShallowGitRepoMirror
from papertrace_core.settings import Settings, get_settings
from papertrace_core.storage import (
    ensure_review_session,
    get_job_result,
    get_job_summary,
    mark_review_session_building,
    mark_review_session_failed,
    mark_review_session_ready,
)

ARTIFACT_VERSION = "review-v2-phase2"


def build_review_artifact_for_job(job_id: str) -> ReviewManifest | None:
    settings = get_settings()
    summary = get_job_summary(job_id)
    if summary is None:
        raise ValueError(f"Job not found: {job_id}")
    if summary.status != JobStatus.SUCCEEDED:
        ensure_review_session(
            job_id,
            paper_source=summary.paper_source,
            current_repo_url=summary.repo_url,
        )
        return None
    result = get_job_result(job_id)
    if result is None:
        raise ValueError(f"Analysis result not available for review build: {job_id}")

    mirror = ShallowGitRepoMirror(settings)
    source_repo = result.selected_base_repo.repo_url
    current_repo = summary.repo_url
    source_root = mirror.prepare(source_repo)
    current_root = mirror.prepare(current_repo)
    paper_source_hash = stable_digest(summary.paper_source, length=16)
    source_revision = resolve_repo_revision(source_root, settings)
    current_revision = resolve_repo_revision(current_root, settings)
    cache_key = stable_digest(
        {
            "paper_source_hash": paper_source_hash,
            "source_repo_url": source_repo,
            "current_repo_url": current_repo,
            "source_revision": source_revision,
            "current_revision": current_revision,
            "diff_settings_fingerprint": build_diff_settings_fingerprint(),
        },
        length=32,
    )
    artifact_dir = settings.resolved_review_artifact_base_dir / cache_key
    ensure_review_session(
        job_id,
        paper_source=summary.paper_source,
        current_repo_url=current_repo,
        cache_key=cache_key,
        source_repo_url=source_repo,
        source_revision=source_revision,
        current_revision=current_revision,
        artifact_dir=artifact_dir,
    )

    manifest_path = artifact_dir / "manifest.json"
    if manifest_path.exists():
        manifest = ReviewManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        mark_review_session_ready(job_id, manifest=manifest, artifact_dir=artifact_dir)
        return manifest

    with review_build_lock(cache_key, settings) as acquired:
        if not acquired:
            ensure_review_session(
                job_id,
                paper_source=summary.paper_source,
                current_repo_url=current_repo,
                cache_key=cache_key,
                source_repo_url=source_repo,
                source_revision=source_revision,
                current_revision=current_revision,
                artifact_dir=artifact_dir,
                build_status=ReviewBuildStatus.BUILDING,
                build_phase=ReviewBuildPhase.FILE_MAPPING,
            )
            return None
        if manifest_path.exists():
            manifest = ReviewManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            mark_review_session_ready(job_id, manifest=manifest, artifact_dir=artifact_dir)
            return manifest

        temp_dir = artifact_dir.parent / f"{artifact_dir.name}.tmp.{uuid4().hex}"
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            mark_review_session_building(job_id, build_phase=ReviewBuildPhase.FILE_MAPPING, detail="Mapping files.")
            manifest = _build_manifest(
                job_id=job_id,
                result=result,
                source_root=source_root,
                current_root=current_root,
                source_repo=source_repo,
                current_repo=current_repo,
                source_revision=source_revision,
                current_revision=current_revision,
                cache_key=cache_key,
                artifact_dir=temp_dir,
            )
            temp_manifest_path = temp_dir / "manifest.json"
            temp_manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            (temp_dir / "meta").mkdir(parents=True, exist_ok=True)
            (temp_dir / "meta" / "build.json").write_text(
                json.dumps(
                    {
                        "artifact_version": ARTIFACT_VERSION,
                        "cache_key": cache_key,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            if artifact_dir.exists():
                shutil.rmtree(artifact_dir, ignore_errors=True)
            temp_dir.rename(artifact_dir)
            mark_review_session_ready(job_id, manifest=manifest, artifact_dir=artifact_dir)
            return manifest
        except Exception as exc:
            shutil.rmtree(temp_dir, ignore_errors=True)
            mark_review_session_failed(job_id, str(exc))
            return None


def build_diff_settings_fingerprint() -> str:
    settings = get_settings()
    import papertrace_core.diff_review.claims as claims
    import papertrace_core.diff_review.file_mapper as file_mapper

    payload = {
        "file_mapper_version": module_source_digest(file_mapper),
        "claim_splitter_version": module_source_digest(claims),
        "link_retrieval_version": "phase2-bootstrap",
        "context_lines": settings.review_context_lines,
        "repo_analysis_extensions": settings.repo_analysis_extensions,
        "repo_analysis_exclude_dirs": settings.repo_analysis_exclude_dirs,
        "repo_analysis_exclude_filenames": settings.repo_analysis_exclude_filenames,
        "repo_max_file_size_bytes": settings.repo_max_file_size_bytes,
        "repo_max_files": settings.repo_max_files,
        "semantic_gate_enabled": settings.review_semantic_gate_enabled,
        "semantic_gate_timeout_seconds": settings.review_semantic_gate_timeout_seconds,
        "large_file_line_threshold": settings.review_large_file_line_threshold,
        "large_file_diff_bytes_threshold": settings.review_large_file_diff_bytes_threshold,
        "ambiguous_match_margin": settings.review_ambiguous_match_margin,
        "review_primary_languages": settings.review_primary_languages,
    }
    return stable_digest(payload, length=16)


def _build_manifest(
    *,
    job_id: str,
    result: AnalysisResult,
    source_root: Path,
    current_root: Path,
    source_repo: str,
    current_repo: str,
    source_revision: str,
    current_revision: str,
    cache_key: str,
    artifact_dir: Path,
) -> ReviewManifest:
    settings = get_settings()
    mapper = FileMapper(settings)
    pairs = mapper.map_repositories(source_root, current_root)
    claim_entries = _build_claim_index(result)
    candidate_inputs: list[tuple[str, str, str]] = []
    prepared_pairs: list[tuple[FilePair, str, str, ReviewSemanticStatus]] = []
    files_dir = artifact_dir / "files"
    raw_dir = artifact_dir / "raw"
    rendered_dir = artifact_dir / "rendered"
    files_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)
    review_entries: list[ReviewFileEntry] = []
    file_tree_entries: list[ReviewFileEntry] = []
    secondary: dict[str, list[ReviewFileEntry]] = {
        "added": [],
        "deleted": [],
        "ambiguous": [],
        "low_confidence": [],
        "other_languages": [],
        "large_files": [],
    }

    for index, pair in enumerate(pairs, start=1):
        mark_review_session_building(
            job_id,
            build_phase=ReviewBuildPhase.DIFF_GENERATION,
            build_progress=index / max(len(pairs), 1),
            files_total=len(pairs),
            files_done=index - 1,
            current_file=pair.current_path or pair.source_path,
            detail=f"Generating diff for {pair.current_path or pair.source_path}.",
        )
        file_id = stable_digest(
            {
                "cache_key": cache_key,
                "source_rel_path": pair.source_path or "/dev/null",
                "current_rel_path": pair.current_path or "/dev/null",
                "diff_type": pair.diff_type.value,
            }
        )
        raw_unified_diff = generate_raw_unified_diff(
            source_root,
            current_root,
            source_path=pair.source_path,
            current_path=pair.current_path,
            context_lines=settings.review_context_lines,
        )
        file_path = pair.current_path or pair.source_path or ""
        semantic_status = _semantic_status_for_pair(pair)
        candidate_inputs.append((file_path, pair.language, raw_unified_diff))
        prepared_pairs.append(
            (
                pair,
                file_id,
                raw_unified_diff,
                semantic_status,
            )
        )
    candidate_files = build_file_candidates(result, candidate_inputs, settings)
    deterministic_links = retrieve_claim_file_links(
        claim_entries=claim_entries,
        contributions=result.contributions,
        candidate_files=candidate_files,
    )
    claim_entries, contribution_status, linked_claims_by_file = project_contribution_status(
        claim_entries,
        deterministic_links,
    )

    for index, (pair, file_id, raw_unified_diff, semantic_status) in enumerate(prepared_pairs, start=1):
        linked_claim_ids, linked_contribution_keys = linked_claims_by_file.get(
            pair.current_path or pair.source_path or "",
            ([], []),
        )
        fallback_mode = ReviewFallbackMode.NONE
        fallback_html_path: str | None = None
        raw_diff_path = raw_dir / f"{file_id}.diff"
        raw_diff_path.write_text(raw_unified_diff, encoding="utf-8")
        try:
            stored_payload = build_file_payload(
                file_id=file_id,
                source_path=pair.source_path,
                current_path=pair.current_path,
                diff_type=pair.diff_type,
                match_type=pair.match_type,
                raw_unified_diff=raw_unified_diff,
                semantic_status=semantic_status,
                fallback_mode=fallback_mode,
                fallback_html_path=fallback_html_path,
                linked_claim_ids=linked_claim_ids,
                linked_contribution_keys=linked_contribution_keys,
            )
        except Exception:
            stored_payload = build_raw_diff_only_payload(
                file_id=file_id,
                source_path=pair.source_path,
                current_path=pair.current_path,
                diff_type=pair.diff_type,
                match_type=pair.match_type,
                raw_unified_diff=raw_unified_diff,
                semantic_status=semantic_status,
                linked_claim_ids=linked_claim_ids,
                linked_contribution_keys=linked_contribution_keys,
            )
        changed_line_count = stored_payload.stats.changed_line_count
        is_large_file = raw_unified_diff.count("\n") > settings.review_large_file_line_threshold or len(
            raw_unified_diff.encode("utf-8")
        ) > settings.review_large_file_diff_bytes_threshold
        if is_large_file:
            mark_review_session_building(
                job_id,
                build_phase=ReviewBuildPhase.FALLBACK_RENDER,
                build_progress=index / max(len(pairs), 1),
                files_total=len(pairs),
                files_done=index - 1,
                current_file=pair.current_path or pair.source_path,
                detail=f"Rendering fallback diff for {pair.current_path or pair.source_path}.",
            )
            rendered_path = rendered_dir / f"{file_id}.html"
            if render_prebuilt_diff2html(raw_diff_path, rendered_path, settings):
                stored_payload.fallback_mode = ReviewFallbackMode.DIFF2HTML_PREBUILT
                stored_payload.fallback_html_path = (
                    f"/api/v1/analyses/{job_id}/review/files/{file_id}/rendered"
                )
            else:
                stored_payload.fallback_mode = ReviewFallbackMode.RAW_DIFF_ONLY
            stored_payload.semantic_status = ReviewSemanticStatus.LARGE_FILE
        significance = _significance_for_file(changed_line_count, len(linked_claim_ids))
        bucket = _bucket_for_pair(pair, stored_payload.semantic_status, settings)
        entry = ReviewFileEntry(
            file_id=file_id,
            source_path=pair.source_path,
            current_path=pair.current_path,
            diff_type=pair.diff_type,
            match_type=pair.match_type,
            semantic_status=stored_payload.semantic_status,
            language=pair.language,
            bucket=bucket,
            significance=significance,
            linked_claim_count=len(linked_claim_ids),
            linked_claim_ids=linked_claim_ids,
            linked_contribution_keys=linked_contribution_keys,
            stats=stored_payload.stats,
        )
        file_tree_entries.append(entry)
        if bucket == ReviewBucketKind.PRIMARY:
            review_entries.append(entry)
        else:
            secondary[bucket.value].append(entry)
        (files_dir / f"{file_id}.json").write_text(stored_payload.model_dump_json(indent=2), encoding="utf-8")

    sorted_primary = sorted(
        review_entries,
        key=lambda item: (
            -(1 if item.linked_claim_count > 0 else 0),
            -_significance_rank(item.significance),
            -item.stats.changed_line_count,
            item.current_path or item.source_path or "",
        ),
    )
    manifest = ReviewManifest(
        source_repo=source_repo,
        current_repo=current_repo,
        source_revision=source_revision,
        current_revision=current_revision,
        file_tree=_build_file_tree(file_tree_entries),
        review_queue=sorted_primary,
        secondary_buckets={
            key: ReviewBucket(label=_bucket_label(key), count=len(files), files=files)
            for key, files in secondary.items()
        },
        claim_index=claim_entries,
        contribution_status=contribution_status,
        summary_counts=ReviewSummaryCounts(
            total_files=len(file_tree_entries),
            primary_files=len(sorted_primary),
            total_claims=len(claim_entries),
            total_contributions=len(result.contributions),
        ),
        artifact_version=ARTIFACT_VERSION,
        cache_key=cache_key,
        refinement_status=ReviewRefinementStatus.DISABLED,
    )
    return manifest


def _build_claim_index(result: AnalysisResult) -> list[ReviewClaimIndexEntry]:
    claim_entries: list[ReviewClaimIndexEntry] = []
    for contribution in result.contributions:
        claim_entries.extend(split_contribution_claims(contribution, status=ReviewContributionStatus.UNMAPPED))
    return claim_entries


def _semantic_status_for_pair(pair: FilePair) -> ReviewSemanticStatus:
    if pair.diff_type == ReviewDiffType.ADDED:
        if pair.match_type == ReviewMatchType.LOW_CONFIDENCE:
            return ReviewSemanticStatus.FALLBACK_TEXT
        if pair.match_type == ReviewMatchType.AMBIGUOUS:
            return ReviewSemanticStatus.FALLBACK_TEXT
        return ReviewSemanticStatus.NEW_FILE
    if pair.diff_type == ReviewDiffType.DELETED:
        return ReviewSemanticStatus.DELETED_FILE
    if pair.language != "python":
        return ReviewSemanticStatus.UNSUPPORTED_LANGUAGE
    return ReviewSemanticStatus.FALLBACK_TEXT


def _significance_for_file(changed_line_count: int, linked_claim_count: int) -> str:
    if linked_claim_count > 0 or changed_line_count >= 50:
        return "high"
    if changed_line_count >= 10:
        return "medium"
    return "low"


def _significance_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(value, 0)


def _bucket_for_pair(
    pair: FilePair,
    semantic_status: ReviewSemanticStatus,
    settings: Settings,
) -> ReviewBucketKind:
    if semantic_status == ReviewSemanticStatus.LARGE_FILE:
        return ReviewBucketKind.LARGE_FILES
    if pair.match_type == ReviewMatchType.AMBIGUOUS:
        return ReviewBucketKind.AMBIGUOUS
    if pair.match_type == ReviewMatchType.LOW_CONFIDENCE:
        return ReviewBucketKind.LOW_CONFIDENCE
    if pair.diff_type == ReviewDiffType.ADDED:
        return ReviewBucketKind.ADDED
    if pair.diff_type == ReviewDiffType.DELETED:
        return ReviewBucketKind.DELETED
    if pair.language not in settings.review_primary_languages:
        return ReviewBucketKind.OTHER_LANGUAGES
    return ReviewBucketKind.PRIMARY


def _bucket_label(key: str) -> str:
    labels = {
        "added": "Added",
        "deleted": "Deleted",
        "ambiguous": "Ambiguous",
        "low_confidence": "Low Confidence",
        "other_languages": "Other Languages",
        "large_files": "Large Files",
    }
    return labels[key]


def _build_file_tree(entries: list[ReviewFileEntry]) -> list[ReviewFileTreeNode]:
    root: list[ReviewFileTreeNode] = []

    def upsert(nodes: list[ReviewFileTreeNode], parts: list[str], entry: ReviewFileEntry, prefix: str = "") -> None:
        head = parts[0]
        path = f"{prefix}/{head}".strip("/")
        node = next((item for item in nodes if item.path == path), None)
        is_file = len(parts) == 1
        if node is None:
            node = ReviewFileTreeNode(name=head, path=path, is_file=is_file, changed_count=0)
            nodes.append(node)
        if is_file:
            node.file_id = entry.file_id
            node.changed_count = max(1, entry.stats.changed_line_count)
            return
        upsert(node.children, parts[1:], entry, path)
        node.changed_count = sum(child.changed_count for child in node.children)

    for entry in entries:
        file_path = entry.current_path or entry.source_path
        if not file_path:
            continue
        upsert(root, file_path.split("/"), entry)
    return root
