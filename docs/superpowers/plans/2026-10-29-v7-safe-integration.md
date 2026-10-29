# JoltIQ V7 Safe Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port V7's verification, rollback, approval-alert, collaboration, development-fixture, and UI improvements into the current JoltIQ branch without replacing its newer OIDC, direct-device, remote-support, browser, or Windows work.

**Architecture:** The current branch remains canonical. V7 behavior is reimplemented in bounded slices against current models, starting with forward-only migrations after `0009_oidc_sso`, followed by service orchestration, connectors, fixtures, and UI. Every slice begins with adapted V7 tests, remains disabled by default where it can create egress or device authority, and passes current regressions before the next slice begins.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL RLS, Redis, httpx, Next.js 16, React 19, TypeScript, Playwright, .NET 8, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-29-v7-safe-integration-design.md`

## Global Constraints

- Current JoltIQ is canonical; `/Users/johnw/Downloads/joltiq-git-v7.zip` is untrusted reference material only.
- Do not delete or replace OIDC/Entra, direct-device help, guided assistance, MeshCentral, browser-worker, Windows companion, procedure, policy, ticket, knowledge, or audit behavior.
- Do not renumber or rewrite committed migration `0009_oidc_sso`; new migrations start at `0010` and are forward-only.
- The unexecuted Freshdesk-hardening and audit-anchoring plans must rebase their planned migration numbers after `0013`; do not create a second Alembic head.
- All Slack, Teams, Freshdesk mock, headless companion, and local fixture behavior is development-only or disabled by default and makes zero external calls when unset.
- Guide mode has no input authority. Fix mode remains exact-goal, consent, approval, STOP, verification, and audit bound.
- Never report customer-confirmed success as independently verified success.
- Do not use `npm audit fix --force`; dependency remediation must preserve runtime and test contracts.
- Do not claim live SaaS, Windows VM, signing, installer, Entra, AI-provider, or external-security acceptance without external evidence.
- Each task follows red-green TDD and ends in a focused commit.

## Review Focus

- Upgrade a database containing OIDC identities, sessions, devices, integrations, and tickets through all four new migrations; every row and default behavior must survive. Covered in Task 1 migration-upgrade tests.
- Lose/replay control after a lease handoff or STOP while an action is in flight; stale browser/device commands must fail closed and never be counted twice. Covered in Task 3 lease/STOP tests.
- Fail rollback after one or more changes already succeeded; the run must record exactly what changed, what was restored, and what remains unresolved before escalation. Covered in Tasks 2 and 3.
- Generate reminder storms, retry deliveries, or race two workers; each recipient/channel/event is deduplicated and tenant isolated. Covered in Tasks 4 and 5.
- Enable none of the optional integrations and run registration, ticketing, direct-device help, OIDC, and approvals; no Slack, Teams, Freshdesk mock, or headless-fixture egress occurs. Covered in Task 8.

---

### Task 1: Add V7-compatible state without migration collisions

**Files:**
- Create: `apps/api/migrations/versions/0010_verification_rollback_lease.py`
- Create: `apps/api/migrations/versions/0011_rollback_availability.py`
- Create: `apps/api/migrations/versions/0012_approval_notifications.py`
- Create: `apps/api/migrations/versions/0013_external_approval_dispatch.py`
- Modify: `apps/api/joltiq/models/operations.py`
- Modify: `apps/api/joltiq/models/platform.py`
- Modify: `apps/api/joltiq/models/__init__.py`
- Create: `apps/api/tests/test_v7_migrations.py`

**Interfaces:**
- Produces `VerificationRun.kind: str`, `VerificationRun.evidence_hash: str | None`, `AgentPlan.rollback_steps: list[dict]`, `AgentPlan.rollback_availability: dict`, `SupportSession.lease_epoch: int`, and `ComputerUseRun.rollback: dict`.
- Produces tenant-scoped `NotificationDispatch` with nullable `user_id`, unique `(org_id, dedupe_key)`, `kind`, `approval_id`, `channels`, and `suppressed`.
- Migration chain is exactly `0009 -> 0010 -> 0011 -> 0012 -> 0013`.

- [ ] **Step 1: Write failing upgrade/model tests**

Add tests named `test_v7_migrations_follow_oidc_head`, `test_v7_upgrade_preserves_existing_oidc_device_integration_and_ticket_rows`, `test_v7_new_columns_have_backward_compatible_defaults`, `test_notification_dispatch_is_rls_isolated`, and `test_notification_dispatch_dedupe_is_per_org`. Assert upgrade from the current `0009` schema, preserved row counts/IDs, `kind == "verification"`, empty rollback values, `lease_epoch == 0`, and cross-tenant read/write refusal.

- [ ] **Step 2: Run the migration tests and confirm RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_v7_migrations.py`

