from __future__ import annotations

import re
from dataclasses import dataclass

from papertrace_core.diff_review.common import normalize_identifier_text, path_tokens
from papertrace_core.diff_review.models import ReviewClaimIndexEntry
from papertrace_core.models import AnalysisResult, PaperContribution
from papertrace_core.settings import Settings

IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
MARKER_RE = re.compile(r"\b(?:eq|fig|sec|tab)\b", re.IGNORECASE)
TOP_K = 5
SCORE_THRESHOLD = 0.05


@dataclass(frozen=True)
class ReviewLinkCandidateFile:
    file_path: str
    language: str
    raw_unified_diff: str
    path_token_set: frozenset[str]
    changed_identifier_set: frozenset[str]
    semantic_tag_set: frozenset[str]


@dataclass(frozen=True)
class FileClaimLink:
    claim_id: str
    contribution_id: str
    contribution_key: str
    file_path: str
    score: float


def build_file_candidates(
    result: AnalysisResult,
    files: list[tuple[str, str, str]],
    settings: Settings,
) -> list[ReviewLinkCandidateFile]:
    semantic_tags_by_file = _semantic_tags_by_file(result)
    candidates: list[ReviewLinkCandidateFile] = []
    for file_path, language, raw_unified_diff in files:
        if language not in settings.review_primary_languages:
            continue
        candidates.append(
            ReviewLinkCandidateFile(
                file_path=file_path,
                language=language,
                raw_unified_diff=raw_unified_diff,
                path_token_set=frozenset(path_tokens(file_path)),
                changed_identifier_set=frozenset(_extract_identifiers(raw_unified_diff)),
                semantic_tag_set=frozenset(semantic_tags_by_file.get(file_path, set())),
            )
        )
    return candidates


def retrieve_claim_file_links(
    *,
    claim_entries: list[ReviewClaimIndexEntry],
    contributions: list[PaperContribution],
    candidate_files: list[ReviewLinkCandidateFile],
) -> list[FileClaimLink]:
    contributions_by_id = {contribution.id: contribution for contribution in contributions}
    links: list[FileClaimLink] = []
    for claim in claim_entries:
        contribution = contributions_by_id.get(claim.contribution_id)
        if contribution is None:
            continue
        ranked = sorted(
            (
                (
                    candidate.file_path,
                    _score_claim_candidate(claim, contribution, candidate),
                )
                for candidate in candidate_files
            ),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        accepted = [
            FileClaimLink(
                claim_id=claim.claim_id,
                contribution_id=claim.contribution_id,
                contribution_key=claim.contribution_key,
                file_path=file_path,
                score=score,
            )
            for file_path, score in ranked[:TOP_K]
            if score >= SCORE_THRESHOLD
        ]
        links.extend(accepted)
    return links


def _score_claim_candidate(
    claim: ReviewClaimIndexEntry,
    contribution: PaperContribution,
    candidate: ReviewLinkCandidateFile,
) -> float:
    claim_tokens = _tokenize_text(claim.claim_text)
    hint_tokens = _tokenize_text(" ".join(contribution.impl_hints))
    keyword_tokens = _tokenize_text(" ".join(contribution.keywords))
    contribution_tokens = _tokenize_text(f"{contribution.title} {contribution.problem_solved or ''}")
    marker_overlap = 1.0 if _has_marker(claim.claim_text) and _has_marker(candidate.raw_unified_diff) else 0.0

    path_overlap = _overlap_ratio(candidate.path_token_set, claim_tokens | keyword_tokens | contribution_tokens)
    identifier_overlap = _overlap_ratio(candidate.changed_identifier_set, claim_tokens | contribution_tokens)
    semantic_overlap = _overlap_ratio(candidate.semantic_tag_set, keyword_tokens | contribution_tokens)
    hint_overlap = _overlap_ratio(candidate.changed_identifier_set | candidate.path_token_set, hint_tokens)

    return (
        (0.30 * path_overlap)
        + (0.30 * identifier_overlap)
        + (0.20 * semantic_overlap)
        + (0.15 * hint_overlap)
        + (0.05 * marker_overlap)
    )


def _semantic_tags_by_file(result: AnalysisResult) -> dict[str, set[str]]:
    tags_by_file: dict[str, set[str]] = {}
    for cluster in result.diff_clusters:
        for file_path in cluster.files:
            tags_by_file.setdefault(file_path, set()).update(
                normalize_identifier_text(tag) for tag in cluster.semantic_tags if normalize_identifier_text(tag)
            )
    return tags_by_file


def _extract_identifiers(raw_unified_diff: str) -> set[str]:
    identifiers: set[str] = set()
    for line in raw_unified_diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            identifiers.update(normalize_identifier_text(match) for match in IDENTIFIER_RE.findall(line))
    return {identifier for identifier in identifiers if identifier}


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
