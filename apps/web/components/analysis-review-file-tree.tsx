"use client";

import type { ReviewFileEntry } from "@papertrace/contracts";
import {
  Braces,
  ChevronDown,
  ChevronRight,
  File,
  FileCode2,
  FileJson2,
  FileText,
  Folder,
  FolderOpen,
  type LucideIcon,
} from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { type NodeRendererProps, type RowRendererProps, Tree, type TreeApi } from "react-arborist";

import { ancestorDirectoryNodeIds, buildReviewFileTree, type ReviewTreeNode } from "@/lib/analysis-review";

interface AnalysisReviewFileTreeProps {
  bucketKey: string;
  files: ReviewFileEntry[];
  selectedFileId: string | null;
  onSelectFile: (fileId: string) => void;
  emptyMessage: string;
}

interface ViewportSize {
  width: number;
  height: number;
}

function toOpenState(nodeIds: string[]): Record<string, boolean> {
  return Object.fromEntries(nodeIds.map((id) => [id, true]));
}

export function AnalysisReviewFileTree({
  bucketKey,
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
  const selectedDirectoryIds = useMemo(() => ancestorDirectoryNodeIds(selectedPath), [selectedPath]);
  const allDirectoryIds = useMemo(() => collectDirectoryIds(tree), [tree]);
  const treeRef = useRef<TreeApi<ReviewTreeNode>>();
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [viewportSize, setViewportSize] = useState<ViewportSize>({ width: 0, height: 0 });
  const [treeResetKey, setTreeResetKey] = useState(0);
  const [treeOpenState, setTreeOpenState] = useState<Record<string, boolean>>(() => toOpenState(selectedDirectoryIds));

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) {
      return;
    }

    const syncViewport = (width = element.clientWidth, height = element.clientHeight): void => {
      setViewportSize((current) => {
        if (current.width === width && current.height === height) {
          return current;
        }
        return { width, height };
      });
    };

    syncViewport();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        syncViewport();
        return;
      }
      syncViewport(Math.round(entry.contentRect.width), Math.round(entry.contentRect.height));
    });
    observer.observe(element);

    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setTreeOpenState((current) => {
      const next = { ...current };
      let changed = false;
      for (const directoryId of selectedDirectoryIds) {
        if (!next[directoryId]) {
          next[directoryId] = true;
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [selectedDirectoryIds]);

  useEffect(() => {
    if (!treeRef.current) {
      return;
    }
    for (const directoryId of selectedDirectoryIds) {
      treeRef.current.open(directoryId);
    }
    if (selectedFileId !== null) {
      treeRef.current.openParents(selectedFileId);
    }
    const timeoutId =
      selectedFileId !== null
        ? window.setTimeout(() => {
            treeRef.current?.scrollTo(selectedFileId, "smart");
          }, 0)
        : null;

    return () => {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [selectedDirectoryIds, selectedFileId]);

  if (files.length === 0) {
    return (
      <div className="review-v2-empty-pane">
        <p>{emptyMessage}</p>
      </div>
    );
  }

  const linkedFiles = files.filter((file) => (file.linked_claim_count ?? 0) > 0).length;

  return (
    <div className="review-v2-file-tree" data-testid="github-filetree-pane">
      <div className="review-v2-tree-toolbar">
        <div className="review-v2-tree-toolbar-copy">
          <span>{files.length} review files</span>
          <span>{linkedFiles} linked</span>
        </div>
        <div className="review-v2-tree-toolbar-actions">
          <button
            onClick={() => {
              setTreeOpenState(toOpenState(allDirectoryIds));
              setTreeResetKey((current) => current + 1);
            }}
            type="button"
          >
            Expand all
          </button>
          <button
            onClick={() => {
              setTreeOpenState({});
              setTreeResetKey((current) => current + 1);
            }}
            type="button"
          >
            Collapse all
          </button>
        </div>
      </div>
      <div className="review-v2-tree-viewport" ref={viewportRef}>
        <Tree
          childrenAccessor="children"
          className="review-v2-arborist"
          data={tree}
          disableDrag
          disableEdit
          disableMultiSelection
          height={Math.max(viewportSize.height, 360)}
          idAccessor="id"
          indent={18}
          initialOpenState={treeOpenState}
          key={`${bucketKey}:${files.length}:${treeResetKey}`}
          onSelect={(nodes) => {
            const selectedNode = nodes[0]?.data;
            if (selectedNode?.isFile && selectedNode.fileId) {
              onSelectFile(selectedNode.fileId);
            }
          }}
          openByDefault={false}
          overscanCount={12}
          renderRow={ReviewFileTreeRowContainer}
          rowHeight={34}
          selection={selectedFileId ?? undefined}
          width={viewportSize.width > 0 ? viewportSize.width : "100%"}
        >
          {(props) => <ReviewFileTreeRow {...props} onSelectFile={onSelectFile} selectedFileId={selectedFileId} />}
        </Tree>
      </div>
    </div>
  );
}

interface ReviewFileTreeRowProps extends NodeRendererProps<ReviewTreeNode> {
  onSelectFile: (fileId: string) => void;
  selectedFileId: string | null;
}

function ReviewFileTreeRow({ node, onSelectFile, selectedFileId, style }: ReviewFileTreeRowProps) {
  const icon = node.isInternal ? (node.isOpen ? FolderOpen : Folder) : resolveFileIcon(node.data);
  const toneClass = node.isInternal ? "review-v2-icon-folder" : resolveFileToneClass(node.data);
  const isActive = node.data.isFile && selectedFileId === node.data.fileId;
  const linkedClaimCount = node.data.linkedClaimCount;
  const countLabel = node.data.isFile ? `${node.data.changedCount} changed lines` : `${node.data.fileCount} files`;

  return (
    <div className="review-v2-tree-row" style={style as CSSProperties}>
      <button
        aria-expanded={node.isInternal ? node.isOpen : undefined}
        className={[
          "review-v2-tree-node",
          node.isInternal ? "review-v2-tree-folder" : "review-v2-tree-file",
          isActive ? "active" : "",
        ]
          .filter(Boolean)
          .join(" ")}
        onClick={() => {
          if (node.isInternal) {
            node.toggle();
            return;
          }
          if (node.data.fileId) {
            onSelectFile(node.data.fileId);
          }
        }}
        title={node.data.path}
        type="button"
      >
        <span className="review-v2-tree-node-main">
          <span
            className="review-v2-tree-caret"
            data-state={node.isInternal ? (node.isOpen ? "open" : "closed") : "leaf"}
          >
            {node.isInternal ? (
              node.isOpen ? (
                <ChevronDown size={14} strokeWidth={2} />
              ) : (
                <ChevronRight size={14} strokeWidth={2} />
              )
            ) : null}
          </span>
          <span className={`review-v2-tree-icon ${toneClass}`}>{renderTreeIcon(icon)}</span>
          <span className="review-v2-tree-label">
            <span className="review-v2-tree-name">{node.data.name}</span>
            {linkedClaimCount > 0 ? <span className="review-v2-tree-claim-badge">{linkedClaimCount}</span> : null}
          </span>
        </span>
        <span className="review-v2-tree-meta">
          <span className="review-v2-tree-count" title={countLabel}>
            {node.data.isFile ? node.data.changedCount : node.data.fileCount}
          </span>
        </span>
      </button>
    </div>
  );
}

function ReviewFileTreeRowContainer<T>({ attrs, children, innerRef }: RowRendererProps<T>) {
  const { onClick: _onClick, ...restAttrs } = attrs;

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: react-arborist row containers must stay focusable treeitems.
    <div
      {...restAttrs}
      className={`review-v2-tree-row-shell ${restAttrs.className ?? ""}`.trim()}
      onFocus={(event) => event.stopPropagation()}
      ref={innerRef}
    >
      {children}
    </div>
  );
}

function renderTreeIcon(Icon: LucideIcon) {
  return <Icon size={15} strokeWidth={1.9} />;
}

function resolveFileIcon(node: ReviewTreeNode): LucideIcon {
  const extension = extensionFor(node.name);
  if (extension === "json") {
    return FileJson2;
  }
  if (["md", "txt", "rst"].includes(extension)) {
    return FileText;
  }
  if (["py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(extension)) {
    return FileCode2;
  }
  if (["cu", "cuh", "c", "cc", "cpp", "h", "hpp", "rs", "go", "java"].includes(extension)) {
    return Braces;
  }
  return File;
}

function resolveFileToneClass(node: ReviewTreeNode): string {
  const extension = extensionFor(node.name);
  if (["py", "pyi"].includes(extension)) {
    return "review-v2-icon-python";
  }
  if (["ts", "tsx"].includes(extension)) {
    return "review-v2-icon-typescript";
  }
  if (["js", "jsx", "mjs", "cjs"].includes(extension)) {
    return "review-v2-icon-javascript";
  }
  if (extension === "json") {
    return "review-v2-icon-json";
  }
  if (["md", "txt", "rst"].includes(extension)) {
    return "review-v2-icon-doc";
  }
  if (["cu", "cuh", "c", "cc", "cpp", "h", "hpp", "rs", "go", "java"].includes(extension)) {
    return "review-v2-icon-native";
  }
  return "review-v2-icon-default";
}

function extensionFor(name: string): string {
  const extension = name.split(".").pop();
  return extension ? extension.toLowerCase() : "";
}

function collectDirectoryIds(nodes: ReviewTreeNode[]): string[] {
  const ids: string[] = [];
  for (const node of nodes) {
    if (!node.isFile) {
      ids.push(node.id);
      if (node.children) {
        ids.push(...collectDirectoryIds(node.children));
      }
    }
  }
  return ids;
}
