"use client";

import type { ContributionMapping, DiffCluster, DiffCodeAnchor, PaperContribution } from "@papertrace/contracts";
import { useEffect, useState } from "react";

import { AnalysisMonacoCodeViewer } from "@/components/analysis-monaco-code-viewer";

interface AnalysisEvidencePanelProps {
  contribution: PaperContribution | null;
  diffCluster: DiffCluster | null;
  mapping: ContributionMapping | null;
  sourceRepoUrl: string;
  currentRepoUrl: string;
}

interface ReviewFileEntry {
  path: string;
  anchors: DiffCodeAnchor[];
}

function buildPaperClaims(contribution: PaperContribution | null): string[] {
  if (!contribution) {
    return [];
  }
  return [contribution.problem_solved, contribution.baseline_difference, ...(contribution.impl_hints ?? [])].filter(
    (value): value is string => Boolean(value),
  );
}

function sortCodeAnchors(diffCluster: DiffCluster | null, mapping: ContributionMapping | null): DiffCodeAnchor[] {
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

  return [
    mapping.learning_entry_point
      ? `Start from ${mapping.learning_entry_point} and verify that the selected change is the implementation hook.`
      : `Start from ${diffCluster.files[0] ?? "the selected file"} and verify the implementation hook.`,
    `Check whether the code change actually implements "${contribution.title}".`,
    ...(mapping.missing_aspects ?? []).map((item) => `Missing evidence: ${item}`),
  ];
}

function buildReviewFiles(diffCluster: DiffCluster | null, codeAnchors: DiffCodeAnchor[]): ReviewFileEntry[] {
  if (!diffCluster) {
    return [];
  }

  const grouped = new Map<string, DiffCodeAnchor[]>();
  for (const anchor of codeAnchors) {
    const existing = grouped.get(anchor.file_path) ?? [];
    existing.push(anchor);
    grouped.set(anchor.file_path, existing);
  }

  return diffCluster.files.map((path) => ({
    path,
    anchors: grouped.get(path) ?? [],
  }));
}

function formatRange(startLine: number | null | undefined, endLine: number | null | undefined): string {
  if (!startLine || !endLine) {
    return "range unavailable";
  }
  return `${startLine}-${endLine}`;
}

