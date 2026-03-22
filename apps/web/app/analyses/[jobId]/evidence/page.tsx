import Link from "next/link";

import { AnalysisReviewStatusCard } from "@/components/analysis-review-status-card";
import { AnalysisReviewWorkspace } from "@/components/analysis-review-workspace";
import { getAnalysis, getAnalysisReview } from "@/lib/api";

interface EvidencePageProps {
  params: {
    jobId: string;
  };
}

export default async function EvidencePage({ params }: EvidencePageProps) {
  try {
    const [job, reviewState] = await Promise.all([getAnalysis(params.jobId), getAnalysisReview(params.jobId)]);

    return (
      <main className="shell shell-wide evidence-shell">
        {reviewState.kind === "ready" ? (
          <AnalysisReviewWorkspace jobId={params.jobId} review={reviewState.review} />
        ) : (
          <AnalysisReviewStatusCard job={job} reviewState={reviewState} />
        )}
      </main>
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to load evidence workspace.";

    return (
      <main className="shell shell-wide evidence-shell">
        <div className="panel">
          <div className="panel-inner stack">
            <div className="page-head">
              <div>
                <span className="eyebrow">Evidence workspace</span>
                <h2>Workspace unavailable</h2>
                <p className="muted">{message}</p>
              </div>
              <div className="page-actions">
                <Link className="button secondary" href="/">
                  Back to shell
                </Link>
              </div>
            </div>
          </div>
        </div>
      </main>
    );
  }
}
