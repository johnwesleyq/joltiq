# Freshdesk Integration Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Freshdesk polling adapter into a reliable, webhook-assisted two-way integration without changing JoltIQ's ticket authority, resolution truth model or other integrations.

**Architecture:** Keep the current `Integration`, `SyncJob` and `Freshdesk` service as the foundation. Add a separate webhook secret, durable ticket-link state and conflict metadata. Webhooks are authenticated hints only: workers re-fetch the authoritative ticket, reject stale snapshots and apply tenant-scoped mappings. Outbound updates follow JoltIQ's outcome stages, retry safely and surface conflicts for explicit resolution.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL RLS, existing Redis-backed jobs, httpx, Next.js/TypeScript, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-28-selective-integration-port-design.md`

## Global Constraints

- Preserve every current integration row and behavior through migration.
- A webhook body is never authoritative and may not directly update a ticket.
- The webhook signing secret is independent from Freshdesk API credentials, encrypted at rest and displayed once.
- Only `RESOLVED_VERIFIED` may auto-resolve a Freshdesk ticket. Customer-confirmed but unverified outcomes remain Pending with an explanatory note.
- Explicitly cleared conflicts use JoltIQ/local-wins; unresolved conflicts never silently overwrite either side.
- Connector failure cannot stop local ticket/device/AI processing.
- The mock Freshdesk service is labelled development/test only.

## Review Focus

- Forged, missing, replayed and cross-tenant webhook requests cannot enqueue or mutate work; covered in Task 2.
- Out-of-order deliveries and stale API snapshots cannot roll tickets backward; covered in Tasks 2 and 3.
- Verified versus customer-confirmed outcomes map differently and visibly; covered in Task 3.
- Rate limits honor `Retry-After`; duplicate jobs stay idempotent; exhausted jobs become manually retryable; covered in Task 3.
- Other current integration types are unchanged; covered in Task 6.

---

### Task 1: Add durable Freshdesk link and webhook state

**Files:**
- Create: `apps/api/alembic/versions/0010_freshdesk_hardening.py`
- Modify: `apps/api/joltiq/models/platform.py`
- Modify: `apps/api/joltiq/models/__init__.py`
- Create: `apps/api/tests/test_freshdesk_models.py`

**Interfaces:**
- Produces: `FreshdeskTicketLink` with `org_id`, `integration_id`, `ticket_id`, `external_ticket_id`, `remote_updated_at`, `last_inbound_hash`, `last_outbound_hash`, `conflict_state`, `conflict_payload`, `last_synced_at`.
- Adds: `Integration.webhook_credential_id: UUID | None` referencing a separate encrypted `Credential`.
- Constraints: unique `(integration_id, external_ticket_id)` and `(integration_id, ticket_id)`; tenant RLS; webhook credential cannot equal API credential.

- [ ] **Step 1: Write failing migration/model tests**

Migrate a fixture containing active Freshdesk and non-Freshdesk integration rows from the preceding migration head. Assert all rows/configuration remain, new fields default null/disabled, RLS blocks cross-org access and duplicate links are rejected.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_models.py`

Expected: fail because the model/migration does not exist.

- [ ] **Step 3: Implement the migration and models**

Create only additive tables/columns. Store conflict payloads as bounded structured metadata without attachment bodies, full conversations or secrets. Index pending conflicts and external IDs.

- [ ] **Step 4: Run migration tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_models.py tests/test_tenancy_rls.py`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0010_freshdesk_hardening.py apps/api/joltiq/models/platform.py apps/api/joltiq/models/__init__.py apps/api/tests/test_freshdesk_models.py
git commit -m "feat(freshdesk): add durable ticket sync state"
```

### Task 2: Authenticate webhook hints and re-fetch authoritative tickets

**Files:**
- Modify: `apps/api/joltiq/routers/integrations.py`
- Modify: `apps/api/joltiq/services/integrations.py`
- Create: `apps/api/tests/test_freshdesk_webhooks.py`

**Interfaces:**
- `POST /integrations/freshdesk/{integration_id}/webhook` accepts raw bytes plus `X-JoltIQ-Signature` and returns `202` for an authenticated hint.
- `verify_freshdesk_webhook(raw: bytes, signature: str, secret: bytes, now: datetime) -> WebhookHint` uses constant-time HMAC verification, bounded timestamp skew and a replay key.
- `enqueue_freshdesk_refresh(db, org_id, integration_id, external_ticket_id, hint_hash) -> UUID`
- `refresh_freshdesk_ticket(db, integration_id, external_ticket_id) -> SyncResult` fetches through `Freshdesk.get_ticket(...)` before any local mutation.

- [ ] **Step 1: Write failing webhook security tests**

Cover missing/bad signature, altered body, old/future timestamp, duplicate delivery, unknown integration, disabled/revoked integration, oversized body, malformed JSON, cross-org external ID and same external ID under two tenants. Assert invalid requests enqueue nothing and reveal no tenant existence.

- [ ] **Step 2: Write failing authoritative-refresh tests**

Assert the body cannot change local state, a valid hint performs a GET, deleted/not-found remote tickets are handled, contact/customer mapping is org-scoped, and older `updated_at` snapshots are ignored.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_webhooks.py`

