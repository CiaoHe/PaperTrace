"use client";

import type { AnalysisResult } from "@papertrace/contracts";

interface AnalysisLineageGraphProps {
  result: AnalysisResult;
  submittedRepoUrl: string;
}

function hostnameLabel(url: string): string {
  return url.replace("https://github.com/", "");
}

export function AnalysisLineageGraph({ result, submittedRepoUrl }: AnalysisLineageGraphProps) {
  const selectedRepoUrl = result.selected_base_repo.repo_url;
  const selectedCandidate =
    result.base_repo_candidates.find((candidate) => candidate.repo_url === selectedRepoUrl) ??
    result.selected_base_repo;
  const alternativeCandidates = result.base_repo_candidates.filter(
    (candidate) => candidate.repo_url !== selectedRepoUrl,
  );

  return (
    <div className="workbench-card">
      <div className="section-head">
        <div>
          <h4>Lineage graph</h4>
          <p className="muted">
            Trace the submitted repository to the selected upstream, then inspect lower-confidence ancestry branches.
          </p>
        </div>
      </div>

      <div className="lineage-graph" role="img" aria-label="Repository lineage graph">
        <div className="lineage-node source">
          <small>Submitted repo</small>
          <strong>{hostnameLabel(submittedRepoUrl)}</strong>
          <p>{submittedRepoUrl}</p>
        </div>
        <div className="lineage-edge">
          <span />
          <small>{selectedCandidate.strategy}</small>
        </div>
        <div className="lineage-node selected">
          <small>Selected upstream</small>
          <strong>{hostnameLabel(selectedCandidate.repo_url)}</strong>
          <p>
            confidence {selectedCandidate.confidence.toFixed(2)} · {selectedCandidate.strategy}
          </p>
        </div>
      </div>

      {alternativeCandidates.length > 0 ? (
        <div className="lineage-branches">
          {alternativeCandidates.map((candidate) => (
            <div className="lineage-branch" key={`${candidate.repo_url}-${candidate.strategy}`}>
              <div className="lineage-branch-node">
                <small>{candidate.strategy}</small>
                <strong>{hostnameLabel(candidate.repo_url)}</strong>
                <p>confidence {candidate.confidence.toFixed(2)}</p>
              </div>
              <p className="muted">{candidate.evidence}</p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
