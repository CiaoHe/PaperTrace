from __future__ import annotations

import re
from dataclasses import dataclass

from papertrace_core.diff_review.common import normalize_identifier_text, path_tokens
from papertrace_core.diff_review.models import ReviewClaimIndexEntry
from papertrace_core.diff_review.unified_diff import extract_hunk_metadata
from papertrace_core.models import AnalysisResult, DiffCluster, PaperContribution
from papertrace_core.settings import Settings

MARKER_RE = re.compile(r"\b(?:eq|fig|sec|tab)\b", re.IGNORECASE)
TOP_K = 5
MIN_SCORE_THRESHOLD = 0.08
RELATIVE_SCORE_FLOOR = 0.6


@dataclass(frozen=True)
class ReviewCandidateInput:
    file_id: str
    file_path: str
    language: str
    raw_unified_diff: str


@dataclass(frozen=True)
class ReviewLinkCandidateHunk:
    file_id: str
    file_path: str
    hunk_id: str
    old_start: int
    old_length: int
    new_start: int
    new_length: int
    path_token_set: frozenset[str]
    changed_identifier_set: frozenset[str]
    changed_token_set: frozenset[str]
    semantic_tag_set: frozenset[str]
    cluster_ids: tuple[str, ...]
    snippet: str


@dataclass(frozen=True)
class ClaimHunkLink:
    claim_id: str
    contribution_id: str
    contribution_key: str
    file_id: str
    file_path: str
    hunk_id: str
    cluster_ids: tuple[str, ...]
    snippet: str
    score: float


@dataclass(frozen=True)
class RetrievalResolution:
    accepted_links: list[ClaimHunkLink]
    candidates_by_claim_id: dict[str, list[ClaimHunkLink]]


def build_hunk_candidates(
    result: AnalysisResult,
    files: list[ReviewCandidateInput],
    settings: Settings,
) -> list[ReviewLinkCandidateHunk]:
    clusters_by_file = _clusters_by_file(result.diff_clusters)
    candidates: list[ReviewLinkCandidateHunk] = []
    for file in files:
        if file.language not in settings.review_primary_languages:
            continue
        file_clusters = clusters_by_file.get(file.file_path, [])
        try:
            parsed_hunks = extract_hunk_metadata(file.file_id, file.raw_unified_diff)
        except Exception:
            continue
        for hunk in parsed_hunks:
            related_clusters = _clusters_for_hunk(file.file_path, hunk.new_start, hunk.new_length, file_clusters)
            semantic_tags = {
                token
                for cluster in related_clusters
                for tag in cluster.semantic_tags
                for token in normalize_identifier_text(tag).split(" ")
                if token
            }
            candidates.append(
                ReviewLinkCandidateHunk(
                    file_id=file.file_id,
                    file_path=file.file_path,
                    hunk_id=hunk.hunk_id,
                    old_start=hunk.old_start,
                    old_length=hunk.old_length,
                    new_start=hunk.new_start,
                    new_length=hunk.new_length,
                    path_token_set=frozenset(path_tokens(file.file_path)),
                    changed_identifier_set=hunk.changed_identifiers,
                    changed_token_set=hunk.changed_tokens,
                    semantic_tag_set=frozenset(semantic_tags),
                    cluster_ids=tuple(cluster.id for cluster in related_clusters),
                    snippet=hunk.changed_text,
                )
            )
    return candidates


def retrieve_claim_hunk_links(
    *,
    claim_entries: list[ReviewClaimIndexEntry],
    contributions: list[PaperContribution],
    candidate_hunks: list[ReviewLinkCandidateHunk],
) -> RetrievalResolution:
    contributions_by_id = {contribution.id: contribution for contribution in contributions}
    accepted_links: list[ClaimHunkLink] = []
    candidates_by_claim_id: dict[str, list[ClaimHunkLink]] = {}
    for claim in claim_entries:
        contribution = contributions_by_id.get(claim.contribution_id)
        if contribution is None:
            continue
        ranked = sorted(
            (
                ClaimHunkLink(
                    claim_id=claim.claim_id,
                    contribution_id=claim.contribution_id,
                    contribution_key=claim.contribution_key,
                    file_id=candidate.file_id,
                    file_path=candidate.file_path,
                    hunk_id=candidate.hunk_id,
                    cluster_ids=candidate.cluster_ids,
                    snippet=candidate.snippet,
                    score=_score_claim_candidate(claim, contribution, candidate),
                )
                for candidate in candidate_hunks
            ),
            key=lambda item: (item.score, item.file_path, item.hunk_id),
            reverse=True,
        )
        top_candidates = ranked[:TOP_K]
        candidates_by_claim_id[claim.claim_id] = top_candidates
        best_score = top_candidates[0].score if top_candidates else 0.0
        cutoff = max(MIN_SCORE_THRESHOLD, best_score * RELATIVE_SCORE_FLOOR)
        accepted_links.extend([candidate for candidate in top_candidates if candidate.score >= cutoff])
    return RetrievalResolution(
        accepted_links=accepted_links,
        candidates_by_claim_id=candidates_by_claim_id,
    )


