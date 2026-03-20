"use client";

import type { ContributionMapping, DiffCluster, PaperContribution } from "@papertrace/contracts";

interface AnalysisEvidencePanelProps {
  contribution: PaperContribution | null;
  diffCluster: DiffCluster | null;
  mapping: ContributionMapping | null;
}

function buildPaperClaims(contribution: PaperContribution | null): string[] {
  if (!contribution) {
    return [];
  }
  return [contribution.problem_solved, contribution.baseline_difference, ...(contribution.impl_hints ?? [])].filter(
    (value): value is string => Boolean(value),
  );
}

function buildCodeAnchors(diffCluster: DiffCluster | null, mapping: ContributionMapping | null): string[] {
  if (!diffCluster) {
    return [];
  }
  const orderedFiles = mapping?.reading_order?.length ? mapping.reading_order : diffCluster.files;
  return orderedFiles.map((file, index) => `${index + 1}. ${file}`);
}

function buildReviewChecklist(
  contribution: PaperContribution | null,
  diffCluster: DiffCluster | null,
  mapping: ContributionMapping | null,
): string[] {
  if (!mapping || !contribution || !diffCluster) {
    return [];
  }

  const clusterTags = diffCluster.semantic_tags ?? [];
  return [
    `Open ${mapping.learning_entry_point ?? diffCluster.files[0] ?? "the first changed file"} first and verify the main implementation hook.`,
    `Check whether ${contribution.title.toLowerCase()} is reflected in ${diffCluster.change_type.toLowerCase()} changes.`,
    `Validate that semantic tags ${clusterTags.join(", ") || "from the cluster"} align with the paper claim.`,
    ...(mapping.missing_aspects ?? []).map((item) => `Manual check: ${item}`),
  ];
}

export function AnalysisEvidencePanel({ contribution, diffCluster, mapping }: AnalysisEvidencePanelProps) {
  const paperClaims = buildPaperClaims(contribution);
  const codeAnchors = buildCodeAnchors(diffCluster, mapping);
  const reviewChecklist = buildReviewChecklist(contribution, diffCluster, mapping);
  const referenceBadges = contribution?.evidence_refs ?? [];
  const semanticTags = diffCluster?.semantic_tags ?? [];

  return (
    <div className="workbench-card">
      <div className="section-head">
        <div>
          <h4>Annotation panel</h4>
          <p className="muted">
            This is the reviewer-facing bridge between paper claims, mapping evidence, and the code areas worth reading
            first.
          </p>
        </div>
      </div>

      <div className="annotation-grid">
        <div className="annotation-card">
          <small>Paper claim</small>
          <h5>{contribution ? `${contribution.id} · ${contribution.title}` : "No contribution selected"}</h5>
          {paperClaims.length > 0 ? (
            <div className="list">
              {paperClaims.map((claim) => (
                <div className="item" key={claim}>
                  <p>{claim}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">Select a mapped contribution to inspect its paper-side claims.</p>
          )}
          {referenceBadges.length > 0 ? (
            <div className="pill-row">
              {referenceBadges.map((reference) => (
                <span className="pill" key={reference}>
                  {reference}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="annotation-card">
          <small>Code anchors</small>
          <h5>{diffCluster ? `${diffCluster.id} · ${diffCluster.label}` : "No diff cluster selected"}</h5>
          {diffCluster ? (
            <>
              <p>{diffCluster.summary}</p>
              {semanticTags.length > 0 ? (
                <div className="pill-row">
                  {semanticTags.map((tag) => (
                    <span className="pill" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="code-anchor-list">
                {codeAnchors.map((anchor) => (
                  <code className="code-anchor" key={anchor}>
                    {anchor}
                  </code>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Choose a diff cluster to expose the current code reading path.</p>
          )}
        </div>

        <div className="annotation-card">
          <small>Review protocol</small>
          <h5>{mapping ? `${mapping.diff_cluster_id} → ${mapping.contribution_id}` : "No mapping selected"}</h5>
          {mapping ? (
            <>
              <p>{mapping.evidence}</p>
              <div className="checklist">
                {reviewChecklist.map((item) => (
                  <div className="checklist-item" key={item}>
                    <span />
                    <p>{item}</p>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="muted">Select a mapping to generate a reviewer checklist.</p>
          )}
        </div>
      </div>
    </div>
  );
}
