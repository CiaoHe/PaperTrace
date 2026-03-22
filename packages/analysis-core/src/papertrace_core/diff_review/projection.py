from __future__ import annotations

from papertrace_core.diff_review.models import (
    ReviewClaimIndexEntry,
    ReviewContributionStatus,
    ReviewContributionStatusEntry,
)
from papertrace_core.diff_review.retrieval import FileClaimLink


def project_contribution_status(
    claim_entries: list[ReviewClaimIndexEntry],
    links: list[FileClaimLink],
) -> tuple[list[ReviewClaimIndexEntry], list[ReviewContributionStatusEntry], dict[str, tuple[list[str], list[str]]]]:
    linked_claim_ids = {link.claim_id for link in links}
    links_by_file: dict[str, tuple[list[str], list[str]]] = {}
    claims_by_contribution: dict[str, list[ReviewClaimIndexEntry]] = {}
    contribution_key_by_id: dict[str, str] = {}

    for claim in claim_entries:
        claims_by_contribution.setdefault(claim.contribution_id, []).append(claim)
        contribution_key_by_id[claim.contribution_id] = claim.contribution_key

    for link in links:
        existing_claim_ids, existing_contribution_keys = links_by_file.get(link.file_path, ([], []))
        links_by_file[link.file_path] = (
            list(dict.fromkeys([*existing_claim_ids, link.claim_id])),
            list(dict.fromkeys([*existing_contribution_keys, link.contribution_key])),
        )

    statuses: dict[str, ReviewContributionStatus] = {}
    for contribution_id, contribution_claims in claims_by_contribution.items():
        total_claims = len(contribution_claims)
        mapped_claims = sum(1 for claim in contribution_claims if claim.claim_id in linked_claim_ids)
        if mapped_claims == 0:
            statuses[contribution_id] = ReviewContributionStatus.UNMAPPED
        elif mapped_claims == total_claims:
            statuses[contribution_id] = ReviewContributionStatus.MAPPED
        else:
            statuses[contribution_id] = ReviewContributionStatus.PARTIALLY_MAPPED

    updated_claim_entries = [
        claim.model_copy(update={"status": statuses.get(claim.contribution_id, ReviewContributionStatus.UNMAPPED)})
        for claim in claim_entries
    ]
    contribution_entries = [
        ReviewContributionStatusEntry(
            contribution_id=contribution_id,
            contribution_key=contribution_key_by_id[contribution_id],
            status=status,
        )
        for contribution_id, status in statuses.items()
    ]
    contribution_entries.sort(key=lambda entry: entry.contribution_id)
    return updated_claim_entries, contribution_entries, links_by_file
