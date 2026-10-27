# JoltIQ Support Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct exposure to the MeshCentral administration UI with a focused JoltIQ-branded remote-support workspace while preserving forced local consent and audited session lifecycle.

**Architecture:** MeshCentral remains the self-hosted desktop transport and continues to enforce local consent. The JoltIQ API returns provider-neutral session metadata plus an internal launch URL; the web app opens a dedicated JoltIQ route that embeds the minimal remote desktop view and surrounds it with JoltIQ session state, reason, safety copy, and an explicit end control. Provider setup remains admin-only.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, FastAPI, SQLAlchemy, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-joltiq-support-platform-design.md`

## Global Constraints

- MeshCentral remains a transport only; do not move ticket, approval, automation, or verification decisions into it.
- Only staff with `devices.manage` can bind a node or start/end remote access.
- The customer must approve MeshCentral desktop access locally; timeout and locked/no-user cases deny access.
- Do not expose provider credentials, access tokens, or tenant-supplied provider URLs.
- The workspace must retain a visible session reason and End support control.
- Provider terminology and node IDs are hidden from ordinary customer-facing copy.
- Existing dirty worktree changes are preserved.

## Review Focus

- Popup blocking: the JoltIQ workspace still opens in the current tab and never loses the newly created audit session.
- Reload during an active session: the workspace recovers the active session and its End control from the API.
- Missing provider login: the workspace explains that staff authentication is required without claiming the customer denied access.
- Offline or revoked device: no remote session is started and the UI gives a specific recovery action.
- Session end while the iframe is disconnected: JoltIQ records the end idempotently and returns to the device list.

---

### Task 1: Provider-neutral session contract

**Files:**
- Modify: `apps/api/joltiq/services/remote_access.py`
- Modify: `apps/api/joltiq/routers/devices.py`
- Test: `apps/api/tests/test_remote_access.py`

**Interfaces:**
- Produces: `serialize(db, session) -> dict` with `workspace_path`, `transport_url`, `state`, `reason`, and timestamps.
- Consumes: existing deployment-owned `configured_base_url()` and tenant-scoped `RemoteAccessSession`.

- [ ] **Step 1: Write the failing API assertions**

Assert that a started session returns `workspace_path == "/portal/devices/{device_id}/remote-support"`, retains the fixed HTTPS `transport_url`, and never returns provider credentials.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `docker compose run --rm -T -v "$PWD/apps/api:/workspace/apps/api" -w /workspace/apps/api -e PYTHONPATH=/workspace/apps/api -e JOLTIQ_ENV=test -e JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:joltiq_app@postgres:5432/joltiq_test -e JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/joltiq_test -e JOLTIQ_REDIS_URL=redis://redis:6379/15 api pytest -q tests/test_remote_access.py`

Expected: FAIL because `workspace_path` and `transport_url` are absent.

- [ ] **Step 3: Implement the response contract**

Change `serialize(db: Session, session: RemoteAccessSession) -> dict` to add the exact fields while keeping `launch_url` temporarily as a compatibility alias for `transport_url`.

- [ ] **Step 4: Run the focused API tests**

Run the Step 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: expose JoltIQ remote workspace route`

### Task 2: Dedicated JoltIQ remote-support workspace

**Files:**
- Create: `apps/web/src/app/(app)/portal/devices/[id]/remote-support/page.tsx`
- Create: `apps/web/src/components/RemoteSupportWorkspace.tsx`
- Modify: `apps/web/src/app/globals.css`
- Test: `apps/web/e2e/remote-support-workspace.spec.ts`

**Interfaces:**
- Consumes: `GET /devices/{id}/remote-access` and `POST /devices/{id}/remote-access/sessions/{session_id}/end`.
- Produces: `RemoteSupportWorkspace({ deviceId }: { deviceId: string })`.

- [ ] **Step 1: Write the failing Playwright scenario**

Mock the status endpoint with an active session and assert the page renders `Remote support`, the session reason, `Waiting for the person at the computer`, a provider-neutral embedded desktop title, and `End support` without the words `MeshCentral` or `node ID`. Do not claim the desktop is connected from JoltIQ's audit state alone.

- [ ] **Step 2: Run the scenario to verify it fails**

Run: `npm --prefix apps/web run test:e2e -- remote-support-workspace.spec.ts`

Expected: FAIL because the route does not exist.

- [ ] **Step 3: Implement the workspace shell**

Create the route and component. Use a two-column layout on wide screens and one column on narrow screens: a 280px safety/status rail and a flexible remote canvas. Use existing color tokens, `Unbounded` only for the page title, and the existing `Manrope` body face. The active status rail uses ion cyan and says that local permission is still required; ended uses slate. Embed only `transport_url` in an iframe titled `JoltIQ remote desktop`, with `sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"`, `allow="clipboard-read; clipboard-write; fullscreen"`, and no provider name in visible copy.

