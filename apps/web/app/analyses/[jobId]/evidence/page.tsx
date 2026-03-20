import Link from "next/link";

import { AnalysisEvidenceWorkspace } from "@/components/analysis-evidence-workspace";
import { getAnalysis, getAnalysisResult } from "@/lib/api";

interface EvidencePageProps {
  params: {
    jobId: string;
  };
}

export default async function EvidencePage({ params }: EvidencePageProps) {
  try {
    const [job, result] = await Promise.all([getAnalysis(params.jobId), getAnalysisResult(params.jobId)]);

    return (
      <main className="shell shell-wide evidence-shell">
        <AnalysisEvidenceWorkspace jobId={params.jobId} result={result} submittedRepoUrl={job.repo_url} />
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
