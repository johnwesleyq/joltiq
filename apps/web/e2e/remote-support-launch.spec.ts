import { expect, test, type Page } from "@playwright/test";

const deviceId = "11111111-2222-4333-8444-555555555555";
const workspacePath = `/portal/devices/${deviceId}/remote-support`;

async function mockDevicePage(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/auth/me") return route.fulfill({ json: {
      user: { id: "staff-1", email: "tech@example.test", name: "Taylor", email_verified: true, mfa_enabled: true, is_platform_owner: false },
      organizations: [{ id: "org-1", name: "Example Co", role: "technician", branding: {} }],
      active_organization: { id: "org-1", name: "Example Co", role: "technician", branding: {} },
      csrf_token: "test-csrf",
    } });
    if (pathname === "/api/v1/notifications") return route.fulfill({ json: { unread_count: 0 } });
    if (pathname === "/api/v1/devices") return route.fulfill({ json: { items: [{
      id: deviceId, hostname: "WIN11-LAB", os_name: "Windows", os_version: "11", agent_version: "1.0.0",
      status: "active", online: true, last_seen_at: "2026-10-26T20:00:00Z",
    }], total: 1, page: 1, page_size: 50, has_more: false } });
    if (pathname === `/api/v1/devices/${deviceId}/remote-access` && request.method() === "GET") {
      return route.fulfill({ json: { enabled: true, configured: true, active_session: null } });
    }
    if (pathname === `/api/v1/devices/${deviceId}/remote-access/sessions` && request.method() === "POST") {
      return route.fulfill({ status: 201, json: {
        id: "session-1", device_id: deviceId, state: "active", reason: "Customer requested visible troubleshooting",
        workspace_path: workspacePath, transport_url: "http://127.0.0.1:3100/provider-transport", launch_url: "http://127.0.0.1:3100/provider-transport",
        created_at: "2026-10-26T20:00:00Z", ended_at: null,
      } });
    }
    if (pathname === "/api/v1/events/stream") return route.fulfill({ contentType: "text/event-stream", body: "" });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Not found" } } });
  });
  await page.route("**/provider-transport", (route) => route.fulfill({ body: "Provider transport" }));
}

async function startSession(page: Page) {
  await page.goto("/portal/devices");
  await page.getByText("Remote support", { exact: true }).click();
  await page.getByLabel("Why access is needed").fill("Customer requested visible troubleshooting");
  await page.getByRole("button", { name: "Request remote desktop" }).click();
}

test("opens the JoltIQ workspace instead of the transport URL", async ({ page }) => {
  await page.addInitScript(() => {
    const opened = { location: { href: "about:blank" }, close: () => undefined };
    Object.defineProperty(window, "__openedSupportWindow", { value: opened, configurable: true });
    window.open = () => opened as unknown as Window;
  });
  await mockDevicePage(page);
  await startSession(page);

  await expect.poll(() => page.evaluate(() => (window as Window & { __openedSupportWindow?: { location: { href: string } } }).__openedSupportWindow?.location.href)).toBe(workspacePath);
});

test("uses same-tab navigation when the browser blocks the popup", async ({ page }) => {
  await page.addInitScript(() => { window.open = () => null; });
  await mockDevicePage(page);
  await startSession(page);

  await expect(page).toHaveURL(new RegExp(`${workspacePath.replaceAll("/", "\\/")}$`));
});
