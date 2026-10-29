import { expect, test } from "@playwright/test";

const BASE = process.env.SMOKE_BASE_URL;
test.skip(!BASE, "SMOKE_BASE_URL not set (needs a running stack)");
test.use({ baseURL: BASE });

test("public pages and owner console render on the supplied production-shaped server", async ({ page }) => {
  test.setTimeout(180_000);
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
  for (const path of ["/", "/features", "/pricing", "/security", "/login", "/register"]) {
    expect((await page.goto(path, { waitUntil: "load" }))?.status(), path).toBeLessThan(400);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
  }
  const email = `smoke-${Date.now()}@example.com`, password = "SmokeTestPass!2026";
  expect((await page.request.post("/api/v1/auth/register", { data: {
    email, password, name: "Smoke Owner", organization_name: "DEMO Smoke Company",
  } })).ok()).toBeTruthy();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  for (const path of ["/console", "/console/runs", "/approvals", "/admin/integrations", "/admin/knowledge", "/admin/policies", "/admin/security", "/admin/approval-notifications"]) {
    expect((await page.goto(path, { waitUntil: "load" }))?.status(), path).toBeLessThan(400);
    await expect(page.locator("main h1").first()).toBeVisible();
  }
  expect(problems, problems.join("\n")).toEqual([]);
});