export function AnalysisEvidencePanel({
  contribution,
  diffCluster,
  mapping,
  sourceRepoUrl,
  currentRepoUrl,
}: AnalysisEvidencePanelProps) {
  const paperClaims = buildPaperClaims(contribution);
  const codeAnchors = sortCodeAnchors(diffCluster, mapping);
  const reviewChecklist = buildReviewChecklist(contribution, diffCluster, mapping);
  const reviewFiles = buildReviewFiles(diffCluster, codeAnchors);
  const fidelityNotes = mapping?.fidelity_notes ?? [];
  const referenceBadges = contribution?.evidence_refs ?? [];
  const semanticTags = diffCluster?.semantic_tags ?? [];
  const [selectedAnchorKey, setSelectedAnchorKey] = useState<string | null>(null);
  const firstAnchorKey = codeAnchors[0] ? anchorKey(codeAnchors[0]) : null;

  useEffect(() => {
    setSelectedAnchorKey(firstAnchorKey);
  }, [firstAnchorKey]);

  const selectedAnchor =
    codeAnchors.find((anchor) => anchorKey(anchor) === selectedAnchorKey) ?? codeAnchors[0] ?? null;
  const selectedFilePath = selectedAnchor?.file_path ?? reviewFiles[0]?.path ?? null;
  const selectedAnchorMatched = Boolean(
    selectedAnchor?.patch_id && mapping?.matched_anchor_patch_ids?.includes(selectedAnchor.patch_id),
  );

  return (
    <div className="workbench-card evidence-review-stage">
      <div className="section-head">
        <div>
          <h4>Linked change review</h4>
          <p className="muted">
            Each selected anchor is reviewed as a clean three-way correspondence: upstream source code, current repo
            implementation, and the paper claim it is supposed to realize.
          </p>
        </div>
      </div>

      <div className="review-anchor-strip">
        {codeAnchors.length > 0 ? (
          codeAnchors.map((anchor, index) => {
            const isActive = selectedAnchorKey === anchorKey(anchor);
            const isMatched = Boolean(anchor.patch_id && mapping?.matched_anchor_patch_ids?.includes(anchor.patch_id));
            return (
              <button
                className={`review-anchor-chip${isActive ? " active" : ""}`}
                key={anchorKey(anchor)}
                onClick={() => setSelectedAnchorKey(anchorKey(anchor))}
                type="button"
              >
                <strong>
                  {index + 1}. {anchor.file_path}
                </strong>
                <span>
                  lines {anchor.start_line}-{anchor.end_line} · {isMatched ? "linked" : "context"}
                </span>
              </button>
            );
          })
        ) : (
          <div className="item">
            <p>No line-level anchors were extracted for this diff cluster yet.</p>
          </div>
        )}
      </div>

      <div className="three-way-review-grid" data-testid="three-way-review-grid">
        <section className="review-pane" data-testid="source-review-pane">
          <div className="review-pane-head">
            <small>Source repo</small>
            <h4>{sourceRepoUrl}</h4>
            <p className="muted">
              {selectedAnchor
                ? `${selectedAnchor.file_path} · original ${formatRange(
                    selectedAnchor.original_start_line,
                    selectedAnchor.original_end_line,
                  )}`
                : "No upstream snippet is attached to this cluster yet."}
            </p>
          </div>
          <div className="review-pane-body">
            <div className="review-file-tree">
              {reviewFiles.map((file) => (
                <button
                  className={`review-file-chip${selectedFilePath === file.path ? " active" : ""}`}
                  key={file.path}
                  onClick={() => setSelectedAnchorKey(file.anchors[0] ? anchorKey(file.anchors[0]) : null)}
                  type="button"
                >
                  <strong>{file.path}</strong>
                  <span>{file.anchors.length} anchor(s)</span>
                </button>
              ))}
            </div>
            <AnalysisMonacoCodeViewer
              emptyMessage="No upstream source snippet is available for this anchor."
              filePath={selectedAnchor?.file_path ?? selectedFilePath}
              height="min(68vh, 900px)"
              rangeLabel={
                selectedAnchor
                  ? `original ${formatRange(selectedAnchor.original_start_line, selectedAnchor.original_end_line)}`
                  : undefined
              }
              value={selectedAnchor?.original_snippet ?? ""}
            />
          </div>
        </section>

        <section className="review-pane" data-testid="current-review-pane">
          <div className="review-pane-head">
            <small>Current repo</small>
            <h4>{currentRepoUrl}</h4>
            <p className="muted">
              {selectedAnchor
                ? `${selectedAnchor.file_path} · current ${selectedAnchor.start_line}-${selectedAnchor.end_line}`
                : "Select an anchor to inspect the current implementation."}
            </p>
          </div>
          <div className="review-pane-body">
            <div className="review-file-tree">
              {reviewFiles.map((file) => (
                <button
                  className={`review-file-chip${selectedFilePath === file.path ? " active" : ""}`}
                  key={file.path}
                  onClick={() => setSelectedAnchorKey(file.anchors[0] ? anchorKey(file.anchors[0]) : null)}
                  type="button"
                >
                  <strong>{file.path}</strong>
                  <span>{file.anchors.length} anchor(s)</span>
                </button>
              ))}
            </div>
            <AnalysisMonacoCodeViewer
              emptyMessage="No current-repo snippet is available for this anchor."
              filePath={selectedAnchor?.file_path ?? selectedFilePath}
              height="min(68vh, 900px)"
              rangeLabel={
                selectedAnchor ? `current ${selectedAnchor.start_line}-${selectedAnchor.end_line}` : undefined
              }
              value={selectedAnchor?.snippet ?? ""}
            />
          </div>
        </section>

        <aside className="review-pane paper-review-pane" data-testid="paper-review-pane">
          <div className="review-pane-head">
            <small>Paper</small>
            <h4>{contribution ? `${contribution.id} · ${contribution.title}` : "No contribution selected"}</h4>
            <p className="muted">
              This pane explains why the selected code change is linked to the paper and what still needs manual
              confirmation.
            </p>
          </div>

          <div className="paper-review-stack">
            <div className="annotation-card">
              <small>Claim</small>
              {paperClaims.length > 0 ? (
                <div className="list">
                  {paperClaims.map((claim) => (
                    <div className="item" key={claim}>
                      <p>{claim}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">No structured claim text is available for this contribution yet.</p>
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
              <small>Why this code maps to the paper</small>
              <p>{mapping?.evidence ?? "No mapping rationale was generated."}</p>
              <div className="pill-row">
                <span className="pill">{mapping?.coverage_type ?? "PARTIAL"}</span>
                <span className="pill">coverage {mapping?.implementation_coverage.toFixed(2) ?? "0.00"}</span>
                <span className="pill">{selectedAnchorMatched ? "directly linked anchor" : "context anchor"}</span>
              </div>
              {selectedAnchor ? (
                <div className="item">
                  <h4>Selected change</h4>
                  <p>{selectedAnchor.reason}</p>
                </div>
              ) : null}
              {fidelityNotes.length > 0 ? (
                <div className="list">
                  {fidelityNotes.map((note) => (
                    <div className="item" key={note}>
                      <p>{note}</p>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="annotation-card">
              <small>Review next</small>
              {mapping?.reading_order?.length ? (
                <div className="pill-row">
                  {mapping.reading_order.map((file) => (
                    <span className="pill" key={file}>
                      {file}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="muted">No reading order was inferred.</p>
              )}
              {semanticTags.length > 0 ? (
                <div className="pill-row">
                  {semanticTags.map((tag) => (
                    <span className="pill" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="checklist">
                {reviewChecklist.length > 0 ? (
                  reviewChecklist.map((item) => (
                    <div className="checklist-item" key={item}>
                      <span />
                      <p>{item}</p>
                    </div>
                  ))
                ) : (
                  <p className="muted">No review checklist is available until a mapping is selected.</p>
                )}
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
