import { expect, test, type Page } from "@playwright/test";

async function mockAdmin(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: {
      user: { id: "owner-1", email: "owner@example.test", name: "Owner", email_verified: true, mfa_enabled: true, is_platform_owner: false },
      organizations: [{ id: "org-1", name: "Example", role: "org_owner", branding: {} }],
      active_organization: { id: "org-1", name: "Example", role: "org_owner", branding: {} }, csrf_token: "csrf",
    } });
    if (path === "/api/v1/admin/security/oidc" && request.method() === "GET") return route.fulfill({ json: {
      id: "provider-1", display_name: "Company Microsoft", kind: "entra",
      issuer: "https://login.microsoftonline.com/tenant/v2.0", client_id: "client-id",
      client_secret_set: true, scopes: "openid email profile", domains: ["example.test"],
      default_role: "technician", role_mapping: {}, jit_provisioning: false, enforce_sso: false,
      enabled: true, tested_at: "2026-10-28T20:00:00Z",
      callback_url: "https://support.example.test/api/v1/auth/sso/callback",
    } });
    if (path === "/api/v1/notifications") return route.fulfill({ json: { unread_count: 0 } });
    if (path === "/api/v1/events/stream") return route.fulfill({ contentType: "text/event-stream", body: "" });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Not found" } } });
  });
}

test("login offers work-email SSO without removing password login", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "Continue with work email" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});

test("organization security page shows redacted OIDC configuration", async ({ page }) => {
  await mockAdmin(page);
  await page.goto("/admin/security");
  await expect(page.getByRole("heading", { name: "Organization security" })).toBeVisible();
  await expect(page.getByText("https://support.example.test/api/v1/auth/sso/callback")).toBeVisible();
  await expect(page.getByText("A client secret is saved")).toBeVisible();
  await expect(page.getByText("client-secret-value")).toHaveCount(0);
  await expect(page.getByText(/break-glass recovery requires server CLI access/i)).toBeVisible();
});
