"use client";

import type { ContributionMapping, DiffCluster, DiffCodeAnchor, PaperContribution } from "@papertrace/contracts";
import { useEffect, useMemo, useState } from "react";

import { AnalysisMonacoDiffViewer } from "@/components/analysis-monaco-diff-viewer";

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

interface FileTreeNode {
  name: string;
  path: string;
  children: FileTreeNode[];
  isFile: boolean;
  anchorCount: number;
  changedCount: number;
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
    if (leftRank !== rightRank) {
      return (leftRank === -1 ? 999 : leftRank) - (rightRank === -1 ? 999 : rightRank);
    }
    return left.start_line - right.start_line;
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
      ? `Start from ${mapping.learning_entry_point} and verify the implementation hook.`
      : `Start from ${diffCluster.files[0] ?? "the selected file"} and verify the implementation hook.`,
    `Check whether the selected diff really implements "${contribution.title}".`,
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

  return diffCluster.files
    .map((path) => ({
      path,
      anchors: grouped.get(path) ?? [],
    }))
    .sort((left, right) => right.anchors.length - left.anchors.length || left.path.localeCompare(right.path));
}

function tokenize(value: string): Set<string> {
  return new Set(
    value
      .toLowerCase()
      .split(/[^a-z0-9_]+/)
      .map((token) => token.trim())
      .filter((token) => token.length >= 3),
  );
}

function buildFileTree(files: ReviewFileEntry[]): FileTreeNode[] {
  const root: FileTreeNode[] = [];

  function upsert(nodes: FileTreeNode[], parts: string[], anchorCount: number, prefix = ""): void {
    const [head, ...tail] = parts;
    if (!head) {
      return;
    }
    const nextPath = prefix ? `${prefix}/${head}` : head;
    const isFile = tail.length === 0;
    let node = nodes.find((entry) => entry.name === head && entry.path === nextPath);
    if (!node) {
      node = {
        name: head,
        path: nextPath,
        children: [],
        isFile,
        anchorCount: isFile ? anchorCount : 0,
        changedCount: isFile ? 1 : 0,
      };
      nodes.push(node);
    }
    if (isFile) {
      node.anchorCount = anchorCount;
      node.changedCount = 1;
      return;
    }
    upsert(node.children, tail, anchorCount, nextPath);
    node.anchorCount = node.children.reduce((sum, child) => sum + child.anchorCount, 0);
    node.changedCount = node.children.reduce((sum, child) => sum + child.changedCount, 0);
  }

  for (const file of files) {
    upsert(root, file.path.split("/"), file.anchors.length);
  }

  function sortNodes(nodes: FileTreeNode[]): FileTreeNode[] {
    return [...nodes]
      .map((node) => ({
        ...node,
        children: sortNodes(node.children),
      }))
      .sort((left, right) => {
        if (left.isFile !== right.isFile) {
          return left.isFile ? 1 : -1;
        }
        return left.name.localeCompare(right.name);
      });
  }

  return sortNodes(root);
}

function formatRange(startLine: number | null | undefined, endLine: number | null | undefined): string {
  if (!startLine || !endLine) {
    return "range unavailable";
  }
  return `${startLine}-${endLine}`;
}

interface FileTreeBranchProps {
  nodes: FileTreeNode[];
  selectedFilePath: string | null;
  expandedPaths: Set<string>;
  onSelect: (path: string) => void;
  onToggle: (path: string) => void;
  depth?: number;
}

function FileTreeBranch({
  nodes,
  selectedFilePath,
  expandedPaths,
  onSelect,
  onToggle,
  depth = 0,
}: FileTreeBranchProps) {
  return (
    <div className="file-tree-branch">
      {nodes.map((node) =>
        node.isFile ? (
          <button
            className={`file-tree-node file${selectedFilePath === node.path ? " active" : ""}`}
            key={node.path}
            onClick={() => onSelect(node.path)}
            style={{ paddingLeft: `${12 + depth * 18}px` }}
            type="button"
          >
            <span>{node.name}</span>
            <small>{node.anchorCount > 0 ? `${node.anchorCount} hunks` : "0 hunks"}</small>
          </button>
        ) : (
          <div className="file-tree-group" key={node.path}>
            <button
              className="file-tree-node dir"
              onClick={() => onToggle(node.path)}
              style={{ paddingLeft: `${12 + depth * 18}px` }}
              type="button"
            >
              <span>{expandedPaths.has(node.path) ? "▾" : "▸"} {node.name}</span>
              <small>{node.changedCount}</small>
            </button>
            {expandedPaths.has(node.path) ? (
              <FileTreeBranch
                depth={depth + 1}
                expandedPaths={expandedPaths}
                nodes={node.children}
                onSelect={onSelect}
                onToggle={onToggle}
                selectedFilePath={selectedFilePath}
              />
            ) : null}
          </div>
        ),
      )}
    </div>
  );
}

