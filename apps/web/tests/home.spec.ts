import { expect, test } from "@playwright/test";

function escapePdfText(value: string): string {
  return value.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)");
}

function buildPdfBuffer(title: string, body: string): Buffer {
  const escapedBody = escapePdfText(body);
  const stream = Buffer.from(
    `<< /Length ${Buffer.byteLength(escapedBody, "latin1") + 31} >>\nstream\nBT\n/F1 16 Tf\n36 96 Td\n(${escapedBody}) Tj\nET\nendstream`,
    "latin1",
  );
  const objects = [
    Buffer.from("<< /Type /Catalog /Pages 2 0 R >>\n", "latin1"),
    Buffer.from("<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n", "latin1"),
    Buffer.from(
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n",
      "latin1",
    ),
    Buffer.concat([stream, Buffer.from("\n", "latin1")]),
    Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n", "latin1"),
  ];

  const chunks: Buffer[] = [Buffer.from("%PDF-1.4\n", "latin1")];
  const offsets: number[] = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.concat(chunks).length);
    chunks.push(Buffer.from(`${index + 1} 0 obj\n`, "latin1"));
    chunks.push(object);
    chunks.push(Buffer.from("endobj\n", "latin1"));
  }
  const xrefOffset = Buffer.concat(chunks).length;
  chunks.push(Buffer.from(`xref\n0 ${objects.length + 1}\n`, "latin1"));
  chunks.push(Buffer.from("0000000000 65535 f \n", "latin1"));
  for (const offset of offsets.slice(1)) {
    chunks.push(Buffer.from(`${String(offset).padStart(10, "0")} 00000 n \n`, "latin1"));
  }
  chunks.push(
    Buffer.from(
      `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R /Info << /Title (${escapePdfText(title)}) >> >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
      "latin1",
    ),
  );
  return Buffer.concat(chunks);
}

test("renders the local MVP shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("PaperTrace local MVP")).toBeVisible();
  await expect(page.getByLabel("Paper source type")).toBeVisible();
  await expect(page.getByLabel("PDF upload")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /Force reanalysis/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Analyze" })).toBeVisible();
  await expect(page.getByText("API runtime config")).toHaveCount(0);
  await expect(page.getByText("What the MVP returns")).toHaveCount(0);
});

test("submits an analysis and renders mapped results", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Analyze" }).click();

  await expect(page.getByText("Selected base repo")).toBeVisible();
  await expect(page.getByText("https://github.com/huggingface/transformers").first()).toBeVisible();
  await expect(page.getByText("Runtime provenance")).toBeVisible();
  await expect(page.getByText("Paper fetch mode")).toBeVisible();
  await expect(page.getByText("strategy chain")).toBeVisible();
  await expect(page.getByText("Review handoff")).toBeVisible();
  await expect(page.getByText("Lineage explorer")).toBeVisible();
  await page.getByRole("button", { name: "Signal rings" }).click();
  await expect(page.getByText("Review sequence")).toBeVisible();
  await page.getByRole("button", { name: "Hypothesis paths" }).click();
  const evidenceLink = page.getByRole("link", { name: "Open evidence workspace" });
  const evidenceHref = await evidenceLink.getAttribute("href");
  expect(evidenceHref).toMatch(/\/analyses\/.*\/evidence/);
  await page.goto(evidenceHref ?? "/");
  await expect(page).toHaveURL(/\/analyses\/.*\/evidence/);
  await expect(page.getByText("Evidence review board")).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole("button", { name: /Primary/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Added/i })).toBeVisible();
  await expect(page.getByRole("button", { name: /Large Files/i })).toBeVisible();
  await expect(page.getByTestId("github-review-grid")).toBeVisible();
  await expect(page.getByTestId("github-filetree-pane")).toBeVisible();
  await expect(page.getByTestId("github-diff-pane")).toBeVisible();
  await expect(page.getByTestId("paper-review-pane")).toBeVisible();
  await expect(page.getByText("Sentence-level correspondence")).toBeVisible();
  await expect(page.getByText("Split diff review")).toBeVisible();
  await expect(page.locator(".review-v2-claim-card").first()).toBeVisible();
  await page.locator(".review-v2-claim-card").first().click();
  await expect(page.locator(".review-v2-claim-card").first()).toHaveClass(/active/);
  const directoryNodes = page.locator(".file-tree-node.dir");
  if ((await directoryNodes.count()) > 0) {
    const firstDirectory = directoryNodes.first();
    await expect(firstDirectory).toHaveAttribute("aria-expanded", "true");
    await firstDirectory.click();
    await expect(firstDirectory).toHaveAttribute("aria-expanded", "false");
    await firstDirectory.click();
    await expect(firstDirectory).toHaveAttribute("aria-expanded", "true");
  }
  const fileNodes = page.locator(".review-v2-tree-file");
  await expect(fileNodes.first()).toBeVisible();
  if ((await fileNodes.count()) > 1) {
    await fileNodes.nth(1).click();
    await expect(fileNodes.nth(1)).toHaveClass(/active/);
  }
  await expect(page.getByRole("link", { name: "Back to shell" })).toBeVisible();
});

test("uploads a PDF and renders pdf-file provenance", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Paper source" }).fill("");
  await page.getByLabel("PDF upload").setInputFiles({
    name: "lora-upload.pdf",
    mimeType: "application/pdf",
    buffer: buildPdfBuffer(
      "LoRA Upload",
      "Abstract LoRA low-rank adaptation modules keep the pretrained backbone frozen during training.",
    ),
  });

  await expect(page.getByText("Selected file: lora-upload.pdf")).toBeVisible();
  await page.getByRole("button", { name: "Analyze" }).click();

  await expect(page.getByText("Selected base repo")).toBeVisible({ timeout: 30000 });
  await expect(page.getByText("Low-rank adaptation modules").first()).toBeVisible({ timeout: 30000 });
  await expect(page.getByText("Review handoff")).toBeVisible({ timeout: 30000 });
  await expect(
    page
      .locator(".item")
      .filter({ has: page.getByRole("heading", { name: "Paper source kind" }) })
      .getByText("pdf file", { exact: true }),
  ).toBeVisible();
});
