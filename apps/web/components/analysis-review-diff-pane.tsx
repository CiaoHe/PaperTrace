"use client";

import type { ReviewFileEntry, ReviewFilePayload } from "@papertrace/contracts";
import { useMemo } from "react";
import { Diff, type DiffType, type FileData, Hunk, parseDiff } from "react-diff-view";

import { AnalysisMonacoCodeViewer } from "@/components/analysis-monaco-code-viewer";
import { resolveApiUrl } from "@/lib/api";

interface AnalysisReviewDiffPaneProps {
  fileEntry: ReviewFileEntry | null;
  filePayload: ReviewFilePayload | null;
  loading: boolean;
  error: string | null;
  selectedClaimId: string | null;
}

export function AnalysisReviewDiffPane({
  fileEntry,
  filePayload,
  loading,
  error,
  selectedClaimId,
}: AnalysisReviewDiffPaneProps) {
  const diffFiles = useMemo(() => {
    if (!filePayload?.raw_unified_diff) {
      return [];
    }
    try {
      return parseDiff(filePayload.raw_unified_diff);
    } catch {
      return [];
    }
  }, [filePayload?.raw_unified_diff]);

  const linkedHunkCount = useMemo(() => {
    if (!filePayload) {
      return 0;
    }
    if (!selectedClaimId) {
      return filePayload.hunks.length;
    }
    return filePayload.hunks.filter((hunk) => (hunk.linked_claim_ids ?? []).includes(selectedClaimId)).length;
  }, [filePayload, selectedClaimId]);

  if (!fileEntry) {
    return (
      <div className="review-v2-empty-pane">
        <p>Select a file from the review tree to inspect its diff.</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="review-v2-empty-pane">
        <p>Loading diff payload for {(fileEntry.current_path ?? fileEntry.source_path) || "selected file"}.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="review-v2-empty-pane">
        <p>{error}</p>
      </div>
    );
  }

  if (!filePayload) {
    return (
      <div className="review-v2-empty-pane">
        <p>Diff payload is not available for the selected file.</p>
      </div>
    );
  }

  const fileLabel = fileEntry.current_path ?? fileEntry.source_path ?? "unknown file";

  return (
    <div className="review-v2-diff-shell" data-testid="github-diff-pane">
      <div className="review-v2-pane-head">
        <div>
          <small>Split diff review</small>
          <h3>{fileLabel}</h3>
        </div>
        <div className="review-v2-pane-meta">
          <span>{fileEntry.diff_type}</span>
          <span>{fileEntry.match_type}</span>
          <span>{fileEntry.semantic_status}</span>
        </div>
      </div>
      <div className="review-v2-diff-toolbar">
        <span>
          {filePayload.stats.added_lines} additions / {filePayload.stats.removed_lines} deletions
        </span>
        <span>{filePayload.stats.hunk_count} hunks</span>
        <span>
          {linkedHunkCount} linked hunk{linkedHunkCount === 1 ? "" : "s"}
        </span>
      </div>
      {filePayload.fallback_mode === "diff2html_prebuilt" && filePayload.fallback_html_path ? (
        <iframe
          className="review-v2-rendered-frame"
          src={resolveApiUrl(filePayload.fallback_html_path)}
          title={`Rendered diff for ${fileLabel}`}
        />
      ) : filePayload.fallback_mode === "raw_diff_only" || diffFiles.length === 0 ? (
        <AnalysisMonacoCodeViewer
          emptyMessage="No raw diff text is available."
          filePath={fileLabel}
          height="720px"
          rangeLabel="raw unified diff"
          value={filePayload.raw_unified_diff}
        />
      ) : (
        <div className="review-v2-diff-view">
          {diffFiles.map((file) => (
            <Diff
              diffType={normalizeDiffType(file)}
              hunks={file.hunks}
              key={`${file.oldPath}-${file.newPath}`}
              viewType="split"
            >
              {(hunks) => hunks.map((hunk) => <Hunk hunk={hunk} key={hunk.content} />)}
            </Diff>
          ))}
        </div>
      )}
    </div>
  );
}

function normalizeDiffType(file: FileData): DiffType {
  if (file.type === "add" || file.type === "delete" || file.type === "rename" || file.type === "copy") {
    return file.type;
  }
  return "modify";
}
