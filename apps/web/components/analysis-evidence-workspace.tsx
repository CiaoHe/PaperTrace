"use client";

import type { AnalysisResult } from "@papertrace/contracts";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AnalysisEvidencePanel } from "@/components/analysis-evidence-panel";
import { AnalysisLineageGraph } from "@/components/analysis-lineage-graph";
import {
  defaultFocus,
  findCluster,
  findContribution,
  findMapping,
  focusCluster,
  focusContribution,
  formatEnumLabel,
  mappingKey,
  type WorkbenchFocus,
} from "@/lib/analysis-workbench";

interface AnalysisEvidenceWorkspaceProps {
  jobId: string;
  result: AnalysisResult;
  submittedRepoUrl: string;
}

export function AnalysisEvidenceWorkspace({ jobId, result, submittedRepoUrl }: AnalysisEvidenceWorkspaceProps) {
  const [focus, setFocus] = useState<WorkbenchFocus>(() => defaultFocus(result));

  useEffect(() => {
    setFocus(defaultFocus(result));
  }, [result]);

  const activeMapping = findMapping(result, focus.mappingKey);
  const activeContribution = findContribution(result, focus.contributionId);
  const activeCluster = findCluster(result, focus.clusterId);
  const activeReadingOrder = activeMapping?.reading_order ?? [];
  const activeMissingAspects = activeMapping?.missing_aspects ?? [];
  const activeEngineeringDivergences = activeMapping?.engineering_divergences ?? [];
  const activeContributionRefs = activeContribution?.evidence_refs ?? [];
  const activeClusterTags = activeCluster?.semantic_tags ?? [];
  const activeClusterRelatedIds = activeCluster?.related_cluster_ids ?? [];
  const runtimeMetadata = [
    ["Paper fetch mode", formatEnumLabel(result.metadata.paper_fetch_mode)],
    ["Paper source kind", formatEnumLabel(result.metadata.paper_source_kind)],
    ["Parser mode", formatEnumLabel(result.metadata.parser_mode)],
    ["Repo tracer mode", formatEnumLabel(result.metadata.repo_tracer_mode)],
    ["Diff analyzer mode", formatEnumLabel(result.metadata.diff_analyzer_mode)],
    ["Contribution mapper mode", formatEnumLabel(result.metadata.contribution_mapper_mode)],
    ["Selected repo strategy", formatEnumLabel(result.metadata.selected_repo_strategy)],
  ] as const;

  return (
    <div className="evidence-review-shell">
      <div className="panel">
        <div className="panel-inner stack">
          <div className="page-head">
            <div>
              <span className="eyebrow">Evidence workspace</span>
              <h2>Evidence review board</h2>
              <p className="muted">
                Full-screen review mode keeps the mapping queue and provenance rails fixed while the center surface
                stays focused on code-level evidence.
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
              <small>Job</small>
              <strong>{jobId}</strong>
              <span className="muted">Standalone reviewer layout for this analysis result.</span>
            </div>
            <div className="kpi">
              <small>Selected mapping</small>
              <strong>
                {activeMapping ? `${activeMapping.diff_cluster_id} → ${activeMapping.contribution_id}` : "None yet"}
              </strong>
              <span className="muted">
                {activeMapping
                  ? `${activeMapping.coverage_type} · implementation coverage ${activeMapping.implementation_coverage.toFixed(2)}`
                  : "Choose a mapping from the queue to focus the workspace."}
              </span>
            </div>
            <div className="kpi">
              <small>Selected upstream</small>
              <strong>{result.selected_base_repo.repo_url}</strong>
              <span className="muted">
                {result.selected_base_repo.strategy} · confidence {result.selected_base_repo.confidence.toFixed(2)}
              </span>
            </div>
            <div className="kpi">
              <small>Review footprint</small>
              <strong>
                {result.contributions.length} claims · {result.diff_clusters.length} diff clusters
              </strong>
              <span className="muted">Left rail for queue, center for review, right rail for lineage and runtime.</span>
            </div>
          </div>
        </div>
      </div>

      <div className="evidence-review-grid" data-testid="evidence-review-grid">
        <aside className="evidence-review-rail" data-testid="evidence-left-rail">
          <div className="evidence-review-rail-inner">
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
          </div>
        </aside>

        <div className="evidence-review-main" data-testid="evidence-review-main">
          {activeMapping ? (
            <article className="workbench-card trace-card active evidence-focus-card">
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
              <div className="detail-grid evidence-focus-metrics">
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

          <div className="detail-grid evidence-detail-grid">
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
                {activeContribution.keywords.length > 0 ? (
                  <div className="pill-row">
                    {activeContribution.keywords.map((keyword) => (
                      <span className="pill" key={keyword}>
                        {keyword}
                      </span>
                    ))}
                  </div>
                ) : null}
                {activeContribution.impl_hints.length > 0 ? (
                  <div className="list">
                    {activeContribution.impl_hints.map((hint) => (
                      <div className="item" key={hint}>
                        <p>{hint}</p>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            {activeCluster ? (
              <div className="workbench-card">
                <h4>
                  {activeCluster.id} · {activeCluster.label}
                </h4>
                <p>
                  {formatEnumLabel(activeCluster.change_type)} · {activeCluster.summary}
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
        </div>

        <aside className="evidence-review-rail" data-testid="evidence-right-rail">
          <div className="evidence-review-rail-inner">
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
              <h4>Focused lineage context</h4>
              <div className="list">
                <div className="item">
                  <h4>Submitted repo</h4>
                  <p>{submittedRepoUrl}</p>
                </div>
                <div className="item">
                  <h4>Selected base repo</h4>
                  <p>{result.selected_base_repo.repo_url}</p>
                </div>
                {activeCluster ? (
                  <div className="item">
                    <h4>Focused diff cluster</h4>
                    <p>
                      {activeCluster.id} · {formatEnumLabel(activeCluster.change_type)}
                    </p>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="workbench-card">
              <h4>Runtime provenance</h4>
              <div className="list">
                {runtimeMetadata.map(([label, value]) => (
                  <div className="item" key={label}>
                    <h4>{label}</h4>
                    <p>{value}</p>
                  </div>
                ))}
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
          </div>
        </aside>
      </div>
    </div>
  );
}
