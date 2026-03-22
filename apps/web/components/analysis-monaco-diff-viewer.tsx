"use client";

import type { DiffCluster, DiffCodeAnchor } from "@papertrace/contracts";
import dynamic from "next/dynamic";

const DiffEditor = dynamic(() => import("@monaco-editor/react").then((module) => module.DiffEditor), {
  ssr: false,
});

export type MonacoReviewMode = "anchor" | "cluster";

interface AnalysisMonacoDiffViewerProps {
  anchor: DiffCodeAnchor | null;
  cluster: DiffCluster | null;
  mode: MonacoReviewMode;
  className?: string;
  height?: number | string;
}

function languageForFile(filePath: string): string {
  if (filePath.endsWith(".py")) {
    return "python";
  }
  if (filePath.endsWith(".ts") || filePath.endsWith(".tsx")) {
    return "typescript";
  }
  if (filePath.endsWith(".js") || filePath.endsWith(".jsx")) {
    return "javascript";
  }
  if (filePath.endsWith(".json")) {
    return "json";
  }
  if (filePath.endsWith(".md")) {
    return "markdown";
  }
  return "plaintext";
}

function buildClusterPatchDocument(cluster: DiffCluster): { original: string; modified: string } {
  const anchors = cluster.code_anchors ?? [];
  if (anchors.length === 0) {
    return {
      original: "# No original snippets available for this cluster.",
      modified: "# No modified snippets available for this cluster.",
    };
  }

  const originalBlocks: string[] = [];
  const modifiedBlocks: string[] = [];
  for (const [index, anchor] of anchors.entries()) {
    const heading = [
      `# ${index + 1}. ${anchor.original_file_path ?? anchor.file_path} -> ${anchor.file_path}`,
      `kind=${anchor.anchor_kind}`,
      `patch=${anchor.patch_id ?? "n/a"}`,
      `range=${anchor.start_line}-${anchor.end_line}`,
    ].join(" | ");
    const originalRange =
      anchor.original_start_line && anchor.original_end_line
        ? `${anchor.original_start_line}-${anchor.original_end_line}`
        : "n/a";

    originalBlocks.push(
      [heading, `# original=${originalRange}`, anchor.original_snippet ?? "# no original snippet", ""].join("\n"),
    );
    modifiedBlocks.push([heading, `# reason=${anchor.reason}`, anchor.snippet, ""].join("\n"));
  }

  return {
    original: originalBlocks.join("\n"),
    modified: modifiedBlocks.join("\n"),
  };
}

export function AnalysisMonacoDiffViewer({
  anchor,
  cluster,
  mode,
  className,
  height = "280px",
}: AnalysisMonacoDiffViewerProps) {
  const shellClassName = ["monaco-shell", className].filter(Boolean).join(" ");

  if (mode === "cluster" && !cluster) {
    return (
      <div className={`${shellClassName} empty`} data-testid="monaco-evidence-viewer">
        <p className="muted">Select a diff cluster to open the full cluster patch view.</p>
      </div>
    );
  }

  if (mode === "anchor" && !anchor) {
    return (
      <div className={`${shellClassName} empty`} data-testid="monaco-evidence-viewer">
        <p className="muted">Select a code anchor to open the diff viewer.</p>
      </div>
    );
  }

  const originalValue =
    mode === "cluster" && cluster ? buildClusterPatchDocument(cluster).original : (anchor?.original_snippet ?? "");
  const modifiedValue =
    mode === "cluster" && cluster ? buildClusterPatchDocument(cluster).modified : (anchor?.snippet ?? "");
  const language = mode === "cluster" ? "plaintext" : languageForFile(anchor?.file_path ?? "");

  return (
    <div className={shellClassName} data-testid="monaco-evidence-viewer">
      <div className="trace-head">
        <div>
          <small>Monaco diff viewer</small>
          <h4>
            {mode === "cluster" && cluster
              ? `${cluster.id} · full cluster patch`
              : `${anchor?.original_file_path ?? "unknown"} -> ${anchor?.file_path ?? "unknown"}:${anchor?.start_line ?? 0}-${anchor?.end_line ?? 0}`}
          </h4>
        </div>
        <strong>{mode === "cluster" ? `${cluster?.code_anchors?.length ?? 0} anchors` : anchor?.anchor_kind}</strong>
      </div>
      <p className="muted">
        {mode === "cluster" && cluster
          ? "Aggregated patch review surface across all code anchors in the selected cluster."
          : `${anchor?.original_file_path ?? "unknown"}:${
              anchor?.original_start_line && anchor?.original_end_line
                ? `${anchor.original_start_line}-${anchor.original_end_line}`
                : "n/a"
            } -> ${anchor?.file_path ?? "unknown"}:${anchor?.start_line ?? 0}-${anchor?.end_line ?? 0}`}
      </p>
      <div className="monaco-frame">
        <DiffEditor
          height={height}
          language={language}
          options={{
            minimap: { enabled: false },
            readOnly: true,
            renderSideBySide: true,
            scrollBeyondLastLine: false,
            wordWrap: "on",
            automaticLayout: true,
            renderOverviewRuler: false,
            fontSize: 13,
          }}
          original={originalValue}
          modified={modifiedValue}
          theme="vs-light"
        />
      </div>
    </div>
  );
}