Expected: fail because the endpoint and refresh service do not exist.

- [ ] **Step 4: Implement one-time webhook-secret creation and verification**

Generate at least 256 random bits, display plaintext once on creation/rotation, encrypt with the existing credential facility, compare HMACs in constant time and write audit/security events without body or secret content.

- [ ] **Step 5: Implement idempotent refresh jobs**

Use the current durable job handler registry. Deduplicate by integration/external ticket/hint hash, re-fetch using existing Freshdesk credentials, lock the link row, compare authoritative timestamps and apply only a newer snapshot. Polling and webhook refresh must call the same reconciliation function.

- [ ] **Step 6: Run tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_webhooks.py tests/test_platform_features.py`

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/routers/integrations.py apps/api/joltiq/services/integrations.py apps/api/tests/test_freshdesk_webhooks.py
git commit -m "feat(freshdesk): authenticate webhook refresh hints"
```

### Task 3: Enforce outcome-aware outbound sync, conflict handling and retries

**Files:**
- Modify: `apps/api/joltiq/services/integrations.py`
- Modify: `apps/api/joltiq/services/jobs.py`
- Modify: `apps/api/joltiq/routers/integrations.py`
- Create: `apps/api/tests/test_freshdesk_reconciliation.py`

**Interfaces:**
- `reconcile_freshdesk_link(db, link, remote: FreshdeskTicketSnapshot, source: Literal["poll","webhook","outbound"]) -> ReconciliationResult`
- `on_ticket_event(db, ticket, kind, data)` consumes the existing event plus `outcome_stage` and verification metadata.
- `POST /admin/integrations/{id}/conflicts/{link_id}/clear` requires org admin + MFA and applies explicit local-wins on the next outbound job.
- `FreshdeskRateLimited.retry_after_seconds: int` is consumed by the durable job scheduler rather than generic exponential delay.

- [ ] **Step 1: Write a failing outcome mapping matrix**

Test every local ticket/outcome stage. Assert `RESOLVED_VERIFIED` posts a verification note then resolves; customer-confirmed/unverified posts a public note and leaves Pending; waiting/approval/failed/escalated states post appropriate private progress notes without false resolution.

- [ ] **Step 2: Write failing concurrency/retry tests**

Cover simultaneous local/remote edits, equivalent edits, explicit local-wins clear, remote-newer conflict, no timestamp, duplicate outbound event, 429 numeric/date `Retry-After`, transient 5xx, permanent 4xx, lost response after remote success, maximum attempts and manual retry.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_reconciliation.py`

Expected: fail because the new semantics are absent.

- [ ] **Step 4: Implement the pure reconciliation decision first**

Keep decision logic separate from HTTP writes so the matrix is deterministic. Persist hashes/timestamps before enqueueing follow-up work. When both sides changed after the common base, record a conflict and stop automatic writes.

- [ ] **Step 5: Implement exact outcome and note mapping**

Use existing ticket outcome fields, not AI text inference. Include what was attempted and verification status, but filter internal secrets/device data through current redaction helpers. Never use a private note to claim customer-visible resolution.

- [ ] **Step 6: Implement rate-limit and terminal failure behavior**

Parse bounded `Retry-After`, schedule at that time, preserve idempotency keys/hashes and mark exhausted/permanent failures `dead` with a safe admin-visible reason. Manual retry creates a new attempt without deleting history.

- [ ] **Step 7: Run focused and job tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_freshdesk_reconciliation.py tests/test_platform_features.py`

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/joltiq/services/integrations.py apps/api/joltiq/services/jobs.py apps/api/joltiq/routers/integrations.py apps/api/tests/test_freshdesk_reconciliation.py
git commit -m "feat(freshdesk): reconcile verified outcomes safely"
```

### Task 4: Add development-only mock Freshdesk service

**Files:**
- Create: `services/mock-freshdesk/Dockerfile`
- Create: `services/mock-freshdesk/app.py`
- Create: `services/mock-freshdesk/requirements.txt`
- Create: `services/mock-freshdesk/README.md`
- Modify: `docker-compose.yml`
- Create: `apps/api/tests/integration/test_mock_freshdesk.py`

**Interfaces:**
- Dev-only API implements the subset used by JoltIQ: get/update ticket, add public/private note, contact lookup and controllable 429/5xx/stale-response scenarios.
- Compose profile: `test-connectors`; service is not started in default or production profiles.

- [ ] **Step 1: Write a failing black-box contract test**

Exercise the exact existing `Freshdesk` client against the mock, including auth, notes, status, stale snapshots, rate limits and lost-response simulation.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/integration/test_mock_freshdesk.py`

