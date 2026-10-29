import { expect, test } from "@playwright/test";

const BASE = process.env.SMOKE_BASE_URL, MOCK = process.env.MOCK_SLACK_URL;
const EMAIL = process.env.E2E_SLACK_EMAIL, PASSWORD = process.env.E2E_SLACK_PASSWORD;
test.skip(!BASE || !MOCK || !EMAIL || !PASSWORD, "seeded MOCK Slack acceptance environment not set");
test.use({ baseURL: BASE });

test("MOCK Slack receives a redacted approval deep link and settings are visible", async ({ page, request }) => {
  const messages = await request.get(`${MOCK}/__mock/messages`);
  expect(messages.ok()).toBeTruthy();
  const payload = await messages.json();
  expect(payload.mock).toBe(true);
  const approval = payload.messages.find((message: { text: string }) => message.text.startsWith("Approval needed"));
  expect(approval).toBeTruthy();
  expect(JSON.stringify(approval)).not.toContain("@example.com");
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.goto("/admin/approval-notifications");
  await expect(page.getByLabel("Approval requested Slack")).toBeVisible();
  await page.goto("/admin/integrations");
  await expect(page.getByTestId("slack-connect")).toContainText("MOCK");
});