Expected: fail because migrations/models do not exist.

- [ ] **Step 3: Implement the four forward-only migrations and matching models**

Adapt V7's `0009`-`0012` schema changes to revisions `0010`-`0013`. Preserve current model fields and RLS policy style. Do not import V7 downgrade behavior that deletes customer data; downgrades may remove only newly introduced columns/tables.

- [ ] **Step 4: Run migration, model, OIDC, and tenancy tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_v7_migrations.py tests/test_oidc_models.py tests/test_tenancy_rls.py`

Expected: pass with the upgrade and cross-tenant assertions intact.

- [ ] **Step 5: Commit**

```bash
git add apps/api/migrations/versions/0010_verification_rollback_lease.py apps/api/migrations/versions/0011_rollback_availability.py apps/api/migrations/versions/0012_approval_notifications.py apps/api/migrations/versions/0013_external_approval_dispatch.py apps/api/joltiq/models/operations.py apps/api/joltiq/models/platform.py apps/api/joltiq/models/__init__.py apps/api/tests/test_v7_migrations.py
git commit -m "feat: add verification rollback and dispatch state"
```

### Task 2: Port independent verification and rollback derivation

**Files:**
- Create: `apps/api/joltiq/services/rollback.py`
- Modify: `apps/api/joltiq/services/verification.py`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/api/joltiq/services/agent.py`
- Modify: `apps/api/joltiq/services/executor.py`
- Create: `apps/api/tests/test_verification_rollback.py`

**Interfaces:**
- Produces `derive(steps: list[dict], channel: str) -> tuple[list[dict], dict[str, Any]]`.
- Produces `plan_hash(base_hash: str, rollback_steps: list[dict], avail: dict | None = None) -> str`.
- Produces `capture_pre_state(...)`, `start(...)`, `seal(...)`, and `recompute_evidence(...)` in `services.verification` using canonical evidence hashes.
- Consumes current action registry metadata; no AI-generated rollback instruction is executable unless the registry defines it.

- [ ] **Step 1: Port and adapt failing V7 tests**

Add tests for deterministic browser rollback derivation, device rollback referring only to a local journal entry, unsupported action availability, exact plan-hash binding, pre-state capture, evidence tampering, critical-check aggregation, and false-success refusal. Add `test_partial_rollback_reports_changed_restored_and_unresolved_items` and `test_rollback_never_accepts_server_supplied_old_registry_value` from the approved safety requirements.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_verification_rollback.py`

Expected: fail because rollback derivation/evidence sealing is absent.

- [ ] **Step 3: Implement minimal derivation and evidence services**

Port algorithms into current services, preserving current postcondition types. Device restore steps identify the earlier change/journal command and never carry arbitrary old values from the server. Rollback availability is calculated before approval and included in the plan hash.

- [ ] **Step 4: Wire the plan/executor state transitions**

The executor stores pre-state before mutation, seals verification evidence, starts rollback only after a failed critical verification or explicit authorized request, and ends with one of `ROLLED_BACK`, `ROLLBACK_FAILED`, `ROLLBACK_BLOCKED`, `ROLLBACK_UNVERIFIED`, or `NO_ROLLBACK_AVAILABLE` without rewriting the original action result.

- [ ] **Step 5: Run focused and existing execution tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_verification_rollback.py tests/test_procedures.py tests/test_e2e_browser.py tests/test_e2e_desktop.py tests/test_computer_use.py`

