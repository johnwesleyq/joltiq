import { expect, test } from "@playwright/test";

const BASE = process.env.SMOKE_BASE_URL, MOCK = process.env.MOCK_TEAMS_URL;
const EMAIL = process.env.E2E_TEAMS_EMAIL, PASSWORD = process.env.E2E_TEAMS_PASSWORD;
test.skip(!BASE || !MOCK || !EMAIL || !PASSWORD, "seeded MOCK Teams acceptance environment not set");
test.use({ baseURL: BASE });

test("MOCK Teams viewer is labelled and receives a redacted approval card", async ({ page, request }) => {
  const messages = await request.get(`${MOCK}/__mock/messages`);
  expect(messages.ok()).toBeTruthy();
  const payload = await messages.json();
  expect(payload.mock).toBe(true);
  const card = payload.messages.find((message: { card: unknown }) => JSON.stringify(message.card).includes("Approval needed"));
  expect(card).toBeTruthy();
  expect(JSON.stringify(card)).not.toContain("@example.com");
  await page.goto(MOCK!);
  await expect(page.getByText(/MOCK Microsoft Teams — DEV\/TEST ONLY/)).toBeVisible();
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL!);
  await page.getByLabel("Password").fill(PASSWORD!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.goto("/admin/integrations");
  await expect(page.getByTestId("teams-connect")).toContainText("MOCK");
});
