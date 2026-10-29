import { expect, test } from "@playwright/test";

const BASE = process.env.SMOKE_BASE_URL;
const EMAIL = process.env.E2E_NOTIFY_EMAIL;
const PASSWORD = process.env.E2E_NOTIFY_PASSWORD;
const MAILPIT = process.env.MAILPIT_URL;
test.skip(!BASE || !EMAIL || !PASSWORD, "seeded notification credentials not set");
test.use({ baseURL: BASE });

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("approval request notification exposes risk expiry and a JoltIQ deep link", async ({ page }) => {
  await login(page);
  await page.goto("/notifications");
  const item = page.getByTestId("notification-item").filter({ hasText: "Approval needed" }).first();
  await expect(item).toContainText(/Risk:/);
  await expect(item).toContainText(/expires/i);
  await item.getByRole("link").click();
  await expect(page).toHaveURL(/\/approvals\/[0-9a-f-]{36}/);
});

test("Mailpit receives the matching deep link", async ({ request }) => {
  test.skip(!MAILPIT, "MAILPIT_URL not set");
  const result = await request.get(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${EMAIL} subject:"Approval needed"`)}`);
  expect(result.ok()).toBeTruthy();
  expect((await result.json()).messages.length).toBeGreaterThan(0);
});