Expected: pass, including existing verified/customer-confirmed outcome distinctions.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/rollback.py apps/api/joltiq/services/verification.py apps/api/joltiq/services/actions/registry.py apps/api/joltiq/services/agent.py apps/api/joltiq/services/executor.py apps/api/tests/test_verification_rollback.py
git commit -m "feat: add verified rollback orchestration"
```

### Task 3: Enforce browser/device lease epochs and STOP-safe rollback

**Files:**
- Modify: `services/browser-worker/worker/app.py`
- Create: `services/browser-worker/tests/test_lease_ws.py`
- Modify: `services/browser-worker/tests/test_upload.py`
- Modify: `apps/api/joltiq/services/browser_client.py`
- Modify: `apps/api/joltiq/services/sessions.py`
- Modify: `apps/api/joltiq/services/devices.py`
- Modify: `apps/api/joltiq/services/computer_use.py`
- Create: `apps/api/tests/test_device_rollback.py`
- Modify: `apps/api/tests/test_security.py`

**Interfaces:**
- Browser worker accepts `POST /sessions/{sid}/lease` with `{epoch, controller}` and requires a non-stale epoch for actions, uploads, and human input once a lease has been established.
- `BrowserClient.set_lease(session_id: UUID, epoch: int, controller: str) -> dict` and all action/upload calls carry the current epoch.
- Device rollback commands name the local journal entry to restore and remain signed, expiring, idempotent, and bound to the enrolled/logged-in user.

- [ ] **Step 1: Write failing lease, websocket, STOP, and device rollback tests**

Cover stale/missing/future epoch behavior, customer/technician/AI handoff, WebSocket allowlist enforcement, upload epoch binding, duplicate results, lost connection, STOP before action, STOP during action, STOP during rollback, local-journal-missing refusal, enrolled-user mismatch, and partial-change reporting.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd services/browser-worker && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q tests/test_lease_ws.py tests/test_upload.py && cd ../../apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_device_rollback.py tests/test_security.py`

Expected: fail because current browser actions lack lease epochs and device rollback orchestration is absent.

- [ ] **Step 3: Implement browser lease enforcement**

Port V7's monotonic epoch check and WebSocket route guard. Legacy no-epoch calls are accepted only before any epoch is established. Never add `unsafe-eval` or weaken the production domain allowlist.

- [ ] **Step 4: Implement API lease propagation and device rollback commands**

Increment/push the epoch transactionally on control transfer or STOP. Capture journal identifiers before the change, issue restore-by-reference only, and require independent read-back verification after restore.

- [ ] **Step 5: Run browser, device, direct-help, and security regressions**

Run: `cd services/browser-worker && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q && cd ../../apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_device_protocol.py tests/test_device_rollback.py tests/test_security.py tests/test_computer_use.py tests/test_e2e_browser.py tests/test_e2e_desktop.py`

Expected: pass with stale controllers refused and all existing consent/STOP behavior green.

- [ ] **Step 6: Commit**

```bash
git add services/browser-worker/worker/app.py services/browser-worker/tests/test_lease_ws.py services/browser-worker/tests/test_upload.py apps/api/joltiq/services/browser_client.py apps/api/joltiq/services/sessions.py apps/api/joltiq/services/devices.py apps/api/joltiq/services/computer_use.py apps/api/tests/test_device_rollback.py apps/api/tests/test_security.py
git commit -m "feat: bind computer control to lease epochs"
```

### Task 4: Add approval reminders and notification preferences

**Files:**
- Create: `apps/api/joltiq/services/approval_notify.py`
- Modify: `apps/api/joltiq/services/approvals.py`
- Modify: `apps/api/joltiq/services/notifications.py`
- Modify: `apps/api/joltiq/routers/admin.py`
- Modify: `apps/api/joltiq/worker.py`
- Create: `apps/api/tests/test_approval_notifications.py`

**Interfaces:**
- `eligible_approvers(db: Session, approval: Approval) -> list[UUID]` excludes the requester and returns only active same-org users with an allowed role.
- `on_requested(...)`, `send_reminders(...)`, and `on_outcome(...)` use `NotificationDispatch.dedupe_key` for atomic at-most-once dispatch per event/recipient/channel.
- `GET/PUT /admin/approval-notifications` manages validated timing/channel settings; user notification preferences remain separate.

- [ ] **Step 1: Port failing notification tests and add race cases**

