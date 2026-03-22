from __future__ import annotations

import subprocess
from pathlib import Path

from unidiff import PatchSet

from papertrace_core.diff_review.common import normalize_changed_line, stable_digest
from papertrace_core.diff_review.models import (
    ReviewDiffType,
    ReviewFallbackMode,
    ReviewHunk,
    ReviewMatchType,
    ReviewSemanticStatus,
    ReviewStats,
    StoredReviewFilePayload,
)


def synthesize_added_diff(current_path: str, current_text: str) -> str:
    lines = current_text.splitlines()
    header = f"--- /dev/null\n+++ b/{current_path}\n@@ -0,0 +1,{len(lines)} @@\n"
    body = "\n".join(f"+{line}" for line in lines)
    return f"{header}{body}\n"


def synthesize_deleted_diff(source_path: str, source_text: str) -> str:
    lines = source_text.splitlines()
    header = f"--- a/{source_path}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n"
    body = "\n".join(f"-{line}" for line in lines)
    return f"{header}{body}\n"


def generate_raw_unified_diff(
    source_root: Path,
    current_root: Path,
    *,
    source_path: str | None,
    current_path: str | None,
    context_lines: int,
) -> str:
    if source_path is None and current_path is not None:
        current_text = (current_root / current_path).read_text(encoding="utf-8", errors="ignore")
        return synthesize_added_diff(current_path, current_text)
    if current_path is None and source_path is not None:
        source_text = (source_root / source_path).read_text(encoding="utf-8", errors="ignore")
        return synthesize_deleted_diff(source_path, source_text)
    if source_path is None or current_path is None:
        raise ValueError("source_path and current_path cannot both be null")

    command = [
        "git",
        "diff",
        "--no-index",
        f"-U{context_lines}",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        str(source_root / source_path),
        str(current_root / current_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode not in {0, 1}:
        raise RuntimeError(completed.stderr.strip() or f"git diff failed for {source_path} -> {current_path}")
    return completed.stdout


def build_file_payload(
    *,
    file_id: str,
    source_path: str | None,
    current_path: str | None,
    diff_type: ReviewDiffType,
    match_type: ReviewMatchType,
    raw_unified_diff: str,
    semantic_status: ReviewSemanticStatus,
    fallback_mode: ReviewFallbackMode,
    fallback_html_path: str | None,
    linked_claim_ids: list[str],
    linked_contribution_keys: list[str],
) -> StoredReviewFilePayload:
    patch_set = PatchSet(raw_unified_diff)
    added_lines = 0
    removed_lines = 0
    hunks: list[ReviewHunk] = []
    changed_line_count = 0
    for patched_file in patch_set:
        for hunk in patched_file:
            normalized_lines = [
                normalize_changed_line(line.value)
                for line in hunk
                if line.is_added or line.is_removed
            ]
            content_hash = stable_digest(normalized_lines, length=24)
            added_count = sum(1 for line in hunk if line.is_added)
            removed_count = sum(1 for line in hunk if line.is_removed)
            added_lines += added_count
            removed_lines += removed_count
            changed_line_count += added_count + removed_count
            hunks.append(
                ReviewHunk(
                    hunk_id=stable_digest(
                        {
                            "file_id": file_id,
                            "old_start": hunk.source_start,
                            "old_len": hunk.source_length,
                            "new_start": hunk.target_start,
                            "new_len": hunk.target_length,
                            "content_hash": content_hash,
                        }
                    ),
                    old_start=hunk.source_start,
                    old_length=hunk.source_length,
                    new_start=hunk.target_start,
                    new_length=hunk.target_length,
                    added_count=added_count,
                    removed_count=removed_count,
                    semantic_kind=None,
                    linked_claim_ids=linked_claim_ids,
                    linked_contribution_keys=linked_contribution_keys,
                )
            )
    return StoredReviewFilePayload(
        file_id=file_id,
        source_path=source_path,
        current_path=current_path,
        diff_type=diff_type,
        match_type=match_type,
        semantic_status=semantic_status,
        stats=ReviewStats(
            added_lines=added_lines,
            removed_lines=removed_lines,
            changed_line_count=changed_line_count,
            hunk_count=len(hunks),
        ),
        hunks=hunks,
        linked_claim_ids=linked_claim_ids,
        linked_cluster_ids=[],
        fallback_mode=fallback_mode,
        fallback_html_path=fallback_html_path,
    )


def build_raw_diff_only_payload(
    *,
    file_id: str,
    source_path: str | None,
    current_path: str | None,
    diff_type: ReviewDiffType,
    match_type: ReviewMatchType,
    raw_unified_diff: str,
    semantic_status: ReviewSemanticStatus,
    linked_claim_ids: list[str],
    linked_contribution_keys: list[str],
) -> StoredReviewFilePayload:
    added_lines = 0
    removed_lines = 0
    for line in raw_unified_diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("+"):
            added_lines += 1
        elif line.startswith("-"):
            removed_lines += 1

    return StoredReviewFilePayload(
        file_id=file_id,
        source_path=source_path,
        current_path=current_path,
        diff_type=diff_type,
        match_type=match_type,
        semantic_status=semantic_status,
        stats=ReviewStats(
            added_lines=added_lines,
            removed_lines=removed_lines,
            changed_line_count=added_lines + removed_lines,
            hunk_count=0,
        ),
        hunks=[],
        linked_claim_ids=linked_claim_ids,
        linked_cluster_ids=[],
        fallback_mode=ReviewFallbackMode.RAW_DIFF_ONLY,
        fallback_html_path=None,
    )
