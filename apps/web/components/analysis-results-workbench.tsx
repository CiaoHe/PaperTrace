"use client";

import type { AnalysisResult, ContributionMapping, DiffCluster, PaperContribution } from "@papertrace/contracts";
import { useEffect, useState } from "react";

import { AnalysisEvidencePanel } from "@/components/analysis-evidence-panel";
import { AnalysisLineageGraph } from "@/components/analysis-lineage-graph";

interface WorkbenchFocus {
  mappingKey: string | null;
  contributionId: string | null;
  clusterId: string | null;
}

interface AnalysisResultsWorkbenchProps {
  result: AnalysisResult;
  submittedRepoUrl: string;
}

function formatEnumLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function mappingKey(mapping: ContributionMapping): string {
  return `${mapping.diff_cluster_id}:${mapping.contribution_id}`;
}

function buildCoverageBuckets(result: AnalysisResult): Record<string, number> {
  const buckets: Record<string, number> = { FULL: 0, PARTIAL: 0, APPROXIMATED: 0, MISSING: 0 };
  for (const mapping of result.mappings) {
    const coverageType = mapping.coverage_type ?? "PARTIAL";
    buckets[coverageType] = (buckets[coverageType] ?? 0) + 1;
  }
  return buckets;
}

function readOptionalStringArray(
  value: AnalysisResult,
  key: "unmatched_contribution_ids" | "unmatched_diff_cluster_ids",
): string[] {
  if (!(key in value)) {
    return [];
  }
  const nextValue = value[key];
  return Array.isArray(nextValue) ? nextValue : [];
}

function defaultFocus(result: AnalysisResult): WorkbenchFocus {
  const firstMapping = result.mappings[0] ?? null;
  const firstContribution = result.contributions[0] ?? null;
  const firstCluster = result.diff_clusters[0] ?? null;
  return {
    mappingKey: firstMapping ? mappingKey(firstMapping) : null,
    contributionId: firstMapping?.contribution_id ?? firstContribution?.id ?? null,
    clusterId: firstMapping?.diff_cluster_id ?? firstCluster?.id ?? null,
  };
}

function findContribution(result: AnalysisResult, contributionId: string | null): PaperContribution | null {
  if (!contributionId) {
    return null;
  }
  return result.contributions.find((contribution) => contribution.id === contributionId) ?? null;
}

function findCluster(result: AnalysisResult, clusterId: string | null): DiffCluster | null {
  if (!clusterId) {
    return null;
  }
  return result.diff_clusters.find((cluster) => cluster.id === clusterId) ?? null;
}

function findMapping(result: AnalysisResult, selectedMappingKey: string | null): ContributionMapping | null {
  if (!selectedMappingKey) {
    return null;
  }
  return result.mappings.find((mapping) => mappingKey(mapping) === selectedMappingKey) ?? null;
}

function focusContribution(result: AnalysisResult, contributionId: string): WorkbenchFocus {
  const relatedMapping = result.mappings.find((mapping) => mapping.contribution_id === contributionId) ?? null;
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId,
    clusterId: relatedMapping?.diff_cluster_id ?? null,
  };
}

function focusCluster(result: AnalysisResult, clusterId: string): WorkbenchFocus {
  const relatedMapping = result.mappings.find((mapping) => mapping.diff_cluster_id === clusterId) ?? null;
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId: relatedMapping?.contribution_id ?? null,
    clusterId,
  };
}