Cover eligible roles, requester exclusion, tenant isolation, initial alert, reminder timing, expiry, outcome, user preference suppression, disabled channels, quiet duplicate retries, two-worker race, no-recipient behavior, invalid settings, audit trail, and no external calls when integrations are absent.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_approval_notifications.py`

Expected: fail because approval notification dispatch does not exist.

- [ ] **Step 3: Implement deduplicated in-app/email notification flow**

Use database uniqueness as the final race barrier. An email failure records the channel result without suppressing in-app delivery or approval processing. Worker retries must reuse the same dedupe key.

- [ ] **Step 4: Add settings endpoints and scheduler hook**

Store the validated settings in the existing organization settings document; default behavior remains compatible and channels are off unless configured.

- [ ] **Step 5: Run notification, approval, auth, and tenancy regressions**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_approval_notifications.py tests/test_platform_features.py tests/test_auth.py tests/test_tenancy_rls.py`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/approval_notify.py apps/api/joltiq/services/approvals.py apps/api/joltiq/services/notifications.py apps/api/joltiq/routers/admin.py apps/api/joltiq/worker.py apps/api/tests/test_approval_notifications.py
git commit -m "feat: add approval notification workflow"
```

### Task 5: Add Slack and Teams approval connectors safely

**Files:**
- Create: `apps/api/joltiq/services/slack.py`
- Create: `apps/api/joltiq/services/teams.py`
- Modify: `apps/api/joltiq/services/integrations.py`
- Modify: `apps/api/joltiq/services/approval_notify.py`
- Modify: `apps/api/joltiq/routers/integrations.py`
- Modify: `apps/api/joltiq/config.py`
- Create: `apps/api/tests/test_slack_alerts.py`
- Create: `apps/api/tests/test_teams_alerts.py`
- Create: `services/mock-slack/`
- Create: `services/mock-teams/`
- Modify: `docker-compose.yml`

**Interfaces:**
- Slack and Teams configs store encrypted webhook URLs/secrets and expose redacted status only.
- `slack.validate_webhook_url(url: str) -> None` and `teams.validate_webhook_url(url: str) -> None` accept only approved HTTPS hosts; labelled development mocks require explicit mock-base settings.
- `fanout(db: Session, approval: Approval, kind: str) -> list[str]` records channel outcomes through the existing dispatch row and never blocks core approval processing.

- [ ] **Step 1: Port failing connector tests and add adversarial URLs**

Cover connect/test/update/disconnect, encrypted secret redaction, approval-request/reminder/outcome event selection, retry/dedupe, wrong tenant, 429/5xx handling, timeout, credential-bearing URL, HTTP URL, alternate port, encoded host/path, DNS/private-IP rejection where applicable, absent config zero egress, and visible `MOCK` labels.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_slack_alerts.py tests/test_teams_alerts.py`

Expected: fail because services/routes do not exist.

- [ ] **Step 3: Implement connector services and admin routes**

Use bounded `httpx` timeouts, no redirects, existing credential encryption, destination validation, redacted errors, event allowlists, and best-effort delivery. Keep both connectors disabled until an admin explicitly connects and tests them.

- [ ] **Step 4: Add labelled local mock services and Compose profile**

Mocks run only under `dev-mock`, use fixed non-production credentials, render a permanent `MOCK` banner, and are excluded from production manifests.

- [ ] **Step 5: Run connector, SSRF, integration, and compose tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_slack_alerts.py tests/test_teams_alerts.py tests/test_security.py tests/test_platform_features.py && cd ../.. && docker compose config --quiet`

Expected: pass with zero external calls while unset.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/slack.py apps/api/joltiq/services/teams.py apps/api/joltiq/services/integrations.py apps/api/joltiq/services/approval_notify.py apps/api/joltiq/routers/integrations.py apps/api/joltiq/config.py apps/api/tests/test_slack_alerts.py apps/api/tests/test_teams_alerts.py services/mock-slack services/mock-teams docker-compose.yml
git commit -m "feat: add Slack and Teams approval alerts"
```

### Task 6: Add development-only headless and Freshdesk fixtures

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Headless/`
- Modify: `apps/windows-agent/JoltIQ.Agent.sln`
- Create: `apps/windows-agent/Dockerfile.headless`
- Create: `docs/runbooks/companion-headless.md`
- Create: `services/mock-freshdesk/`
- Modify: `docker-compose.yml`
- Create: `apps/api/tests/test_headless_fixture_contract.py`
- Create: `services/mock-freshdesk/tests/test_adapter_contract.py`

**Interfaces:**
- Headless companion uses the current signed protocol client and a fixed development-only UI/OS adapter; it has no production installer target and cannot be enabled when `JOLTIQ_ENV=production`.
- Mock Freshdesk implements only the API subset called by current JoltIQ, advertises `MOCK`, uses fixed dev credentials, and runs only under `dev-mock`.

- [ ] **Step 1: Write failing fixture-boundary tests**

Assert production refusal, explicit profile requirement, fixed mock identity, no host input path, signed/idempotent command handling, visible MOCK markers, Freshdesk adapter compatibility, and no service start in the default production-shaped profile.

- [ ] **Step 2: Run tests and confirm RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_headless_fixture_contract.py && cd ../../services/mock-freshdesk && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q tests/test_adapter_contract.py`

Expected: fail because fixtures do not exist.

- [ ] **Step 3: Port the headless companion against current protocol assemblies**

