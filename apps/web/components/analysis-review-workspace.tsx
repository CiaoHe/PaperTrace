"use client";

import type { ReviewFilePayload, ReviewManifest } from "@papertrace/contracts";
import Link from "next/link";
import {
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

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

type ReviewResizeSide = "left" | "right";

interface ReviewResizeDragState {
  side: ReviewResizeSide;
  containerLeft: number;
  containerRight: number;
  containerWidth: number;
  leftWidth: number;
  rightWidth: number;
}

const TREE_PANE_MIN_WIDTH = 232;
const TREE_PANE_MAX_WIDTH = 440;
const CLAIMS_PANE_MIN_WIDTH = 300;
const CLAIMS_PANE_MAX_WIDTH = 520;
const DIFF_PANE_MIN_WIDTH = 560;

export function AnalysisReviewWorkspace({ jobId, review }: AnalysisReviewWorkspaceProps) {
  const buckets = useMemo(() => reviewBuckets(review), [review]);
  const initialSelection = useMemo(() => initialReviewSelection(review), [review]);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const dragStateRef = useRef<ReviewResizeDragState | null>(null);
  const [selectedBucketKey, setSelectedBucketKey] = useState<ReviewBucketKey>(initialSelection.bucketKey);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(initialSelection.fileId);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [payloadCache, setPayloadCache] = useState<Record<string, ReviewFilePayload>>({});
  const [loadingFileId, setLoadingFileId] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [treePaneWidth, setTreePaneWidth] = useState(272);
  const [claimsPaneWidth, setClaimsPaneWidth] = useState(336);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const savedTreeWidth = window.localStorage.getItem("papertrace.review.treePaneWidth");
    const savedClaimsWidth = window.localStorage.getItem("papertrace.review.claimsPaneWidth");
    if (savedTreeWidth) {
      const parsedTreeWidth = Number(savedTreeWidth);
      if (Number.isFinite(parsedTreeWidth)) {
        setTreePaneWidth(clamp(parsedTreeWidth, TREE_PANE_MIN_WIDTH, TREE_PANE_MAX_WIDTH));
      }
    }
    if (savedClaimsWidth) {
      const parsedClaimsWidth = Number(savedClaimsWidth);
      if (Number.isFinite(parsedClaimsWidth)) {
        setClaimsPaneWidth(clamp(parsedClaimsWidth, CLAIMS_PANE_MIN_WIDTH, CLAIMS_PANE_MAX_WIDTH));
      }
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem("papertrace.review.treePaneWidth", String(treePaneWidth));
    window.localStorage.setItem("papertrace.review.claimsPaneWidth", String(claimsPaneWidth));
  }, [claimsPaneWidth, treePaneWidth]);

  useEffect(() => {
    const container = workspaceRef.current;
    if (!container || typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      const width = entry ? Math.round(entry.contentRect.width) : container.clientWidth;
      const maxTree = Math.max(
        TREE_PANE_MIN_WIDTH,
        Math.min(TREE_PANE_MAX_WIDTH, width - CLAIMS_PANE_MIN_WIDTH - DIFF_PANE_MIN_WIDTH - 24),
      );
      const maxClaims = Math.max(
        CLAIMS_PANE_MIN_WIDTH,
        Math.min(CLAIMS_PANE_MAX_WIDTH, width - TREE_PANE_MIN_WIDTH - DIFF_PANE_MIN_WIDTH - 24),
      );
      setTreePaneWidth((current) => clamp(current, TREE_PANE_MIN_WIDTH, maxTree));
      setClaimsPaneWidth((current) => clamp(current, CLAIMS_PANE_MIN_WIDTH, maxClaims));
    });
    observer.observe(container);

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const handlePointerMove = (event: PointerEvent): void => {
      const dragState = dragStateRef.current;
      if (!dragState) {
        return;
      }

      if (dragState.side === "left") {
        const maxLeft = Math.min(
          TREE_PANE_MAX_WIDTH,
          dragState.containerWidth - dragState.rightWidth - DIFF_PANE_MIN_WIDTH - 24,
        );
        const nextLeft = clamp(event.clientX - dragState.containerLeft - 6, TREE_PANE_MIN_WIDTH, maxLeft);
        setTreePaneWidth(nextLeft);
        return;
      }

      const maxRight = Math.min(
        CLAIMS_PANE_MAX_WIDTH,
        dragState.containerWidth - dragState.leftWidth - DIFF_PANE_MIN_WIDTH - 24,
      );
      const nextRight = clamp(dragState.containerRight - event.clientX - 6, CLAIMS_PANE_MIN_WIDTH, maxRight);
      setClaimsPaneWidth(nextRight);
    };

    const handlePointerUp = (): void => {
      dragStateRef.current = null;
      setIsResizing(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, []);

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

  const startResize = (side: ReviewResizeSide, event: ReactPointerEvent<HTMLButtonElement>): void => {
    const container = workspaceRef.current;
    if (!container) {
      return;
    }
    const rect = container.getBoundingClientRect();
    dragStateRef.current = {
      side,
      containerLeft: rect.left,
      containerRight: rect.right,
      containerWidth: rect.width,
      leftWidth: treePaneWidth,
      rightWidth: claimsPaneWidth,
    };
    setIsResizing(true);
    event.preventDefault();
  };

  const handleResizeKeyDown = (side: ReviewResizeSide, event: KeyboardEvent<HTMLButtonElement>): void => {
    const step = event.shiftKey ? 32 : 16;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    if (side === "left") {
      setTreePaneWidth((current) =>
        clamp(current + (event.key === "ArrowLeft" ? -step : step), TREE_PANE_MIN_WIDTH, TREE_PANE_MAX_WIDTH),
      );
      return;
    }
    setClaimsPaneWidth((current) =>
      clamp(current + (event.key === "ArrowLeft" ? step : -step), CLAIMS_PANE_MIN_WIDTH, CLAIMS_PANE_MAX_WIDTH),
    );
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

      <div
        className={`github-review-grid review-v2-grid review-v2-grid-resizable${isResizing ? " is-resizing" : ""}`}
        data-testid="github-review-grid"
        ref={workspaceRef}
      >
        <section
          className="review-v2-pane review-v2-tree-pane review-v2-column review-v2-column-tree"
          style={{ flexBasis: `${treePaneWidth}px`, width: `${treePaneWidth}px` }}
        >
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
            bucketKey={selectedBucket.key}
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
        <button
          aria-label="Resize file tree and diff panes"
          className="review-v2-resizer review-v2-resizer-left"
          onKeyDown={(event) => handleResizeKeyDown("left", event)}
          onPointerDown={(event) => startResize("left", event)}
          type="button"
        />

        <section className="review-v2-pane review-v2-main-pane review-v2-column review-v2-column-main">
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
        <button
          aria-label="Resize diff and claims panes"
          className="review-v2-resizer review-v2-resizer-right"
          onKeyDown={(event) => handleResizeKeyDown("right", event)}
          onPointerDown={(event) => startResize("right", event)}
          type="button"
        />

        <div
          className="review-v2-column review-v2-column-claims"
          style={{ flexBasis: `${claimsPaneWidth}px`, width: `${claimsPaneWidth}px` }}
        >
          <AnalysisReviewClaimsPane
            fileEntry={selectedFile}
            filePayload={selectedPayload}
            manifest={review}
            onSelectClaim={handleSelectClaim}
            selectedClaimId={selectedClaimId}
          />
        </div>
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
