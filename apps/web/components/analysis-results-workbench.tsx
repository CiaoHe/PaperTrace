"use client";

import type { AnalysisResult } from "@papertrace/contracts";
import Link from "next/link";

import { AnalysisLineageGraph } from "@/components/analysis-lineage-graph";
import {
  buildCoverageBuckets,
  countComparableAnchors,
  findCluster,
  formatEnumLabel,
  isWeakMapping,
  readOptionalStringArray,
} from "@/lib/analysis-workbench";

interface AnalysisResultsWorkbenchProps {
  jobId: string | null;
  result: AnalysisResult;
  submittedRepoUrl: string;
}

export function AnalysisResultsWorkbench({ jobId, result, submittedRepoUrl }: AnalysisResultsWorkbenchProps) {
  const coverageBuckets = buildCoverageBuckets(result);
  const unmatchedContributionIds = readOptionalStringArray(result, "unmatched_contribution_ids");
  const unmatchedDiffClusterIds = readOptionalStringArray(result, "unmatched_diff_cluster_ids");
  const leadMapping = result.mappings[0] ?? null;
  const reviewableMappingCount = result.mappings.filter(
    (mapping) => countComparableAnchors(findCluster(result, mapping.diff_cluster_id)) > 0,
  ).length;
  const weakMappingCount = result.mappings.filter((mapping) =>
    isWeakMapping(mapping, findCluster(result, mapping.diff_cluster_id)),
  ).length;
  const lineageWarning = result.metadata.fallback_notes.find((note) =>
    note.includes("no comparable hunks during lineage preview"),
  );

  return (
    <div className="stack">
      <div className="panel">
        <div className="panel-inner stack">
          <div className="result-hero">
            <div>
              <small className="eyebrow">Active analysis</small>
              <h3>Analysis summary</h3>
              <p className="muted">{result.summary}</p>
            </div>
            <div className="hero-metrics">
              <div className="hero-metric">
                <small>Contributions</small>
                <strong>{result.contributions.length}</strong>
              </div>
              <div className="hero-metric">
                <small>Diff clusters</small>
                <strong>{result.diff_clusters.length}</strong>
              </div>
              <div className="hero-metric">
                <small>Mappings</small>
                <strong>{result.mappings.length}</strong>
              </div>
            </div>
          </div>

          <div className="result-band">
            <div className="kpi">
              <small>Selected base repo</small>
              <strong>{result.selected_base_repo.repo_url}</strong>
              <span className="muted">
                {result.selected_base_repo.strategy} • confidence {result.selected_base_repo.confidence.toFixed(2)}
              </span>
            </div>
            <div className="evidence-grid">
              {Object.entries(coverageBuckets).map(([label, count]) => (
                <div className="evidence-stat" key={label}>
                  <small>{label}</small>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
          </div>
          {lineageWarning ? <div className="warning">{lineageWarning}</div> : null}
          {reviewableMappingCount === 0 && result.mappings.length > 0 ? (
            <div className="warning">
              Review mode is currently degraded: {weakMappingCount} weak mapping hypothesis
              {weakMappingCount === 1 ? "" : "es"} and 0 source-comparable mapped changes.
            </div>
          ) : null}
        </div>
      </div>

      <div className="panel">
        <div className="panel-inner stack">
          <div className="section-head">
            <div>
              <h3>Review handoff</h3>
              <p className="muted">
                The heavy reviewer flow now lives in a separate workspace so the home result page can stay readable.
              </p>
            </div>
            {jobId ? (
              <Link className="button" href={`/analyses/${jobId}/evidence`}>
                Open evidence workspace
              </Link>
            ) : null}
          </div>

          <div className="workbench-preview-grid">
            <div className="workbench-card">
              <small>Lead mapping</small>
              <h4>
                {leadMapping ? `${leadMapping.diff_cluster_id} → ${leadMapping.contribution_id}` : "No mapping yet"}
              </h4>
              <p>
                {leadMapping
                  ? `${leadMapping.coverage_type} · implementation coverage ${leadMapping.implementation_coverage.toFixed(2)} · ${reviewableMappingCount} reviewable`
                  : "Open the evidence workspace after the first confident mapping lands."}
              </p>
            </div>
            <div className="workbench-card">
              <small>Workspace scope</small>
              <h4>Mapping queue, annotation panel, and full-cluster diff review</h4>
              <p>Use the standalone page to inspect anchors, switch reviewer modes, and keep lineage visible.</p>
            </div>
          </div>
        </div>
      </div>

      <div className="workbench-grid workbench-grid-summary">
        <div className="workbench-main">
          <div className="workbench-card">
            <h4>Contribution inventory</h4>
            <div className="signal-list">
              {result.contributions.map((contribution) => (
                <div className="signal-chip static" key={contribution.id}>
                  <strong>{contribution.id}</strong>
                  <span>{contribution.title}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="workbench-card">
            <h4>Diff inventory</h4>
            <div className="signal-list">
              {result.diff_clusters.map((cluster) => (
                <div className="signal-chip static" key={cluster.id}>
                  <strong>{cluster.id}</strong>
                  <span>{cluster.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <aside className="workbench-column">
          <AnalysisLineageGraph result={result} submittedRepoUrl={submittedRepoUrl} />

          <div className="workbench-card">
            <h4>Lineage candidates</h4>
            <div className="lineage-list">
              {result.base_repo_candidates.map((candidate) => (
                <div
                  className={`lineage-card${candidate.repo_url === result.selected_base_repo.repo_url ? " selected" : ""}`}
                  key={`${candidate.repo_url}-${candidate.strategy}`}
                >
                  <small>{candidate.strategy}</small>
                  <strong>{candidate.repo_url}</strong>
                  <p>confidence {candidate.confidence.toFixed(2)}</p>
                  <p>{candidate.evidence}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="workbench-card">
            <h4>Runtime provenance</h4>
            <div className="list">
              <div className="item">
                <h4>Paper fetch mode</h4>
                <p>{formatEnumLabel(result.metadata.paper_fetch_mode)}</p>
              </div>
              <div className="item">
                <h4>Paper source kind</h4>
                <p>{formatEnumLabel(result.metadata.paper_source_kind)}</p>
              </div>
              <div className="item">
                <h4>Parser mode</h4>
                <p>{formatEnumLabel(result.metadata.parser_mode)}</p>
              </div>
              <div className="item">
                <h4>Repo tracer mode</h4>
                <p>{formatEnumLabel(result.metadata.repo_tracer_mode)}</p>
              </div>
              <div className="item">
                <h4>Diff analyzer mode</h4>
                <p>{formatEnumLabel(result.metadata.diff_analyzer_mode)}</p>
              </div>
              <div className="item">
                <h4>Contribution mapper mode</h4>
                <p>{formatEnumLabel(result.metadata.contribution_mapper_mode)}</p>
              </div>
              <div className="item">
                <h4>Selected repo strategy</h4>
                <p>{formatEnumLabel(result.metadata.selected_repo_strategy)}</p>
              </div>
            </div>
          </div>

          {result.metadata.fallback_notes.length > 0 ? (
            <div className="workbench-card">
              <h4>Runtime fallback notes</h4>
              <div className="list">
                {result.metadata.fallback_notes.map((note) => (
                  <div className="warning" key={note}>
                    {note}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {result.warnings.length > 0 ? (
            <div className="workbench-card">
              <h4>Warnings</h4>
              <div className="list">
                {result.warnings.map((warning) => (
                  <div className="warning" key={warning}>
                    {warning}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </aside>
      </div>

      {unmatchedContributionIds.length > 0 || unmatchedDiffClusterIds.length > 0 ? (
        <div className="panel">
          <div className="panel-inner stack">
            {unmatchedContributionIds.length > 0 ? (
              <div>
                <h3>Unmatched contributions</h3>
                <div className="list">
                  {unmatchedContributionIds.map((contributionId) => (
                    <div className="warning" key={contributionId}>
                      {contributionId}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {unmatchedDiffClusterIds.length > 0 ? (
              <div>
                <h3>Unmatched diff clusters</h3>
                <div className="list">
                  {unmatchedDiffClusterIds.map((clusterId) => (
                    <div className="warning" key={clusterId}>
                      {clusterId}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