Do not copy V7 protocol/crypto code over current code. Reference current Core assemblies and keep simulated OS responses deterministic and explicitly non-native.

- [ ] **Step 4: Port the minimal Freshdesk mock and Compose wiring**

The mock must never be cited as live Freshdesk acceptance and must not broaden production egress settings.

- [ ] **Step 5: Run .NET, mock, compose, and current Freshdesk contract tests**

Run: `dotnet build apps/windows-agent/src/JoltIQ.Agent.Headless/JoltIQ.Agent.Headless.csproj && dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest && cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_headless_fixture_contract.py tests/test_platform_features.py && cd ../../services/mock-freshdesk && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q && cd ../.. && docker compose config --quiet`

Expected: pass; if the host lacks a required .NET workload, record the exact external block and run the existing Core self-test instead without claiming full .NET acceptance.

- [ ] **Step 6: Commit**

```bash
git add apps/windows-agent/src/JoltIQ.Agent.Headless apps/windows-agent/JoltIQ.Agent.sln apps/windows-agent/Dockerfile.headless docs/runbooks/companion-headless.md services/mock-freshdesk docker-compose.yml apps/api/tests/test_headless_fixture_contract.py
git commit -m "feat(dev): add labelled support fixtures"
```

### Task 7: Merge V7 UI improvements without regressing current features

**Files:**
- Create: `apps/web/src/components/ui/`
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/src/components/DrivePanel.tsx`
- Modify: `apps/web/src/components/RunPanel.tsx`
- Modify: `apps/web/src/components/TicketList.tsx`
- Modify: `apps/web/src/components/TicketView.tsx`
- Create: `apps/web/src/components/ApprovalCard.tsx`
- Create: `apps/web/src/app/(app)/admin/approval-notifications/page.tsx`
- Modify: `apps/web/src/app/(app)/admin/integrations/page.tsx`
- Modify: `apps/web/src/app/(app)/admin/knowledge/page.tsx`
- Modify: `apps/web/src/app/(app)/admin/policies/page.tsx`
- Modify: `apps/web/src/app/(app)/approvals/page.tsx`
- Modify: `apps/web/src/app/(app)/approvals/[id]/page.tsx`
- Modify: `apps/web/src/app/(app)/console/runs/page.tsx`
- Create: `apps/web/src/app/(app)/console/runs/[id]/page.tsx`
- Modify: `apps/web/src/app/(auth)/login/page.tsx`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Create: `apps/web/e2e/v7-integration.spec.ts`

**Interfaces:**
- UI consumes the current API routes from Tasks 2-5 and retains `/admin/security`, work-email SSO, SSO MFA, direct-device help, Guide/Fix controls, and remote-support workspace.
- Role-filtered navigation remains centralized in `AppShell`; V7 must not introduce a competing admin layout.
- Approval cards show risk, exact action, expiry, rollback availability, and confirmation before approve/deny.

- [ ] **Step 1: Write failing integration UI tests**

Cover role-filtered navigation, SSO controls, organization-security link, queue/ticket timeline, approval risk/undo copy and confirmation, run verification/rollback evidence, policy sentence builder, approval notification settings, Slack/Teams MOCK labels, knowledge pages, direct-device Guide/Fix controls, and inaccessible-role route handling.

- [ ] **Step 2: Run UI tests and confirm RED**

Run: `cd apps/web && npx playwright test e2e/v7-integration.spec.ts`

Expected: fail because V7 components/pages are not on the current branch.

- [ ] **Step 3: Port the shared UI primitives**

Adopt V7 primitives only where they replace current `ui.tsx` behavior without losing accessibility names or test selectors. Keep changes mechanical before changing page behavior.

- [ ] **Step 4: Merge pages one functional group at a time**

Merge approvals/runs first, then admin settings/integrations/policies/knowledge, then queue/tickets. Resolve each page against current API types rather than copying V7's older types.

- [ ] **Step 5: Run typecheck, production build, and all web tests**

Run: `cd apps/web && npm run typecheck && npm run build && npx playwright test`

Expected: typecheck/build pass; tests requiring live seeded credentials may skip only when explicitly documented, while all local non-live tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/web
git commit -m "feat(web): integrate V7 support experience"
```

### Task 8: Close static, dependency, harness, and release gates