function ancestorPaths(filePath: string | null): string[] {
  if (!filePath) {
    return [];
  }
  const parts = filePath.split("/");
  const paths: string[] = [];
  for (let index = 0; index < parts.length - 1; index += 1) {
    paths.push(parts.slice(0, index + 1).join("/"));
  }
  return paths;
}

function selectBestAnchorForClaim(claim: string, anchors: DiffCodeAnchor[]): string | null {
  const claimTokens = tokenize(claim);
  let bestScore = -1;
  let bestKey: string | null = null;
  for (const anchor of anchors) {
    const haystack = `${anchor.file_path}\n${anchor.reason}\n${anchor.snippet}\n${anchor.original_snippet ?? ""}`;
    const anchorTokens = tokenize(haystack);
    const overlap = [...claimTokens].filter((token) => anchorTokens.has(token)).length;
    if (overlap > bestScore) {
      bestScore = overlap;
      bestKey = anchorKey(anchor);
    }
  }
  return bestKey;
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
  const reviewFiles = buildReviewFiles(diffCluster, codeAnchors);
  const fileTree = useMemo(() => buildFileTree(reviewFiles), [reviewFiles]);
  const reviewChecklist = buildReviewChecklist(contribution, diffCluster, mapping);
  const fidelityNotes = mapping?.fidelity_notes ?? [];
  const referenceBadges = contribution?.evidence_refs ?? [];
  const semanticTags = diffCluster?.semantic_tags ?? [];
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(reviewFiles[0]?.path ?? null);
  const [selectedAnchorKey, setSelectedAnchorKey] = useState<string | null>(
    codeAnchors[0] ? anchorKey(codeAnchors[0]) : null,
  );
  const [selectedClaim, setSelectedClaim] = useState<string | null>(paperClaims[0] ?? null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(ancestorPaths(reviewFiles[0]?.path ?? null)));

  useEffect(() => {
    const nextFile = reviewFiles[0]?.path ?? null;
    setSelectedFilePath(nextFile);
    setSelectedAnchorKey(codeAnchors[0] ? anchorKey(codeAnchors[0]) : null);
    setSelectedClaim(paperClaims[0] ?? null);
    setExpandedPaths(new Set(ancestorPaths(nextFile)));
  }, [reviewFiles, codeAnchors]);

  const selectedFile = reviewFiles.find((file) => file.path === selectedFilePath) ?? reviewFiles[0] ?? null;
  const selectedFileAnchors = selectedFile?.anchors ?? [];
  const selectedAnchor =
    selectedFileAnchors.find((anchor) => anchorKey(anchor) === selectedAnchorKey) ??
    selectedFileAnchors[0] ??
    codeAnchors[0] ??
    null;
  const selectedAnchorMatched = Boolean(
    selectedAnchor?.patch_id && mapping?.matched_anchor_patch_ids?.includes(selectedAnchor.patch_id),
  );

  useEffect(() => {
    if (!selectedFile) {
      return;
    }
    if (!selectedFileAnchors.some((anchor) => anchorKey(anchor) === selectedAnchorKey)) {
      setSelectedAnchorKey(selectedFileAnchors[0] ? anchorKey(selectedFileAnchors[0]) : null);
    }
  }, [selectedAnchorKey, selectedFile, selectedFileAnchors]);

  function handleToggle(path: string) {
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  function handleSelectFile(path: string) {
    setSelectedFilePath(path);
    for (const parentPath of ancestorPaths(path)) {
      setExpandedPaths((current) => new Set([...current, parentPath]));
    }
  }

  function handleSelectClaim(claim: string) {
    setSelectedClaim(claim);
    const nextAnchorKey = selectBestAnchorForClaim(claim, codeAnchors);
    if (!nextAnchorKey) {
      return;
    }
    const nextAnchor = codeAnchors.find((anchor) => anchorKey(anchor) === nextAnchorKey) ?? null;
    if (!nextAnchor) {
      return;
    }
    setSelectedFilePath(nextAnchor.file_path);
    setSelectedAnchorKey(nextAnchorKey);
    setExpandedPaths((current) => new Set([...current, ...ancestorPaths(nextAnchor.file_path)]));
  }

  return (
    <div className="workbench-card evidence-review-stage">
      <div className="section-head">
        <div>
          <h4>Linked change review</h4>
          <p className="muted">
            Left is the changed file tree, middle is the actual code diff, and right explains how the selected code
            block maps back to the paper.
          </p>
        </div>
      </div>

      <div className="github-review-grid" data-testid="github-review-grid">
        <aside className="github-filetree-pane" data-testid="github-filetree-pane">
          <div className="review-pane-head">
            <small>Files changed</small>
            <h4>{diffCluster ? `${diffCluster.id} · ${diffCluster.label}` : "No diff cluster selected"}</h4>
            <p className="muted">
              {reviewFiles.length} files · {codeAnchors.length} extracted code anchors
            </p>
          </div>
          <div className="github-filetree-shell">
            {fileTree.length > 0 ? (
              <FileTreeBranch
                expandedPaths={expandedPaths}
                nodes={fileTree}
                onSelect={handleSelectFile}
                onToggle={handleToggle}
                selectedFilePath={selectedFilePath}
              />
            ) : (
              <p className="muted">No file-level evidence is available for this cluster.</p>
            )}
          </div>
        </aside>

        <section className="github-diff-pane" data-testid="github-diff-pane">
          <div className="review-pane-head">
            <small>Code review</small>
            <h4>{selectedFile?.path ?? "No file selected"}</h4>
            <p className="muted">
              {selectedFile
                ? `${sourceRepoUrl} -> ${currentRepoUrl}`
                : "Select a file with extracted evidence to review the diff."}
            </p>
          </div>

          {selectedFileAnchors.length > 0 ? (
            <div className="github-diff-stack">
              {selectedFileAnchors.map((anchor) => {
                const isActive = selectedAnchor ? anchorKey(anchor) === anchorKey(selectedAnchor) : false;
                return (
                  <button
                    className={`github-diff-block${isActive ? " active" : ""}`}
                    key={anchorKey(anchor)}
                    onClick={() => setSelectedAnchorKey(anchorKey(anchor))}
                    type="button"
                  >
                    <div className="github-diff-head">
                      <div>
                        <strong>{anchor.file_path}</strong>
                        <p className="muted">
                          original {formatRange(anchor.original_start_line, anchor.original_end_line)} {"->"} current{" "}
                          {anchor.start_line}-{anchor.end_line}
                        </p>
                      </div>
                      <span className="pill">{anchor.anchor_kind}</span>
                    </div>
                    <div className="github-hunk-meta">
                      <code>
                        @@ -{anchor.original_start_line ?? 0},{Math.max(
                          (anchor.original_end_line ?? anchor.original_start_line ?? 0) -
                            (anchor.original_start_line ?? 0) +
                            1,
                          0,
                        )} +{anchor.start_line},{Math.max(anchor.end_line - anchor.start_line + 1, 0)} @@
                      </code>
                      <span>{anchor.reason}</span>
                    </div>
                    <AnalysisMonacoDiffViewer
                      anchor={anchor}
                      className="review-mode"
                      cluster={diffCluster}
                      height="320px"
                      mode="anchor"
                    />
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="review-editor-shell empty">
              <p className="muted">No code anchors were extracted for this file yet.</p>
            </div>
          )}
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
                    <button
                      className={`paper-claim-button${selectedClaim === claim ? " active" : ""}`}
                      key={claim}
                      onClick={() => handleSelectClaim(claim)}
                      type="button"
                    >
                      <p>{claim}</p>
                    </button>
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
