"use client";

import type { DiffCodeAnchor } from "@papertrace/contracts";
import dynamic from "next/dynamic";

const DiffEditor = dynamic(async () => (await import("@monaco-editor/react")).DiffEditor, {
  ssr: false,
});

interface AnalysisMonacoDiffViewerProps {
  anchor: DiffCodeAnchor | null;
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

export function AnalysisMonacoDiffViewer({ anchor }: AnalysisMonacoDiffViewerProps) {
  if (!anchor) {
    return (
      <div className="monaco-shell empty" data-testid="monaco-evidence-viewer">
        <p className="muted">Select a code anchor to open the diff viewer.</p>
      </div>
    );
  }

  const originalValue = anchor.original_snippet ?? "";
  const modifiedValue = anchor.snippet;

  return (
    <div className="monaco-shell" data-testid="monaco-evidence-viewer">
      <div className="trace-head">
        <div>
          <small>Monaco diff viewer</small>
          <h4>
            {anchor.file_path}:{anchor.start_line}-{anchor.end_line}
          </h4>
        </div>
        <strong>{anchor.anchor_kind}</strong>
      </div>
      <p className="muted">
        original{" "}
        {anchor.original_start_line && anchor.original_end_line
          ? `${anchor.original_start_line}-${anchor.original_end_line}`
          : "n/a"}{" "}
        {"->"} current {anchor.start_line}-{anchor.end_line}
      </p>
      <DiffEditor
        height="280px"
        language={languageForFile(anchor.file_path)}
        options={{
          minimap: { enabled: false },
          readOnly: true,
          renderSideBySide: true,
          scrollBeyondLastLine: false,
          wordWrap: "on",
          automaticLayout: true,
          fontSize: 13,
        }}
        original={originalValue}
        modified={modifiedValue}
        theme="vs-light"
      />
    </div>
  );
}
