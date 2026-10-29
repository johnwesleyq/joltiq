import { expect, test, type Page } from "@playwright/test";

const approvalId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";

async function mockApp(page: Page, role = "org_owner") {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/me") return route.fulfill({ json: {
      user: { id: "owner-1", email: "owner@example.test", name: "Owner", email_verified: true, mfa_enabled: true, is_platform_owner: false },
      organizations: [{ id: "org-1", name: "Example", role, branding: {} }],
      active_organization: { id: "org-1", name: "Example", role, branding: {} }, csrf_token: "csrf",
    } });
    if (path === "/api/v1/notifications") return route.fulfill({ json: { items: [], unread_count: 0 } });
    if (path === "/api/v1/events/stream") return route.fulfill({ contentType: "text/event-stream", body: "" });
    if (path === "/api/v1/admin/approval-notifications") return route.fulfill({ json: {
      reminder_minutes: 15, email_rate_per_hour: 20, slack_rate_per_hour: 30, teams_rate_per_hour: 30,
      channels: Object.fromEntries(["approval_requested", "approval_reminder", "approval_outcome"].map((kind) =>
        [kind, { in_app: true, email: true, slack: false, teams: false }])),
      slack_connected: false, teams_connected: false,
    } });
    const approval = { id: approvalId, status: "PENDING", action: "office.addin.set_load_behavior", risk: "SENSITIVE",
      params: { addin_progid: "PdfPreview.Connect", enabled: false }, reason: "Matching Outlook crash found",
      effects: "Turns off the named add-in for this user", approver_roles: ["admin"], expires_at: "2026-10-29T18:00:00Z",
      created_at: "2026-10-29T17:00:00Z", decided_at: null, ticket_id: "ticket-1", can_decide: true,
      rollback: { status: "AVAILABLE", steps: ["office.addin.restore_from_journal"] } };
    if (path === `/api/v1/approvals/${approvalId}/decision`) return route.fulfill({ json: { ...approval, status: "APPROVED" } });
    if (path === `/api/v1/approvals/${approvalId}`) return route.fulfill({ json: approval });
    if (path === "/api/v1/approvals") return route.fulfill({ json: { items: [approval], total: 1, page: 1, page_size: 50, has_more: false } });
    if (path === `/api/v1/agents/runs/${runId}/verification-evidence`) return route.fulfill({ json: { ok: true, items: [{ verification_id: "v1", ok: true,
      stored_hash: "abc", recomputed_hash: "abc", audit_hash: "abc", chain_ok: true }] } });
    if (path === `/api/v1/agents/runs/${runId}`) return route.fulfill({ json: {
      id: runId, ticket_id: "ticket-1", state: "ESCALATED", paused_for: null, retries: 0, max_retries: 2, cost_usd: 0.01,
      outcome_summary: "Change rolled back and escalated", created_at: "2026-10-29T17:00:00Z", uses_test_fixture_model: false,
      transitions: [{ from: "VERIFYING", to: "ESCALATED", reason: "rollback complete", at: "2026-10-29T17:05:00Z" }],
      plans: [], approvals: [], rollback: { status: "ROLLED_BACK", reason: "Original setting restored", channel: "device", auto: true },
      rollback_snapshot: { addin: { before: 3, after: 2 } },
      verifications: [{ id: "v1", status: "VERIFIED", kind: "rollback", evidence_hash: "abcdef1234567890", at: "2026-10-29T17:05:00Z",
        details: [{ index: 0, type: "config.read", description: "Original value restored", status: "PASS", critical: true, observed: 3, expected: 3 }] }],
    } });
    return route.fulfill({ status: 404, json: { error: { code: "not_found", message: "Not found" } } });
  });
}

test("admin navigation keeps security and adds approval alerts", async ({ page }) => {
  await mockApp(page);
  await page.goto("/portal");
  await expect(page.getByRole("link", { name: "Organization security" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Approval alerts" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  await expect(page.getByText("takes over your browser or computer, only with your permission")).toBeVisible();
});

test("approval shows risk rollback and confirms before deciding", async ({ page }) => {
  await mockApp(page);
  await page.goto(`/approvals/${approvalId}`);
  await expect(page.getByText("Sensitive", { exact: true })).toBeVisible();
  await expect(page.getByTestId("approval-rollback")).toContainText("undo");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("dialog")).toContainText("Approve this step?");
  await expect(page.getByRole("button", { name: "Approve and run" })).toBeVisible();
});

test("approval alert settings and run evidence pages are available", async ({ page }) => {
  await mockApp(page);
  await page.goto("/admin/approval-notifications");
  await expect(page.getByRole("heading", { name: "Approval alerts" })).toBeVisible();
  await expect(page.getByLabel("Approval requested Slack")).not.toBeChecked();
  await page.goto(`/console/runs/${runId}`);
  await expect(page.getByTestId("rollback-result")).toContainText("Original setting restored");
  await expect(page.getByTestId("verification")).toContainText("matches audit chain");
});

test("customer navigation hides staff and admin routes", async ({ page }) => {
  await mockApp(page, "customer");
  await page.goto("/portal");
  await expect(page.getByRole("link", { name: "AI runs" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Organization security" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Approval alerts" })).toHaveCount(0);
});
