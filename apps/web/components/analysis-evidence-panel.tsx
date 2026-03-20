"use client";

import type { ContributionMapping, DiffCluster, DiffCodeAnchor, PaperContribution } from "@papertrace/contracts";
import { useEffect, useState } from "react";

import { AnalysisMonacoDiffViewer, type MonacoReviewMode } from "@/components/analysis-monaco-diff-viewer";

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

function sortCodeAnchors(diffCluster: DiffCluster | null, mapping: ContributionMapping | null) {
  if (!diffCluster) {
    return [];
  }
  const anchors = diffCluster.code_anchors ?? [];
  const readingOrder = mapping?.reading_order ?? [];
  const matchedAnchorIds = mapping?.matched_anchor_patch_ids ?? [];
  return [...anchors].sort((left, right) => {
    const leftMatched = matchedAnchorIds.includes(left.patch_id ?? "") ? 0 : 1;
    const rightMatched = matchedAnchorIds.includes(right.patch_id ?? "") ? 0 : 1;
    if (leftMatched !== rightMatched) {
      return leftMatched - rightMatched;
    }
    const leftRank = readingOrder.indexOf(left.file_path);
    const rightRank = readingOrder.indexOf(right.file_path);
    return (leftRank === -1 ? 999 : leftRank) - (rightRank === -1 ? 999 : rightRank);
  });
}

function anchorKey(anchor: DiffCodeAnchor): string {
  return `${anchor.file_path}:${anchor.start_line}:${anchor.end_line}:${anchor.anchor_kind}`;
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
  const codeAnchors = sortCodeAnchors(diffCluster, mapping);
  const reviewChecklist = buildReviewChecklist(contribution, diffCluster, mapping);
  const referenceBadges = contribution?.evidence_refs ?? [];
  const semanticTags = diffCluster?.semantic_tags ?? [];
  const fidelityNotes = mapping?.fidelity_notes ?? [];
  const [selectedAnchorKey, setSelectedAnchorKey] = useState<string | null>(null);
  const [reviewMode, setReviewMode] = useState<MonacoReviewMode>("anchor");
  const firstAnchorKey = codeAnchors[0] ? anchorKey(codeAnchors[0]) : null;

  useEffect(() => {
    setSelectedAnchorKey(firstAnchorKey);
  }, [firstAnchorKey]);

  useEffect(() => {
    if (diffCluster) {
      setReviewMode("anchor");
    }
  }, [diffCluster]);

  const selectedAnchor =
    codeAnchors.find((anchor) => anchorKey(anchor) === selectedAnchorKey) ?? codeAnchors[0] ?? null;

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
              <div className="code-anchor-browser">
                <div className="code-anchor-list">
                  <div className="actions" style={{ marginBottom: 12, position: "relative", zIndex: 1 }}>
                    <button
                      className={`button secondary${reviewMode === "anchor" ? " active" : ""}`}
                      onClick={() => setReviewMode("anchor")}
                      style={{ position: "relative", zIndex: 1 }}
                      type="button"
                    >
                      Focused anchor
                    </button>
                    <button
                      className={`button secondary${reviewMode === "cluster" ? " active" : ""}`}
                      onClick={() => setReviewMode("cluster")}
                      style={{ position: "relative", zIndex: 1 }}
                      type="button"
                    >
                      Full cluster patch
                    </button>
                  </div>
                  {codeAnchors.length > 0 ? (
                    codeAnchors.map((anchor, index) => (
                      <button
                        className={`code-anchor-button${selectedAnchorKey === anchorKey(anchor) ? " active" : ""}`}
                        key={anchorKey(anchor)}
                        onClick={() => setSelectedAnchorKey(anchorKey(anchor))}
                        type="button"
                      >
                        <strong>
                          {index + 1}. {anchor.file_path}:{anchor.start_line}-{anchor.end_line}
                        </strong>
                        <p>{anchor.reason}</p>
                      </button>
                    ))
                  ) : (
                    <p className="muted">No code anchors available for the selected cluster yet.</p>
                  )}
                </div>
                <AnalysisMonacoDiffViewer anchor={selectedAnchor} cluster={diffCluster} mode={reviewMode} />
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
              <div className="pill-row">
                <span className="pill">snippet fidelity {mapping.snippet_fidelity.toFixed(2)}</span>
                <span className="pill">formula fidelity {mapping.formula_fidelity.toFixed(2)}</span>
              </div>
              {fidelityNotes.length > 0 ? (
                <div className="list">
                  {fidelityNotes.map((note: string) => (
                    <div className="item" key={note}>
                      <p>{note}</p>
                    </div>
                  ))}
                </div>
              ) : null}
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
