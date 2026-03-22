"use client";

import type { JobStatusResponse } from "@papertrace/contracts";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

import type { AnalysisReviewState } from "@/lib/api";
import { rebuildAnalysisReview } from "@/lib/api";

interface AnalysisReviewStatusCardProps {
  job: JobStatusResponse;
  reviewState: Exclude<AnalysisReviewState, { kind: "ready" }>;
}

export function AnalysisReviewStatusCard({ job, reviewState }: AnalysisReviewStatusCardProps) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (reviewState.kind !== "building") {
      return;
    }
    const timer = window.setInterval(() => {
      router.refresh();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [reviewState.kind, router]);

  const handleRebuild = (): void => {
    setError(null);
    startTransition(() => {
      void rebuildAnalysisReview(job.id)
        .then(() => {
          router.refresh();
        })
        .catch((nextError) => {
          setError(nextError instanceof Error ? nextError.message : "Failed to re-enqueue review build.");
        });
    });
  };

  return (
    <div className="panel">
      <div className="panel-inner stack">
        <div className="page-head">
          <div>
            <span className="eyebrow">Evidence workspace</span>
            <h2>{reviewState.kind === "building" ? "Review build in progress" : "Review unavailable"}</h2>
            <p className="muted">
              {reviewState.kind === "building"
                ? reviewState.status.detail
                : reviewState.status.detail || reviewState.status.build_error}
            </p>
          </div>
          <div className="page-actions">
            <Link className="button secondary" href="/">
              Back to shell
            </Link>
            <button className="button secondary" onClick={() => router.refresh()} type="button">
              Refresh
            </button>
            {reviewState.kind === "building" ? (
              <button className="button secondary" disabled={isPending} onClick={handleRebuild} type="button">
                Rebuild review
              </button>
            ) : null}
          </div>
        </div>

        <div className="result-band">
          <div className="kpi">
            <small>Job</small>
            <strong>{job.id}</strong>
            <span className="muted">{job.status}</span>
          </div>
          <div className="kpi">
            <small>Paper source</small>
            <strong>{job.paper_source}</strong>
            <span className="muted">{job.repo_url}</span>
          </div>
          {reviewState.kind === "building" ? (
            <div className="kpi">
              <small>Build phase</small>
              <strong>{reviewState.status.build_phase}</strong>
              <span className="muted">
                {Math.round(reviewState.status.build_progress * 100)}% · {reviewState.status.files_done}/
                {reviewState.status.files_total}
              </span>
            </div>
          ) : (
            <div className="kpi">
              <small>Error</small>
              <strong>Review artifact unavailable</strong>
              <span className="muted">{reviewState.status.build_error}</span>
            </div>
          )}
        </div>
        {error ? <div className="warning">{error}</div> : null}
      </div>
    </div>
  );
}
