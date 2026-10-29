import { expect, test, type Page } from "@playwright/test";

const BASE = process.env.SMOKE_BASE_URL;
test.skip(!BASE, "SMOKE_BASE_URL not set (needs a running stack)");
test.use({ baseURL: BASE });
test.describe.configure({ mode: "serial" });

const password = "FlowsTestPass!2026";
let ownerEmail: string | undefined;

async function owner(page: Page) {
  if (!ownerEmail) {
    ownerEmail = `flows-${Date.now()}@example.com`;
    const response = await page.request.post("/api/v1/auth/register", { data: {
      email: ownerEmail, password, name: "Flows Owner", organization_name: "DEMO Flows Company",
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(ownerEmail);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

test("queue search opens a ticket and preserves its timeline", async ({ page }) => {
  await owner(page);
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  const created = await page.request.post("/api/v1/tickets", { headers: { "x-csrf-token": me.csrf_token }, data: {
    subject: "DEMO flows: VPN drops", description: "Repeatable local acceptance ticket", priority: "normal",
  } });
  expect(created.ok(), await created.text()).toBeTruthy();
  await page.goto("/console");
  await page.getByLabel("Search tickets").fill("VPN drops");
  await page.getByRole("link", { name: "DEMO flows: VPN drops" }).click();
  await expect(page.locator("main h1")).toContainText("VPN drops");
  await expect(page.getByText(/Timeline \(/)).toBeVisible();
});

test("work-email SSO and policy sentence builder remain available", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Continue with work email" })).toBeVisible();
  await owner(page);
  await page.goto("/admin/policies");
  await expect(page.getByLabel("Rule name")).toBeVisible();
  await expect(page.getByText("When action")).toBeVisible();
  await expect(page.locator("textarea")).toHaveCount(0);
});
