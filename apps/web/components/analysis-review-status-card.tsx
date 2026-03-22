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
  const [refreshCountdown, setRefreshCountdown] = useState(AUTO_REFRESH_SECONDS);

  useEffect(() => {
    if (reviewState.kind !== "building") {
      return;
    }
    setRefreshCountdown(AUTO_REFRESH_SECONDS);
    const timer = window.setInterval(() => {
      setRefreshCountdown(AUTO_REFRESH_SECONDS);
      router.refresh();
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [reviewState.kind, router]);

  useEffect(() => {
    if (reviewState.kind !== "building") {
      return;
    }
    const timer = window.setInterval(() => {
      setRefreshCountdown((current) => (current <= 1 ? AUTO_REFRESH_SECONDS : current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [reviewState.kind]);

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

  if (reviewState.kind === "building") {
    const stage =
      REVIEW_BUILD_STAGES.find((item) => item.value === reviewState.status.build_phase) ?? REVIEW_BUILD_STAGES[0];
    const currentStageIndex = REVIEW_BUILD_STAGES.findIndex((item) => item.value === reviewState.status.build_phase);
    const stageBaseProgress = currentStageIndex >= 0 ? currentStageIndex / REVIEW_BUILD_STAGES.length : 0;
    const intraStageProgress = stageUsesFineProgress(reviewState.status.build_phase)
      ? Math.max(0, Math.min(1, reviewState.status.build_progress))
      : 0;
    const totalProgress = Math.max(
      Math.round((stageBaseProgress + intraStageProgress / REVIEW_BUILD_STAGES.length) * 100),
      currentStageIndex >= 0 ? Math.round(((currentStageIndex + 1) / REVIEW_BUILD_STAGES.length) * 100) : 0,
    );
    const fileProgressText =
      reviewState.status.files_total > 0
        ? `${reviewState.status.files_done}/${reviewState.status.files_total} files`
        : "waiting for repository scan";

    return (
      <div className="review-v2-shell review-v2-building-shell">
        <div className="panel">
          <div className="panel-inner stack">
            <div className="page-head">
              <div>
                <span className="eyebrow">Evidence workspace</span>
                <h2>Review build in progress</h2>
                <p className="muted">{reviewState.status.detail}</p>
              </div>
              <div className="page-actions">
                <Link className="button secondary" href="/">
                  Back to shell
                </Link>
                <button className="button secondary" onClick={() => router.refresh()} type="button">
                  Refresh
                </button>
                <button className="button secondary" disabled={isPending} onClick={handleRebuild} type="button">
                  Rebuild review
                </button>
              </div>
            </div>

            <div className="review-v2-progress-panel">
              <div className="review-v2-progress-head">
                <div>
                  <small>Build progress</small>
                  <strong>{totalProgress}% ready</strong>
                </div>
                <div className="review-v2-progress-meta">
                  <span>Auto refresh in {refreshCountdown}s</span>
                  <span>{fileProgressText}</span>
                  <span>{stage.label}</span>
                </div>
              </div>
              <div aria-hidden="true" className="review-v2-progress-track">
                <span className="review-v2-progress-fill" style={{ width: `${totalProgress}%` }} />
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
              <div className="kpi">
                <small>Current phase</small>
                <strong>{stage.label}</strong>
                <span className="muted">
                  {reviewState.status.current_file
                    ? `Current file: ${reviewState.status.current_file}`
                    : "Preparing review artifacts and file comparisons."}
                </span>
              </div>
            </div>

            <ul className="review-v2-phase-strip" aria-label="Review build stages">
              {REVIEW_BUILD_STAGES.map((item, index) => {
                const state = index < currentStageIndex ? "done" : index === currentStageIndex ? "active" : "pending";
                return (
                  <li className={`review-v2-phase-pill ${state}`} key={item.value}>
                    <strong>{index + 1}</strong>
                    <span>{item.label}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        </div>

        <div className="github-review-grid review-v2-grid review-v2-grid-building" data-testid="github-review-grid">
          <section className="review-v2-pane review-v2-tree-pane">
            <div className="review-v2-pane-head">
              <div>
                <small>File tree</small>
                <h3>Queue warming up</h3>
              </div>
              <div className="review-v2-pane-meta">
                <span>{fileProgressText}</span>
              </div>
            </div>
            <div className="review-v2-skeleton-shell">
              <div className="review-v2-skeleton-line wide" />
              <div className="review-v2-skeleton-line medium indented" />
              <div className="review-v2-skeleton-line medium indented" />
              <div className="review-v2-skeleton-line wide" />
              <div className="review-v2-skeleton-line short indented" />
              <div className="review-v2-skeleton-line medium indented" />
            </div>
          </section>

          <section className="review-v2-pane review-v2-main-pane">
            <div className="review-v2-pane-head">
              <div>
                <small>Diff pane</small>
                <h3>Preparing comparable hunks</h3>
              </div>
              <div className="review-v2-pane-meta">
                <span>{stage.label}</span>
              </div>
            </div>
            <div className="review-v2-skeleton-diff">
              <div className="review-v2-skeleton-toolbar">
                <div className="review-v2-skeleton-chip" />
                <div className="review-v2-skeleton-chip" />
                <div className="review-v2-skeleton-chip" />
              </div>
              <div className="review-v2-skeleton-hunk">
                <div className="review-v2-skeleton-line wide" />
                <div className="review-v2-skeleton-code-grid">
                  <div className="review-v2-skeleton-code-column">
                    <div className="review-v2-skeleton-line medium" />
                    <div className="review-v2-skeleton-line wide" />
                    <div className="review-v2-skeleton-line short" />
                  </div>
                  <div className="review-v2-skeleton-code-column">
                    <div className="review-v2-skeleton-line medium" />
                    <div className="review-v2-skeleton-line wide" />
                    <div className="review-v2-skeleton-line medium" />
                  </div>
                </div>
              </div>
              <div className="review-v2-skeleton-hunk compact">
                <div className="review-v2-skeleton-line medium" />
                <div className="review-v2-skeleton-code-grid">
                  <div className="review-v2-skeleton-code-column">
                    <div className="review-v2-skeleton-line medium" />
                    <div className="review-v2-skeleton-line short" />
                  </div>
                  <div className="review-v2-skeleton-code-column">
                    <div className="review-v2-skeleton-line wide" />
                    <div className="review-v2-skeleton-line medium" />
                  </div>
                </div>
              </div>
            </div>
          </section>

          <aside className="review-v2-pane review-v2-claims-pane">
            <div className="review-v2-pane-head">
              <div>
                <small>Paper claims</small>
                <h3>Linking queue</h3>
              </div>
              <div className="review-v2-pane-meta">
                <span>{reviewState.status.refinement_status}</span>
              </div>
            </div>
            <div className="review-v2-skeleton-shell review-v2-skeleton-claims">
              <div className="review-v2-skeleton-card">
                <div className="review-v2-skeleton-line short" />
                <div className="review-v2-skeleton-line wide" />
                <div className="review-v2-skeleton-line medium" />
              </div>
              <div className="review-v2-skeleton-card">
                <div className="review-v2-skeleton-line short" />
                <div className="review-v2-skeleton-line medium" />
                <div className="review-v2-skeleton-line wide" />
              </div>
              <div className="review-v2-skeleton-card">
                <div className="review-v2-skeleton-line short" />
                <div className="review-v2-skeleton-line wide" />
                <div className="review-v2-skeleton-line short" />
              </div>
            </div>
          </aside>
        </div>

        {error ? <div className="warning">{error}</div> : null}
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="panel-inner stack">
        <div className="page-head">
          <div>
            <span className="eyebrow">Evidence workspace</span>
            <h2>Review unavailable</h2>
            <p className="muted">{reviewState.status.detail || reviewState.status.build_error}</p>
          </div>
          <div className="page-actions">
            <Link className="button secondary" href="/">
              Back to shell
            </Link>
            <button className="button secondary" onClick={() => router.refresh()} type="button">
              Refresh
            </button>
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
          <div className="kpi">
            <small>Error</small>
            <strong>Review artifact unavailable</strong>
            <span className="muted">{reviewState.status.build_error}</span>
          </div>
        </div>
        {error ? <div className="warning">{error}</div> : null}
      </div>
    </div>
  );
}

const AUTO_REFRESH_MS = 4000;
const AUTO_REFRESH_SECONDS = AUTO_REFRESH_MS / 1000;

const REVIEW_BUILD_STAGES = [
  { value: "waiting_for_analysis", label: "Waiting" },
  { value: "file_mapping", label: "File mapping" },
  { value: "diff_generation", label: "Diff generation" },
  { value: "fallback_render", label: "Fallback render" },
  { value: "claim_extraction", label: "Claim extraction" },
  { value: "deterministic_linking", label: "Deterministic linking" },
  { value: "persisting", label: "Persisting" },
  { value: "done", label: "Ready" },
] as const;

function stageUsesFineProgress(value: string): boolean {
  return value === "diff_generation" || value === "fallback_render";
}
