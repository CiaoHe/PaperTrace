from __future__ import annotations

from dataclasses import dataclass

from papertrace_core.diff_review.models import (
    ReviewClaimIndexEntry,
    ReviewContributionStatus,
    ReviewContributionStatusEntry,
    ReviewRefinementStatus,
)
from papertrace_core.diff_review.retrieval import ClaimHunkLink
from papertrace_core.models import AnalysisResult, ContributionMapping, CoverageType


@dataclass(frozen=True)
class HunkProjection:
    linked_claim_ids: list[str]
    linked_contribution_keys: list[str]


@dataclass(frozen=True)
class FileProjection:
    linked_claim_ids: list[str]
    linked_contribution_keys: list[str]
    linked_cluster_ids: list[str]


@dataclass(frozen=True)
class ReviewProjection:
    claim_entries: list[ReviewClaimIndexEntry]
    contribution_status: list[ReviewContributionStatusEntry]
    file_links: dict[str, FileProjection]
    hunk_links: dict[str, HunkProjection]


def project_review_links(
    *,
    claim_entries: list[ReviewClaimIndexEntry],
    links: list[ClaimHunkLink],
    candidate_links_by_claim_id: dict[str, list[ClaimHunkLink]],
    refinement_status: ReviewRefinementStatus,
) -> ReviewProjection:
    linked_claim_ids = {link.claim_id for link in links}
    links_by_file: dict[str, list[ClaimHunkLink]] = {}
    links_by_hunk: dict[str, list[ClaimHunkLink]] = {}
    claims_by_contribution: dict[str, list[ReviewClaimIndexEntry]] = {}
    contribution_key_by_id: dict[str, str] = {}

    for claim in claim_entries:
        claims_by_contribution.setdefault(claim.contribution_id, []).append(claim)
        contribution_key_by_id[claim.contribution_id] = claim.contribution_key

    for link in links:
        links_by_file.setdefault(link.file_id, []).append(link)
        links_by_hunk.setdefault(link.hunk_id, []).append(link)

    updated_claim_entries: list[ReviewClaimIndexEntry] = []
    for claim in claim_entries:
        if claim.claim_id in linked_claim_ids:
            claim_status = ReviewContributionStatus.MAPPED
        elif refinement_status in {
            ReviewRefinementStatus.QUEUED,
            ReviewRefinementStatus.RUNNING,
        } and candidate_links_by_claim_id.get(claim.claim_id):
            claim_status = ReviewContributionStatus.REFINING
        else:
            claim_status = ReviewContributionStatus.UNMAPPED
        updated_claim_entries.append(claim.model_copy(update={"status": claim_status}))

    contribution_entries: list[ReviewContributionStatusEntry] = []
    updated_claims_by_contribution: dict[str, list[ReviewClaimIndexEntry]] = {}
    for claim in updated_claim_entries:
        updated_claims_by_contribution.setdefault(claim.contribution_id, []).append(claim)

    for contribution_id, contribution_claims in updated_claims_by_contribution.items():
        claim_statuses = {claim.status for claim in contribution_claims}
        if claim_statuses == {ReviewContributionStatus.MAPPED}:
            status = ReviewContributionStatus.MAPPED
        elif ReviewContributionStatus.MAPPED in claim_statuses:
            status = ReviewContributionStatus.PARTIALLY_MAPPED
        elif ReviewContributionStatus.REFINING in claim_statuses:
            status = ReviewContributionStatus.REFINING
        else:
            status = ReviewContributionStatus.UNMAPPED
        contribution_entries.append(
            ReviewContributionStatusEntry(
                contribution_id=contribution_id,
                contribution_key=contribution_key_by_id[contribution_id],
                status=status,
            )
        )

    contribution_entries.sort(key=lambda entry: entry.contribution_id)
    return ReviewProjection(
        claim_entries=updated_claim_entries,
        contribution_status=contribution_entries,
        file_links={
            file_id: FileProjection(
                linked_claim_ids=_unique([link.claim_id for link in file_links]),
                linked_contribution_keys=_unique([link.contribution_key for link in file_links]),
                linked_cluster_ids=_unique([cluster_id for link in file_links for cluster_id in link.cluster_ids]),
            )
            for file_id, file_links in links_by_file.items()
        },
        hunk_links={
            hunk_id: HunkProjection(
                linked_claim_ids=_unique([link.claim_id for link in hunk_links]),
                linked_contribution_keys=_unique([link.contribution_key for link in hunk_links]),
            )
            for hunk_id, hunk_links in links_by_hunk.items()
        },
    )


def project_analysis_result_from_review(
    result: AnalysisResult,
    claim_entries: list[ReviewClaimIndexEntry],
    links: list[ClaimHunkLink],
) -> AnalysisResult:
    claims_by_contribution: dict[str, list[ReviewClaimIndexEntry]] = {}
    for claim in claim_entries:
        claims_by_contribution.setdefault(claim.contribution_id, []).append(claim)

    clusters_by_id = {cluster.id: cluster for cluster in result.diff_clusters}
    mapping_groups: dict[tuple[str, str], list[ClaimHunkLink]] = {}
    for link in links:
        for cluster_id in link.cluster_ids:
            if cluster_id in clusters_by_id:
                mapping_groups.setdefault((link.contribution_id, cluster_id), []).append(link)

    mappings: list[ContributionMapping] = []
    for (contribution_id, cluster_id), grouped_links in mapping_groups.items():
        total_claims = len(claims_by_contribution.get(contribution_id, [])) or 1
        mapped_claim_ids = _unique([link.claim_id for link in grouped_links])
        implementation_coverage = len(mapped_claim_ids) / total_claims
        cluster = clusters_by_id[cluster_id]
        coverage_type = CoverageType.FULL if implementation_coverage >= 0.75 else CoverageType.PARTIAL
        confidence = min(0.95, max(link.score for link in grouped_links) + 0.15)
        mappings.append(
            ContributionMapping(
                diff_cluster_id=cluster_id,
                contribution_id=contribution_id,
                confidence=confidence,
                evidence=(
                    f"Review artifact linked {len(mapped_claim_ids)} claim(s) to {cluster.label} "
                    f"across {len(_unique([link.file_path for link in grouped_links]))} file(s)."
                ),
                completeness="complete" if coverage_type == CoverageType.FULL else "partial",
                implementation_coverage=implementation_coverage,
                snippet_fidelity=min(1.0, max(link.score for link in grouped_links)),
                formula_fidelity=0.0,
                coverage_type=coverage_type,
                matched_anchor_patch_ids=_unique(
                    [anchor.patch_id for anchor in cluster.code_anchors if anchor.patch_id]
                ),
                learning_entry_point=cluster.files[0] if cluster.files else None,
                reading_order=list(cluster.files),
            )
        )

    mappings.sort(
        key=lambda mapping: (
            -mapping.implementation_coverage,
            -mapping.confidence,
            mapping.diff_cluster_id,
            mapping.contribution_id,
        )
    )
    matched_contribution_ids = {mapping.contribution_id for mapping in mappings}
    matched_cluster_ids = {mapping.diff_cluster_id for mapping in mappings}
    return result.model_copy(
        update={
            "mappings": mappings,
            "unmatched_contribution_ids": [
                contribution.id
                for contribution in result.contributions
                if contribution.id not in matched_contribution_ids
            ],
            "unmatched_diff_cluster_ids": [
                cluster.id for cluster in result.diff_clusters if cluster.id not in matched_cluster_ids
            ],
        }
    )


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