- [ ] **Step 4: Add recovery and accessibility states**

Render explicit loading, missing login/transport, ended, and API error states; add a visible keyboard focus style, iframe title, and reduced-motion-safe status indicator.

- [ ] **Step 5: Run Playwright, lint, typecheck, and build**

Run: `npm --prefix apps/web run test:e2e -- remote-support-workspace.spec.ts && npm --prefix apps/web run lint && npm --prefix apps/web run typecheck && npm --prefix apps/web run build`

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

Commit message: `feat: add branded remote support workspace`

### Task 3: Launch the JoltIQ workspace instead of the provider UI

**Files:**
- Modify: `apps/web/src/app/(app)/portal/devices/page.tsx`
- Test: `apps/web/e2e/remote-support-launch.spec.ts`

**Interfaces:**
- Consumes: Task 1 `workspace_path` and Task 2 route.
- Produces: the existing `RemoteSupport` action opening JoltIQ's workspace.

- [ ] **Step 1: Write the failing launch scenario**

Assert that starting a session opens `workspace_path`, not `transport_url`, and that popup blocking falls back to same-tab navigation.

- [ ] **Step 2: Run the scenario to verify it fails**

Run: `npm --prefix apps/web run test:e2e -- remote-support-launch.spec.ts`

Expected: FAIL because the device page opens the provider URL.

- [ ] **Step 3: Change `RemoteSupport.open()`**

Navigate the pre-opened popup to `session.workspace_path`; retain same-tab fallback. Replace customer-facing references to MeshCentral with `JoltIQ Remote Support` and `the approval prompt on the PC`.

- [ ] **Step 4: Run focused and full web verification**

Run the Task 2 Step 5 command plus `remote-support-launch.spec.ts`. Expected: all commands exit 0.

- [ ] **Step 5: Commit**

Commit message: `feat: launch remote sessions inside JoltIQ`

### Task 4: Reliable consent prompt foregrounding

**Files:**
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs`
- Test: `apps/windows-agent/tests/JoltIQ.Agent.Tests/SupportConversationTests.cs`

**Interfaces:**
- Consumes: existing local-consent and `waiting_customer` notifications.
- Produces: `ShowForegroundPrompt(...)` behavior that activates the exact JoltIQ prompt without stealing focus for noninteractive status updates.

- [ ] **Step 1: Write failing tests**

Cover local session consent, consequential-action approval, ordinary question, duplicate prompt, locked screen, and already-visible prompt. Assert consent/approval/question prompts request foreground activation once; status updates do not.

- [ ] **Step 2: Run the Windows test group and verify failure**

Run the existing physical-Windows `SupportConversationTests` procedure from the acceptance runbook.

Expected: the new foreground assertions fail.

- [ ] **Step 3: Implement foreground activation**

Add a single UI-thread path that restores the minimized window, places only actionable prompts at the front, and records a structured diagnostic when Windows refuses activation. Do not simulate a user click.

- [ ] **Step 4: Run focused Windows tests and cross-build**

Expected: focused tests pass and the Windows projects build with zero warnings/errors.

- [ ] **Step 5: Commit**

Commit message: `fix: foreground actionable JoltIQ prompts`

### Task 5: Acceptance evidence and operator copy

**Files:**
- Modify: `docs/runbooks/meshcentral-remote-access.md`
- Create: `docs/evidence/meshcentral/workspace-acceptance.md`

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: reproducible acceptance evidence and launch guidance.

- [ ] **Step 1: Update the runbook**

Document that staff launch JoltIQ's workspace, the provider admin UI is setup-only, and protected Windows prompts require the local user.

- [ ] **Step 2: Run physical Windows acceptance**

Prove: request reason recorded; local Allow required; workspace connects; privacy banner remains; denial blocks access; End support closes the audit session; approval prompt foregrounds; Adobe Genuine Service remains installed after the controlled Adobe test.

- [ ] **Step 3: Save evidence**

Record timestamps, run/session IDs, audit events, screenshots, and pass/fail outcomes without secrets.

- [ ] **Step 4: Run regression suites**

Run API remote-access/computer-use tests, web lint/typecheck/build/e2e, and Windows companion focus/conversation tests.

Expected: all scoped suites pass; unrelated failures are listed honestly.

- [ ] **Step 5: Commit**

Commit message: `docs: record support workspace acceptance`

## Follow-on plans

- Expert Knowledge ingestion, provenance, domain packs, and evaluation.
- Suggest-and-Fix recommendations, applicability checks, approved execution, verification, and rollback.
- Production release hardening, signing, installer lifecycle, security review, and supported-device matrix.
