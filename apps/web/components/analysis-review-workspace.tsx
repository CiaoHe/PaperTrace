"use client";

import type { ReviewFilePayload, ReviewManifest } from "@papertrace/contracts";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AnalysisReviewClaimsPane } from "@/components/analysis-review-claims-pane";
import { AnalysisReviewDiffPane } from "@/components/analysis-review-diff-pane";
import { AnalysisReviewFileTree } from "@/components/analysis-review-file-tree";
import {
  claimById,
  findFileById,
  findSelectionForClaim,
  initialReviewSelection,
  type ReviewBucketKey,
  reviewBuckets,
} from "@/lib/analysis-review";
import { getAnalysisReviewFile } from "@/lib/api";

interface AnalysisReviewWorkspaceProps {
  jobId: string;
  review: ReviewManifest;
}

export function AnalysisReviewWorkspace({ jobId, review }: AnalysisReviewWorkspaceProps) {
  const buckets = useMemo(() => reviewBuckets(review), [review]);
  const initialSelection = useMemo(() => initialReviewSelection(review), [review]);
  const [selectedBucketKey, setSelectedBucketKey] = useState<ReviewBucketKey>(initialSelection.bucketKey);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(initialSelection.fileId);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [payloadCache, setPayloadCache] = useState<Record<string, ReviewFilePayload>>({});
  const [loadingFileId, setLoadingFileId] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedBucketKey(initialSelection.bucketKey);
    setSelectedFileId(initialSelection.fileId);
    setSelectedClaimId(null);
  }, [initialSelection]);

  const selectedBucket = buckets.find((bucket) => bucket.key === selectedBucketKey) ?? buckets[0];

  useEffect(() => {
    if (!selectedBucket) {
      return;
    }
    if (selectedFileId && selectedBucket.files.some((file) => file.file_id === selectedFileId)) {
      return;
    }
    setSelectedFileId(selectedBucket.files[0]?.file_id ?? null);
  }, [selectedBucket, selectedFileId]);

  useEffect(() => {
    if (!selectedFileId || payloadCache[selectedFileId]) {
      return;
    }
    let cancelled = false;
    setLoadingFileId(selectedFileId);
    setFileError(null);
    getAnalysisReviewFile(jobId, selectedFileId)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setPayloadCache((current) => ({ ...current, [selectedFileId]: payload }));
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setFileError(error instanceof Error ? error.message : "Failed to load review file.");
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingFileId((current) => (current === selectedFileId ? null : current));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, payloadCache, selectedFileId]);

  const selectedFile = useMemo(() => findFileById(review, selectedFileId), [review, selectedFileId]);
  const selectedPayload = selectedFileId ? (payloadCache[selectedFileId] ?? null) : null;

  useEffect(() => {
    if (!selectedPayload) {
      return;
    }
    if (selectedClaimId && (selectedPayload.linked_claim_ids ?? []).includes(selectedClaimId)) {
      return;
    }
    setSelectedClaimId(selectedPayload.linked_claim_ids?.[0] ?? null);
  }, [selectedClaimId, selectedPayload]);

  const selectedClaim = useMemo(
    () => claimById(review.claim_index, selectedClaimId),
    [review.claim_index, selectedClaimId],
  );

  const handleSelectClaim = (claimId: string): void => {
    setSelectedClaimId(claimId);
    if ((selectedPayload?.linked_claim_ids ?? []).includes(claimId)) {
      return;
    }
    const nextSelection = findSelectionForClaim(review, claimId);
    if (nextSelection) {
      setSelectedBucketKey(nextSelection.bucketKey);
      setSelectedFileId(nextSelection.fileId);
    }
  };

  return (
    <div className="review-v2-shell">
      <div className="panel">
        <div className="panel-inner stack">
          <div className="page-head">
            <div>
              <span className="eyebrow">Evidence workspace</span>
              <h2>Evidence review board</h2>
              <p className="muted">
                Inspect stable cross-repo review artifacts with a GitHub-style split diff, explicit file buckets, and
                sentence-level paper claims.
              </p>
            </div>
            <div className="page-actions">
              <Link className="button secondary" href="/">
                Back to shell
              </Link>
            </div>
          </div>
          <div className="result-band">
            <div className="kpi">
              <small>Source repo</small>
              <strong>{review.source_repo}</strong>
              <span className="muted">{review.source_revision}</span>
            </div>
            <div className="kpi">
              <small>Current repo</small>
              <strong>{review.current_repo}</strong>
              <span className="muted">{review.current_revision}</span>
            </div>
            <div className="kpi">
              <small>Review artifact</small>
              <strong>{review.artifact_version}</strong>
              <span className="muted">
                {review.summary_counts.total_files} files · {review.summary_counts.total_claims} claims
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="review-v2-bucket-bar">
        {buckets.map((bucket) => (
          <button
            className={`review-v2-bucket-tab${bucket.key === selectedBucketKey ? " active" : ""}`}
            key={bucket.key}
            onClick={() => setSelectedBucketKey(bucket.key)}
            type="button"
          >
            <span>{bucket.label}</span>
            <strong>{bucket.count}</strong>
          </button>
        ))}
      </div>

      <div className="github-review-grid review-v2-grid" data-testid="github-review-grid">
        <section className="review-v2-pane review-v2-tree-pane">
          <div className="review-v2-pane-head">
            <div>
              <small>File tree</small>
              <h3>{selectedBucket.label}</h3>
            </div>
            <div className="review-v2-pane-meta">
              <span>{selectedBucket.count} files</span>
            </div>
          </div>
          <AnalysisReviewFileTree
            emptyMessage={selectedBucket.emptyMessage}
            files={selectedBucket.files}
            onSelectFile={setSelectedFileId}
            selectedFileId={selectedFileId}
          />
          {selectedBucket.key === "primary" && selectedBucket.files.length === 0 ? (
            <div className="review-v2-explicit-empty" data-testid="review-empty-state">
              <p>Primary review lane is empty. Browse secondary buckets to inspect added, ambiguous, or large files.</p>
            </div>
          ) : null}
        </section>

        <section className="review-v2-pane review-v2-main-pane">
          <AnalysisReviewDiffPane
            error={fileError}
            fileEntry={selectedFile}
            filePayload={selectedPayload}
            loading={loadingFileId === selectedFileId}
            selectedClaimId={selectedClaimId}
          />
          {selectedClaim ? (
            <div className="review-v2-selected-claim">
              <strong>{selectedClaim.claim_label}</strong>
              <p>{selectedClaim.claim_text}</p>
            </div>
          ) : null}
        </section>

        <AnalysisReviewClaimsPane
          fileEntry={selectedFile}
          filePayload={selectedPayload}
          manifest={review}
          onSelectClaim={handleSelectClaim}
          selectedClaimId={selectedClaimId}
        />
      </div>
    </div>
  );
}
