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
  await expect(page.getByText("API runtime config")).toBeVisible();
  await expect(page.getByLabel("PDF upload")).toBeVisible();
  await expect(page.getByRole("button", { name: "Analyze" })).toBeVisible();
});

test("submits an analysis and renders mapped results", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Analyze" }).click();

  await expect(page.getByText("Selected base repo")).toBeVisible();
  await expect(
    page.getByRole("strong").filter({ hasText: "https://github.com/huggingface/transformers" }),
  ).toBeVisible();
  await expect(page.getByText("Runtime provenance")).toBeVisible();
  await expect(page.getByText("Paper fetch mode")).toBeVisible();
  await expect(page.getByText("strategy chain")).toBeVisible();
  await expect(page.getByText("Contribution mappings")).toBeVisible();
  await expect(page.getByText("D1 → C1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open job" }).first()).toBeVisible();
});

test("uploads a PDF and renders pdf-file provenance", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Paper source").fill("");
  await page.getByLabel("Repository URL").fill("https://github.com/microsoft/LoRA");
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

  await expect(page.getByText("Selected base repo")).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("heading", { name: /Low-rank adaptation modules/i })).toBeVisible({ timeout: 15000 });
  await expect(
    page
      .locator(".item")
      .filter({ has: page.getByRole("heading", { name: "Paper source kind" }) })
      .getByText("pdf file", { exact: true }),
  ).toBeVisible();
});