def _score_claim_candidate(
    claim: ReviewClaimIndexEntry,
    contribution: PaperContribution,
    candidate: ReviewLinkCandidateHunk,
) -> float:
    claim_tokens = _tokenize_text(claim.claim_text)
    hint_tokens = _tokenize_text(" ".join(contribution.impl_hints))
    keyword_tokens = _tokenize_text(" ".join(contribution.keywords))
    contribution_tokens = _tokenize_text(
        " ".join(
            [
                contribution.title,
                contribution.problem_solved or "",
                contribution.baseline_difference or "",
                contribution.section,
            ]
        )
    )
    candidate_tokens = set(candidate.changed_token_set)
    target_tokens = claim_tokens | keyword_tokens | contribution_tokens
    marker_overlap = 1.0 if _has_marker(claim.claim_text) and _has_marker(candidate.snippet) else 0.0

    path_overlap = _overlap_ratio(candidate.path_token_set, target_tokens)
    symbol_overlap = _overlap_ratio(candidate_tokens, claim_tokens | contribution_tokens)
    semantic_overlap = _overlap_ratio(candidate.semantic_tag_set, keyword_tokens | contribution_tokens)
    hint_overlap = _overlap_ratio(candidate_tokens | set(candidate.path_token_set), hint_tokens)
    identifier_overlap = _overlap_ratio(candidate.changed_identifier_set, target_tokens | hint_tokens)

    return (
        (0.20 * path_overlap)
        + (0.20 * symbol_overlap)
        + (0.20 * semantic_overlap)
        + (0.15 * hint_overlap)
        + (0.20 * identifier_overlap)
        + (0.05 * marker_overlap)
    )


def _clusters_by_file(diff_clusters: list[DiffCluster]) -> dict[str, list[DiffCluster]]:
    clusters: dict[str, list[DiffCluster]] = {}
    for cluster in diff_clusters:
        for file_path in cluster.files:
            clusters.setdefault(file_path, []).append(cluster)
        for anchor in cluster.code_anchors:
            clusters.setdefault(anchor.file_path, []).append(cluster)
    for file_path, items in clusters.items():
        clusters[file_path] = _dedupe_clusters(items)
    return clusters


def _clusters_for_hunk(
    file_path: str,
    hunk_start: int,
    hunk_length: int,
    clusters: list[DiffCluster],
) -> list[DiffCluster]:
    if not clusters:
        return []
    hunk_end = hunk_start + max(hunk_length - 1, 0)
    anchored: list[DiffCluster] = []
    file_level: list[DiffCluster] = []
    for cluster in clusters:
        if file_path in cluster.files:
            file_level.append(cluster)
        for anchor in cluster.code_anchors:
            if anchor.file_path != file_path:
                continue
            anchor_start = anchor.start_line
            anchor_end = anchor.end_line
            if _ranges_overlap(hunk_start, hunk_end, anchor_start, anchor_end):
                anchored.append(cluster)
                break
    if anchored:
        return _dedupe_clusters(anchored)
    return _dedupe_clusters(file_level)


def _dedupe_clusters(clusters: list[DiffCluster]) -> list[DiffCluster]:
    deduped: list[DiffCluster] = []
    seen: set[str] = set()
    for cluster in clusters:
        if cluster.id in seen:
            continue
        seen.add(cluster.id)
        deduped.append(cluster)
    return deduped


def _ranges_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    if left_start <= 0 or right_start <= 0:
        return False
    return left_start <= right_end and right_start <= left_end


def _tokenize_text(value: str) -> set[str]:
    normalized = normalize_identifier_text(value)
    return {token for token in normalized.split(" ") if token}


def _overlap_ratio(left: set[str] | frozenset[str], right: set[str] | frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(set(left) & set(right))
    return intersection / max(len(right), 1)


def _has_marker(value: str) -> bool:
    return bool(MARKER_RE.search(value))
