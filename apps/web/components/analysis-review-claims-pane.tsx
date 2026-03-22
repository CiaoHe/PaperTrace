"use client";

import type { ReviewClaimIndexEntry, ReviewFileEntry, ReviewFilePayload, ReviewManifest } from "@papertrace/contracts";
import { useMemo } from "react";

interface AnalysisReviewClaimsPaneProps {
  manifest: ReviewManifest;
  fileEntry: ReviewFileEntry | null;
  filePayload: ReviewFilePayload | null;
  selectedClaimId: string | null;
  onSelectClaim: (claimId: string) => void;
}

export function AnalysisReviewClaimsPane({
  manifest,
  fileEntry,
  filePayload,
  selectedClaimId,
  onSelectClaim,
}: AnalysisReviewClaimsPaneProps) {
  const contributionStatus = useMemo(
    () => new Map(manifest.contribution_status.map((entry) => [entry.contribution_key, entry.status])),
    [manifest.contribution_status],
  );
  const linkedClaimIds = useMemo(() => new Set(filePayload?.linked_claim_ids ?? []), [filePayload?.linked_claim_ids]);
  const orderedClaims = useMemo(
    () =>
      [...manifest.claim_index].sort((left, right) => compareClaims(left, right, linkedClaimIds, contributionStatus)),
    [manifest.claim_index, contributionStatus, linkedClaimIds],
  );
  const selectedClaim = orderedClaims.find((claim) => claim.claim_id === selectedClaimId) ?? null;
  const fileLabel = fileEntry?.current_path ?? fileEntry?.source_path ?? "No file selected";

  return (
    <aside className="review-v2-claims-pane review-pane paper-review-pane" data-testid="paper-review-pane">
      <div className="review-v2-pane-head">
        <div>
          <small>Paper claims</small>
          <h3>Sentence-level correspondence</h3>
        </div>
        <div className="review-v2-pane-meta">
          <span>{linkedClaimIds.size} linked</span>
          <span>{manifest.claim_index.length} total</span>
          <span>{manifest.refinement_status}</span>
        </div>
      </div>
      <div className="review-v2-claim-summary">
        <p>
          Current diff focus: <strong>{fileLabel}</strong>
        </p>
        <p>
          {selectedClaim
            ? `Selected ${selectedClaim.claim_label} in ${selectedClaim.section}.`
            : "Select a claim to jump across linked files and inspect the correspondence."}
        </p>
      </div>
      <div className="review-v2-claim-list">
        {orderedClaims.map((claim) => {
          const isActive = claim.claim_id === selectedClaimId;
          const isLinked = linkedClaimIds.has(claim.claim_id);
          const status = claim.status;
          const contributionState = contributionStatus.get(claim.contribution_key) ?? claim.status;
          return (
            <button
              className={`review-v2-claim-card${isActive ? " active" : ""}${isLinked ? " linked" : ""}`}
              key={claim.claim_id}
              onClick={() => onSelectClaim(claim.claim_id)}
              type="button"
            >
              <div className="review-v2-claim-head">
                <strong>{claim.claim_label}</strong>
                <span className={`review-v2-status review-v2-status-${status}`}>{status.replaceAll("_", " ")}</span>
              </div>
              <p>{claim.claim_text}</p>
              <div className="review-v2-claim-meta">
                <span>{claim.section}</span>
                <span>{isLinked ? "linked to current file" : "browse to locate linked file"}</span>
                <span>{`contribution ${contributionState.replaceAll("_", " ")}`}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function compareClaims(
  left: ReviewClaimIndexEntry,
  right: ReviewClaimIndexEntry,
  linkedClaimIds: Set<string>,
  contributionStatus: Map<string, string>,
): number {
  const leftLinked = linkedClaimIds.has(left.claim_id) ? 1 : 0;
  const rightLinked = linkedClaimIds.has(right.claim_id) ? 1 : 0;
  if (leftLinked !== rightLinked) {
    return rightLinked - leftLinked;
  }
  const leftRank = claimStatusRank(contributionStatus.get(left.contribution_key) ?? left.status);
  const rightRank = claimStatusRank(contributionStatus.get(right.contribution_key) ?? right.status);
  if (leftRank !== rightRank) {
    return rightRank - leftRank;
  }
  const leftContributionRank = claimStatusRank(contributionStatus.get(left.contribution_key) ?? left.status);
  const rightContributionRank = claimStatusRank(contributionStatus.get(right.contribution_key) ?? right.status);
  if (leftContributionRank !== rightContributionRank) {
    return rightContributionRank - leftContributionRank;
  }
  return left.claim_label.localeCompare(right.claim_label);
}

function claimStatusRank(value: string): number {
  return (
    {
      mapped: 4,
      partially_mapped: 3,
      refining: 2,
      unmapped: 1,
      skipped_non_primary_language: 0,
    }[value] ?? 0
  );
}