export function AnalysisResultsWorkbench({ result, submittedRepoUrl }: AnalysisResultsWorkbenchProps) {
  const [focus, setFocus] = useState<WorkbenchFocus>(() => defaultFocus(result));

  useEffect(() => {
    setFocus(defaultFocus(result));
  }, [result]);

  const coverageBuckets = buildCoverageBuckets(result);
  const unmatchedContributionIds = readOptionalStringArray(result, "unmatched_contribution_ids");
  const unmatchedDiffClusterIds = readOptionalStringArray(result, "unmatched_diff_cluster_ids");
  const activeMapping = findMapping(result, focus.mappingKey);
  const activeContribution = findContribution(result, focus.contributionId);
  const activeCluster = findCluster(result, focus.clusterId);
  const activeReadingOrder = activeMapping?.reading_order ?? [];
  const activeMissingAspects = activeMapping?.missing_aspects ?? [];
  const activeEngineeringDivergences = activeMapping?.engineering_divergences ?? [];
  const activeContributionRefs = activeContribution?.evidence_refs ?? [];
  const activeClusterTags = activeCluster?.semantic_tags ?? [];
  const activeClusterRelatedIds = activeCluster?.related_cluster_ids ?? [];

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
        </div>
      </div>

      <div className="panel">
        <div className="panel-inner stack">
          <div className="section-head">
            <div>
              <h3>Evidence review board</h3>
              <p className="muted">
                Review the mapping queue, inspect the linked contribution and diff cluster, and follow the reading path
                before building richer code-level viewers.
              </p>
            </div>
          </div>

          <div className="workbench-grid">
            <aside className="workbench-column">
              <div className="workbench-card">
                <div className="trace-head">
                  <div>
                    <small>Queue</small>
                    <h4>Contribution mappings</h4>
                  </div>
                  <strong>{result.mappings.length}</strong>
                </div>
                <div className="review-lane">
                  {result.mappings.length > 0 ? (
                    result.mappings.map((mapping) => {
                      const isActive = mappingKey(mapping) === focus.mappingKey;
                      const contribution = findContribution(result, mapping.contribution_id);
                      const cluster = findCluster(result, mapping.diff_cluster_id);
                      return (
                        <button
                          className={`trace-card trace-button${isActive ? " active" : ""}`}
                          key={mappingKey(mapping)}
                          onClick={() =>
                            setFocus({
                              mappingKey: mappingKey(mapping),
                              contributionId: mapping.contribution_id,
                              clusterId: mapping.diff_cluster_id,
                            })
                          }
                          type="button"
                        >
                          <div className="trace-head">
                            <div>
                              <small>{mapping.coverage_type}</small>
                              <h4>
                                {cluster?.id ?? mapping.diff_cluster_id} → {contribution?.id ?? mapping.contribution_id}
                              </h4>
                            </div>
                            <strong>{mapping.implementation_coverage.toFixed(2)}</strong>
                          </div>
                          <div className="coverage-meter" aria-hidden="true">
                            <span style={{ width: `${Math.round(mapping.implementation_coverage * 100)}%` }} />
                          </div>
                          <p>{contribution?.title ?? mapping.contribution_id}</p>
                        </button>
                      );
                    })
                  ) : (
                    <p className="muted">No confident mappings yet for this run.</p>
                  )}
                </div>
              </div>

              <div className="workbench-card">
                <h4>Contribution index</h4>
                <div className="signal-list">
                  {result.contributions.map((contribution) => (
                    <button
                      className={`signal-chip${focus.contributionId === contribution.id ? " active" : ""}`}
                      key={contribution.id}
                      onClick={() => setFocus(focusContribution(result, contribution.id))}
                      type="button"
                    >
                      <strong>{contribution.id}</strong>
                      <span>{contribution.title}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="workbench-card">
                <h4>Diff index</h4>
                <div className="signal-list">
                  {result.diff_clusters.map((cluster) => (
                    <button
                      className={`signal-chip${focus.clusterId === cluster.id ? " active" : ""}`}
                      key={cluster.id}
                      onClick={() => setFocus(focusCluster(result, cluster.id))}
                      type="button"
                    >
                      <strong>{cluster.id}</strong>
                      <span>{cluster.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            </aside>

            <div className="workbench-main">
              {activeMapping ? (
                <article className="workbench-card trace-card active">
                  <div className="trace-head">
                    <div>
                      <small>Focused mapping</small>
                      <h4>
                        {activeMapping.diff_cluster_id} → {activeMapping.contribution_id}
                      </h4>
                    </div>
                    <strong>{activeMapping.confidence.toFixed(2)}</strong>
                  </div>
                  <div className="coverage-meter" aria-hidden="true">
                    <span style={{ width: `${Math.round(activeMapping.implementation_coverage * 100)}%` }} />
                  </div>
                  <p>{activeMapping.evidence}</p>
                  <div className="detail-grid">
                    <div className="item">
                      <h4>Coverage</h4>
                      <p>
                        {activeMapping.completeness} · {activeMapping.coverage_type} ·{" "}
                        {activeMapping.implementation_coverage.toFixed(2)}
                      </p>
                    </div>
                    <div className="item">
                      <h4>Learning entry point</h4>
                      <p>{activeMapping.learning_entry_point ?? "No entry point inferred"}</p>
                    </div>
                  </div>
                  {activeReadingOrder.length > 0 ? (
                    <div>
                      <h4>Reading path</h4>
                      <div className="pill-row">
                        {activeReadingOrder.map((file) => (
                          <code className="pill" key={file}>
                            {file}
                          </code>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {activeMissingAspects.length > 0 ? (
                    <div>
                      <h4>Manual review gaps</h4>
                      <div className="list">
                        {activeMissingAspects.map((item) => (
                          <div className="warning" key={item}>
                            {item}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {activeEngineeringDivergences.length > 0 ? (
                    <div>
                      <h4>Engineering divergences</h4>
                      <div className="list">
                        {activeEngineeringDivergences.map((item) => (
                          <div className="warning" key={item}>
                            {item}
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </article>
              ) : null}

              <AnalysisEvidencePanel
                contribution={activeContribution}
                diffCluster={activeCluster}
                mapping={activeMapping}
              />

              {activeContribution ? (
                <div className="workbench-card">
                  <h4>
                    {activeContribution.id} · {activeContribution.title}
                  </h4>
                  <p className="muted">{activeContribution.section}</p>
                  {activeContribution.problem_solved ? <p>problem: {activeContribution.problem_solved}</p> : null}
                  {activeContribution.baseline_difference ? (
                    <p>difference: {activeContribution.baseline_difference}</p>
                  ) : null}
                  {activeContributionRefs.length > 0 ? <p>refs: {activeContributionRefs.join(" · ")}</p> : null}
                  {typeof activeContribution.implementation_complexity === "number" ? (
                    <p>implementation complexity: {activeContribution.implementation_complexity}/5</p>
                  ) : null}
                  <div className="pill-row">
                    {activeContribution.keywords.map((keyword) => (
                      <span className="pill" key={keyword}>
                        {keyword}
                      </span>
                    ))}
                  </div>
                  <div className="list">
                    {activeContribution.impl_hints.map((hint) => (
                      <div className="item" key={hint}>
                        <p>{hint}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {activeCluster ? (
                <div className="workbench-card">
                  <h4>
                    {activeCluster.id} · {activeCluster.label}
                  </h4>
                  <p>
                    {activeCluster.change_type} · {activeCluster.summary}
                  </p>
                  {activeClusterTags.length > 0 ? (
                    <div className="pill-row">
                      {activeClusterTags.map((tag) => (
                        <span className="pill" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {activeClusterRelatedIds.length > 0 ? (
                    <p>related clusters: {activeClusterRelatedIds.join(" · ")}</p>
                  ) : null}
                  <div className="list">
                    {activeCluster.files.map((file) => (
                      <div className="item" key={file}>
                        <code>{file}</code>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <aside className="workbench-column">
              <AnalysisLineageGraph result={result} submittedRepoUrl={submittedRepoUrl} />

              <div className="workbench-card">
                <h4>Lineage candidates</h4>
                <div className="lineage-list">
                  {result.base_repo_candidates.map((candidate) => (
                    <div
                      className={`lineage-card${
                        candidate.repo_url === result.selected_base_repo.repo_url ? " selected" : ""
                      }`}
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
        </div>
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
