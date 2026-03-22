"use client";

import dynamic from "next/dynamic";

const Editor = dynamic(() => import("@monaco-editor/react").then((module) => module.Editor), {
  ssr: false,
});

interface AnalysisMonacoCodeViewerProps {
  value: string;
  filePath?: string | null;
  rangeLabel?: string;
  height?: number | string;
  emptyMessage: string;
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
  if (filePath.endsWith(".yml") || filePath.endsWith(".yaml")) {
    return "yaml";
  }
  return "plaintext";
}

export function AnalysisMonacoCodeViewer({
  value,
  filePath,
  rangeLabel,
  height = "560px",
  emptyMessage,
}: AnalysisMonacoCodeViewerProps) {
  if (!value.trim()) {
    return (
      <div className="review-editor-shell empty">
        <p className="muted">{emptyMessage}</p>
      </div>
    );
  }

  return (
    <div className="review-editor-shell">
      <div className="trace-head">
        <div>
          <small>Snippet viewer</small>
          <h4>{filePath ?? "unknown file"}</h4>
        </div>
        <strong>{rangeLabel ?? "snippet"}</strong>
      </div>
      <div className="review-editor-frame">
        <Editor
          height={height}
          language={languageForFile(filePath ?? "")}
          options={{
            automaticLayout: true,
            glyphMargin: false,
            lineNumbersMinChars: 3,
            minimap: { enabled: false },
            readOnly: true,
            renderLineHighlight: "none",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            fontSize: 13,
          }}
          theme="vs-light"
          value={value}
        />
      </div>
    </div>
  );
}
