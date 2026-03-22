"use client";

import type { ReviewFileEntry } from "@papertrace/contracts";
import { useEffect, useMemo, useState } from "react";

import { ancestorPaths, buildReviewFileTree, type ReviewTreeNode } from "@/lib/analysis-review";

interface AnalysisReviewFileTreeProps {
  files: ReviewFileEntry[];
  selectedFileId: string | null;
  onSelectFile: (fileId: string) => void;
  emptyMessage: string;
}

export function AnalysisReviewFileTree({
  files,
  selectedFileId,
  onSelectFile,
  emptyMessage,
}: AnalysisReviewFileTreeProps) {
  const tree = useMemo(() => buildReviewFileTree(files), [files]);
  const selectedEntry = useMemo(
    () => files.find((entry) => entry.file_id === selectedFileId) ?? null,
    [files, selectedFileId],
  );
  const selectedPath = selectedEntry?.current_path ?? selectedEntry?.source_path ?? null;
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => ancestorPaths(selectedPath));

  useEffect(() => {
    setExpandedPaths((current) => {
      const next = new Set(current);
      for (const path of ancestorPaths(selectedPath)) {
        next.add(path);
      }
      return next;
    });
  }, [selectedPath]);

  if (files.length === 0) {
    return (
      <div className="review-v2-empty-pane">
        <p>{emptyMessage}</p>
      </div>
    );
  }

  const togglePath = (path: string): void => {
    setExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  return (
    <div className="review-v2-file-tree" data-testid="github-filetree-pane">
      {tree.map((node) => (
        <TreeNode
          expandedPaths={expandedPaths}
          key={node.path}
          node={node}
          onSelectFile={onSelectFile}
          onTogglePath={togglePath}
          selectedFileId={selectedFileId}
        />
      ))}
    </div>
  );
}

interface TreeNodeProps {
  node: ReviewTreeNode;
  expandedPaths: Set<string>;
  selectedFileId: string | null;
  onSelectFile: (fileId: string) => void;
  onTogglePath: (path: string) => void;
}

function TreeNode({ node, expandedPaths, selectedFileId, onSelectFile, onTogglePath }: TreeNodeProps) {
  if (node.isFile) {
    return (
      <button
        className={`review-v2-tree-node review-v2-tree-file${selectedFileId === node.fileId ? " active" : ""}`}
        onClick={() => node.fileId && onSelectFile(node.fileId)}
        type="button"
      >
        <span>{node.name}</span>
        <strong>{node.changedCount}</strong>
      </button>
    );
  }

  const expanded = expandedPaths.has(node.path);
  return (
    <div className="review-v2-tree-group">
      <button
        aria-expanded={expanded}
        className="review-v2-tree-node review-v2-tree-dir file-tree-node dir"
        onClick={() => onTogglePath(node.path)}
        type="button"
      >
        <span>{expanded ? "▾" : "▸"}</span>
        <span>{node.name}</span>
        <strong>{node.changedCount}</strong>
      </button>
      {expanded ? (
        <div className="review-v2-tree-children">
          {node.children.map((child) => (
            <TreeNode
              expandedPaths={expandedPaths}
              key={child.path}
              node={child}
              onSelectFile={onSelectFile}
              onTogglePath={onTogglePath}
              selectedFileId={selectedFileId}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
