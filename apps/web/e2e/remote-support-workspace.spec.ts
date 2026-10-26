import { expect, test, type Page } from "@playwright/test";

const deviceId = "11111111-2222-4333-8444-555555555555";

async function mockApp(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/v1/auth/me") {
      return route.fulfill({
        json: {
          user: { id: "staff-1", email: "tech@example.test", name: "Taylor", email_verified: true, mfa_enabled: true, is_platform_owner: false },
          organizations: [{ id: "org-1", name: "Example Co", role: "technician", branding: {} }],
          active_organization: { id: "org-1", name: "Example Co", role: "technician", branding: {} },
          csrf_token: "test-csrf",
        },
      });
    }
    if (pathname === "/api/v1/notifications") return route.fulfill({ json: { unread_count: 0 } });
    if (pathname === `/api/v1/devices/${deviceId}/remote-access`) {
      return route.fulfill({
        json: {
          enabled: true,
          configured: true,
          active_session: {
            id: "session-1",
            device_id: deviceId,
            state: "active",
            reason: "Customer requested help opening a signed PDF",
            created_at: "2026-10-26T20:00:00Z",
            ended_at: null,
            workspace_path: `/portal/devices/${deviceId}/remote-support`,
            transport_url: "http://127.0.0.1:3100/transport-placeholder",
          },
        },
      });
    }
    if (pathname === "/api/v1/events/stream") return route.fulfill({ contentType: "text/event-stream", body: "" });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Not found" } } });
  });
  await page.route("**/transport-placeholder", (route) => route.fulfill({ body: "Remote canvas" }));
}

test("shows an active session as waiting without exposing its transport provider", async ({ page }) => {
  await mockApp(page);
  await page.goto(`/portal/devices/${deviceId}/remote-support`);

  await expect(page.getByRole("heading", { name: "Remote support" })).toBeVisible();
  await expect(page.getByText("Customer requested help opening a signed PDF")).toBeVisible();
  await expect(page.getByText("Waiting for the person at the computer")).toBeVisible();
  await expect(page.getByTitle("JoltIQ remote desktop")).toBeVisible();
  await expect(page.getByRole("button", { name: "End support" })).toBeVisible();
  await expect(page.getByText(/connected/i)).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(/MeshCentral|node ID/i);
});
