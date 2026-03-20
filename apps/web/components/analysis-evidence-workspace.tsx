"use client";

import type { AnalysisResult } from "@papertrace/contracts";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AnalysisEvidencePanel } from "@/components/analysis-evidence-panel";
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
  const runtimeMetadata = [
    ["Paper fetch mode", formatEnumLabel(result.metadata.paper_fetch_mode)],
    ["Paper source kind", formatEnumLabel(result.metadata.paper_source_kind)],
    ["Parser mode", formatEnumLabel(result.metadata.parser_mode)],
    ["Repo tracer mode", formatEnumLabel(result.metadata.repo_tracer_mode)],
    ["Diff analyzer mode", formatEnumLabel(result.metadata.diff_analyzer_mode)],
    ["Contribution mapper mode", formatEnumLabel(result.metadata.contribution_mapper_mode)],
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
                Review one linked change at a time as a direct correspondence between upstream code, current repo code,
                and the paper contribution it supports.
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
              <span className="muted">{result.summary}</span>
            </div>
            <div className="kpi">
              <small>Source repo</small>
              <strong>{result.selected_base_repo.repo_url}</strong>
              <span className="muted">
                {result.selected_base_repo.strategy} · confidence {result.selected_base_repo.confidence.toFixed(2)}
              </span>
            </div>
            <div className="kpi">
              <small>Current repo</small>
              <strong>{submittedRepoUrl}</strong>
              <span className="muted">
                {result.mappings.length} mappings · {result.diff_clusters.length} diff clusters ·{" "}
                {result.contributions.length} contributions
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="workbench-card">
        <div className="section-head">
          <div>
            <h4>Mapped change bundles</h4>
            <p className="muted">
              Pick one mapping first. The three-panel review below will always stay aligned to that contribution and
              diff cluster.
            </p>
          </div>
        </div>
        <div className="mapping-lane" data-testid="mapping-lane">
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
            <p className="muted">No contribution mappings are available for this analysis yet.</p>
          )}
        </div>
      </div>

      <AnalysisEvidencePanel
        contribution={activeContribution}
        currentRepoUrl={submittedRepoUrl}
        diffCluster={activeCluster}
        mapping={activeMapping}
        sourceRepoUrl={result.selected_base_repo.repo_url}
      />

      <div className="evidence-context-grid">
        {activeContribution ? (
          <div className="workbench-card">
            <h4>Contribution summary</h4>
            <div className="list">
              <div className="item">
                <h4>{activeContribution.id}</h4>
                <p>{activeContribution.title}</p>
              </div>
              <div className="item">
                <h4>Section</h4>
                <p>{activeContribution.section}</p>
              </div>
            </div>
            {activeContribution.keywords.length > 0 ? (
              <div className="pill-row">
                {activeContribution.keywords.map((keyword) => (
                  <span className="pill" key={keyword}>
                    {keyword}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        {activeCluster ? (
          <div className="workbench-card">
            <h4>Diff cluster summary</h4>
            <div className="list">
              <div className="item">
                <h4>{activeCluster.id}</h4>
                <p>{activeCluster.label}</p>
              </div>
              <div className="item">
                <h4>Change type</h4>
                <p>{formatEnumLabel(activeCluster.change_type)}</p>
              </div>
              <div className="item">
                <h4>Summary</h4>
                <p>{activeCluster.summary}</p>
              </div>
            </div>
            {activeCluster.related_cluster_ids?.length ? (
              <div className="pill-row">
                {activeCluster.related_cluster_ids.map((clusterId) => (
                  <button
                    className="pill-button"
                    key={clusterId}
                    onClick={() => setFocus(focusCluster(result, clusterId))}
                    type="button"
                  >
                    {clusterId}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

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
      </div>

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
  );
}
