import { expect, test } from "@playwright/test";

test("renders the local MVP shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("PaperTrace local MVP")).toBeVisible();
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
  await expect(page.getByText("strategy chain")).toBeVisible();
  await expect(page.getByText("Contribution mappings")).toBeVisible();
  await expect(page.getByText("D1 → C1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open job" }).first()).toBeVisible();
});