Expected: skip/fail because the service is absent.

- [ ] **Step 3: Implement the smallest contract fixture**

Use deterministic in-memory state with reset/scenario endpoints available only inside the test profile. Label every UI/readme/response `DEVELOPMENT TEST FIXTURE — NOT FRESHDESK`.

- [ ] **Step 4: Run the contract suite under the profile**

Run: `docker compose --profile test-connectors up -d --build mock-freshdesk && cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/integration/test_mock_freshdesk.py`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add services/mock-freshdesk docker-compose.yml apps/api/tests/integration/test_mock_freshdesk.py
git commit -m "test(freshdesk): add labelled connector fixture"
```

### Task 5: Add admin webhook, health and conflict UI

**Files:**
- Modify: `apps/web/src/app/(app)/admin/integrations/page.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/e2e/freshdesk-integration.spec.ts`

**Interfaces:**
- Freshdesk card shows webhook URL, one-time secret creation/rotation, last webhook, last poll, queue/dead-job counts and conflicts.
- Conflict control displays both safe summaries and requires MFA-backed explicit `Keep JoltIQ version`.
- Test/mock endpoints are visibly labelled and hidden outside development.

- [ ] **Step 1: Write failing UI tests**

Cover secret one-time display, copy/acknowledge, rotate warning, no secret on reload, health states, manual retry, conflict clear and verified/customer-confirmed status explanations.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/web && npx playwright test e2e/freshdesk-integration.spec.ts`

Expected: fail because controls do not exist.

- [ ] **Step 3: Implement the UI using existing components**

Do not add a second integrations page or expose raw conflict payloads/secrets. Keep other integration cards unchanged.

- [ ] **Step 4: Run web checks**

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add 'apps/web/src/app/(app)/admin/integrations/page.tsx' apps/web/src/lib/api.ts apps/web/e2e/freshdesk-integration.spec.ts
git commit -m "feat(web): expose Freshdesk sync health"
```

### Task 6: Freshdesk acceptance and non-regression evidence

**Files:**
- Modify: `docs/guides/admin.md`
- Create: `docs/evidence/freshdesk-acceptance.md`
- Modify: `.env.example`

**Interfaces:**
- Produces: setup/rotation/runbook, support matrix, failure recovery and evidence table.

- [ ] **Step 1: Run complete API verification**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q`

Expected: all tests pass except explicit external-service skips.

- [ ] **Step 2: Run static and web verification**

Run: `cd apps/api && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq`

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: pass.

- [ ] **Step 3: Run mock acceptance matrix**

Record webhook, poll fallback, stale/replay, customer-confirmed, verified closure, conflict, 429, 5xx, dead/manual retry and connector-disabled cases. Verify the disabled/default stack makes zero Freshdesk calls.

- [ ] **Step 4: Run real Freshdesk sandbox acceptance only when supplied**

Use a dedicated sandbox ticket/contact and save redacted request IDs, ticket final state and JoltIQ audit evidence. Otherwise record `BLOCKED — sandbox not supplied`; never relabel mock results as live Freshdesk proof.

- [ ] **Step 5: Document setup and operations**

Include webhook configuration, secret rotation overlap policy, polling cadence, conflict procedure, outcome mapping, Retry-After behavior and privacy/redaction limits.

- [ ] **Step 6: Commit**

```bash
git add docs/guides/admin.md docs/evidence/freshdesk-acceptance.md .env.example
git commit -m "docs: record Freshdesk hardening acceptance"
```