**Files:**
- Modify: `apps/api/joltiq/services/ai/computer.py`
- Modify: files from Tasks 2-5 that still fail MyPy
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `apps/web/playwright.config.ts`
- Modify: `apps/web/next.config.mjs`
- Modify: `apps/web/scripts/check-remote-csp.mjs`
- Create: `apps/web/e2e/flows.spec.ts`
- Create: `apps/web/e2e/smoke.spec.ts`
- Create: `apps/web/e2e/approval-notifications.spec.ts`
- Create: `apps/web/e2e/slack-and-settings.spec.ts`
- Create: `apps/web/e2e/teams.spec.ts`
- Create: `apps/api/tests/test_v7_disabled_egress.py`
- Modify: `PROJECT_STATUS.md`
- Modify: `README.md`
- Modify: `RELEASE_CHECKLIST.md`
- Modify: `docs/guides/admin.md`
- Create: `docs/evidence/v7-integration-acceptance.md`

**Interfaces:**
- Produces a green full-tree MyPy/Ruff gate without ignores that hide Task 2-5 errors.
- Produces a Playwright configuration that uses `SMOKE_BASE_URL` as the tested server and does not start a conflicting dev server when an external production server is supplied.
- Produces an audited dependency resolution with no untriaged high-severity production advisory.

- [ ] **Step 1: Add regression tests for the test harness and disabled egress**

Extend the config check to assert external `SMOKE_BASE_URL` disables the fallback dev server. Add `test_v7_disabled_egress.py` with fixture counters for Slack, Teams, Freshdesk mock, and headless endpoints while all are unset; assert zero calls through registration, ticket, approval, direct-device, and OIDC flows. Port V7's flow, smoke, approval-notification, Slack, and Teams Playwright cases without weakening their assertions.

- [ ] **Step 2: Run the new gates and confirm current failures**

Run: `cd apps/web && npm run test:config && npm audit --omit=dev --audit-level=high`

Run: `cd apps/api && .venv/bin/mypy joltiq`

Expected before fixes: MyPy reports the known current/V7 errors and npm audit reports the Zod advisory.

- [ ] **Step 3: Fix MyPy errors without weakening configuration**

Correct nullable narrowing, typed collections, `Turn` construction, model selection, and sequence/list assignments. Do not change runtime authority or action semantics merely to satisfy types.

- [ ] **Step 4: Remediate Zod and correct Playwright/CSP mode handling**

Upgrade Zod through the smallest compatible version path, adapt code/tests for any API change, and keep production CSP strict. The fallback development server may receive only the development behavior required by React; production headers remain unchanged.

- [ ] **Step 5: Run the full acceptance matrix**

Run:

```bash
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
.venv/bin/ruff check joltiq tests
.venv/bin/mypy joltiq
cd ../web && npm run typecheck && npm run build && npm run test:config
npm audit --omit=dev --audit-level=high
npx playwright test
cd ../../services/browser-worker && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q
cd ../../apps/windows-agent && dotnet run --project tests/JoltIQ.Agent.Core.SelfTest
cd ../.. && docker compose config --quiet
```

Expected: all local non-live gates pass; only explicitly credential/VM-dependent cases skip or remain externally blocked.

- [ ] **Step 6: Run seeded local scenario acceptance**

Start the production-mode local stack and labelled mocks, seed a pending approval, then run the complete local Playwright suite with Mailpit/Slack/Teams credentials. Exercise registration, queue/timeline, local model, policy builder, approval/deny, reminder, rollback/escalation, and companion-headless online status. Save exact commands and results.

- [ ] **Step 7: Update honest status/evidence**

Document pass counts, mock/live labels, the exact source commit, remaining Windows VM/signing/installer/security/live-connector blockers, and confirm that no existing OIDC/direct-device/remote-support feature was removed.

- [ ] **Step 8: Commit**

```bash
git add apps/api/joltiq/services/ai/computer.py apps/api/joltiq/services/slack.py apps/api/joltiq/services/approval_notify.py apps/api/joltiq/services/verification.py apps/api/joltiq/services/rollback.py apps/api/joltiq/services/agent.py apps/api/joltiq/routers/admin.py apps/api/tests/test_v7_disabled_egress.py apps/web/package.json apps/web/package-lock.json apps/web/playwright.config.ts apps/web/next.config.mjs apps/web/scripts/check-remote-csp.mjs apps/web/e2e/flows.spec.ts apps/web/e2e/smoke.spec.ts apps/web/e2e/approval-notifications.spec.ts apps/web/e2e/slack-and-settings.spec.ts apps/web/e2e/teams.spec.ts PROJECT_STATUS.md README.md RELEASE_CHECKLIST.md docs/guides/admin.md docs/evidence/v7-integration-acceptance.md
git commit -m "chore: close V7 integration release gates"
```
