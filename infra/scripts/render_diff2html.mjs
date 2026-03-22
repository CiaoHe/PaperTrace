import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import process from "node:process";

import { html as renderDiffToHtml } from "diff2html";

const [, , rawDiffPath, outputPath] = process.argv;

if (!rawDiffPath || !outputPath) {
  console.error("Usage: node render_diff2html.mjs <raw-diff-path> <output-path>");
  process.exit(1);
}

const require = createRequire(import.meta.url);
const cssPath = require.resolve("diff2html/bundles/css/diff2html.min.css");
const [rawDiff, css] = await Promise.all([
  readFile(resolve(rawDiffPath), "utf8"),
  readFile(cssPath, "utf8"),
]);

const renderedDiff = renderDiffToHtml(rawDiff, {
  drawFileList: false,
  matching: "none",
  outputFormat: "side-by-side",
  renderNothingWhenEmpty: false,
});

const document = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PaperTrace Review Diff</title>
    <style>
${css}

      :root {
        color-scheme: light;
      }

      body {
        margin: 0;
        background: #f6f8fa;
        color: #24292f;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }

      .papertrace-render-shell {
        padding: 16px;
      }

      .d2h-wrapper {
        margin: 0 auto;
        max-width: 1600px;
        border: 1px solid #d0d7de;
        border-radius: 8px;
        overflow: hidden;
        background: #ffffff;
      }
    </style>
  </head>
  <body>
    <div class="papertrace-render-shell">
      <div class="d2h-wrapper">
        ${renderedDiff}
      </div>
    </div>
  </body>
</html>
`;

await writeFile(resolve(outputPath), document, "utf8");
