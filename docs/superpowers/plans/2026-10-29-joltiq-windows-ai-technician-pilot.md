# JoltIQ Windows AI Technician Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish one coherent customer-hosted Windows 11 pilot in which JoltIQ answers from organization knowledge, guides without controlling input, safely diagnoses and cleans a slow PC with approval, completes one browser/SaaS repair, verifies results, and escalates honestly.

**Architecture:** Extend the existing FastAPI/PostgreSQL knowledge and agent services, signed companion protocol, Windows tray application, typed action registry, verifier, browser worker, and Next.js portal. Organization-aware Ask remains chat-only; device work uses canonical typed plans, exact local consent, device-local inventory tokens, policy approval, independent read-back, and customer-visible evidence. The plan adds no alternate execution path and does not replace the current system.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/PostgreSQL + pgvector, Redis jobs, Next.js/TypeScript, .NET 8 WinForms, xUnit, Docker Compose, Playwright, WiX v5.

**Spec:** `docs/superpowers/specs/2026-10-29-joltiq-ai-technician-product-design.md`

## Global Constraints

- Reuse the current tenant/RLS, knowledge, ticket, model-router, Ask, Guide/Fix, approval, signed protocol, Windows companion, browser worker, verification, audit, billing, and MeshCentral components; do not rewrite them.
- The first pilot target is customer-hosted Windows 11 64-bit; Windows 10 and macOS are not pilot-supported.
- Ordinary Ask is chat-only, Guide has no input authority, and Fix requires exact-goal local consent plus policy/step approval.
- Knowledge, tickets, websites, screenshots, and model output are untrusted data and cannot create executable authority.
- Company policy/procedures govern company choices; live evidence governs current state; authoritative vendor material governs technical facts; unresolved conflict is surfaced and escalated.
- Pilot knowledge authorization uses existing JoltIQ role/document visibility. Source-native per-user ACL synchronization is out of scope and must not be marketed.
- Cleanup never accepts a server-supplied path. The PC resolves a short-lived device-local inventory ID to exact locally discovered entries.
- Cleanup always excludes source files, Git metadata, documents, Downloads, browser profiles, credentials, Trash/Recycle Bin, complete projects, unknown paths, active caches, Docker/Colima data, databases, and VM disks.
- Deleting rebuildable cache/build output is non-rollbackable, must be disclosed before approval, and never runs automatically after a diagnostic retry.
- No secret, API key, credential, private source excerpt, or unrestricted filesystem path may appear in logs, model telemetry, test fixtures, or audit summaries.
- Preserve unrelated working-tree changes and untracked files.

## Review Focus

- A retrieved article or ticket containing prompt-injection text must be quoted as untrusted context, permission-filtered, cited, and structurally unable to start a plan (Task 1 tests).
- A consent timeout, denial, terminated session, or failed run must clear stale “Waiting for approval” state and permit one clean retry without duplicate prompts (Task 2 tests).
- Junctions, symlinks/reparse points, traversal, changed files, and paths outside fixed/configured roots must never enter or escape a cleanup inventory (Task 4 tests).
- Running applications, locked files, unknown caches, missing manifests, active databases/VMs, and a project root itself must be skipped and reported rather than deleted (Task 4 tests).
- Disconnect, Stop, duplicate delivery, partial deletion, expired inventory, or verification failure must produce one idempotent terminal report with exact retained/removed items and no false resolution (Tasks 4, 5, and 8 tests).

---

## File Structure

### New focused units

- `apps/api/joltiq/services/ask_context.py` — permission-filtered organization retrieval, trust labels, prompt-safe context, and citation payloads for Ask.
- `apps/api/joltiq/services/slow_computer.py` — canonical slow-PC diagnostic, cleanup-plan, verification, and final-report orchestration; no filesystem implementation.
- `apps/api/tests/test_slow_computer.py` — server contracts for canonical plans, approvals, retries, verification, and reports.
- `apps/windows-agent/src/JoltIQ.Agent.Windows/PerformanceInspector.cs` — Windows memory, uptime, restart, startup, disk, and top-process observations.
- `apps/windows-agent/src/JoltIQ.Agent.Windows/SafeCleanup.cs` — fixed cleanup categories, scan rules, device-local inventory, apply journal, and verify read-back.
- `apps/windows-agent/tests/JoltIQ.Agent.Tests/SafeCleanupTests.cs` — filesystem-boundary, inventory, expiry, partial failure, and idempotency tests.
- `tests/e2e/web/pilot_workflows.py` — user-visible organization-answer, Guide, Fix-result, FieldDesk, and escalation acceptance.
- `docs/operations/windows-ai-technician-pilot.md` — exact reset, evidence, run-count, signing, and release procedure extending the existing Windows runbooks.
- `docs/operations/evidence/windows-ai-technician-pilot.json` — generated aggregate consumed by release-readiness validation.

### Existing units extended in place

- `apps/api/joltiq/services/ask_chat.py` — supplies organization context to live chat and persists citations.
- `apps/api/joltiq/services/knowledge/service.py` — adds citation metadata/freshness without changing the permission-filtered search contract.
- `apps/api/joltiq/routers/agents.py` and `apps/api/joltiq/routers/devices.py` — expose citations and synchronized computer-use status through existing tenant/device-checked routes.
- `apps/api/joltiq/services/actions/registry.py` and `apps/api/joltiq/services/verification.py` — register typed performance/cleanup actions and independent read-back.
- `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs` — mirrors the new fixed action schemas and refuses arbitrary paths/categories.
- `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs` — advertises and dispatches the new native actions.
- `apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs` and `apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs` — render sources, terminal outcome text, and fresh consent state.
- `apps/web/src/app/(app)/portal/page.tsx` — renders citation metadata/conflicts and the exact durable outcome wording.
- `apps/api/tests/test_ask_chat_v9.py`, `apps/api/tests/test_device_protocol.py`, `apps/windows-agent/tests/JoltIQ.Agent.Tests/SupportConversationTests.cs`, and `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs` — focused regression coverage.
- `apps/api/tests/test_e2e_browser.py` — pins the existing FieldDesk mutation, persisted read-back, rollback, and forced-failure behavior.
- `scripts/validate_windows_release_readiness.py` and its tests — require the new pilot aggregate without weakening existing signing or VM gates.

---

### Task 1: Organization-aware Ask with permission-filtered citations

**Files:**
- Create: `apps/api/joltiq/services/ask_context.py`
- Modify: `apps/api/joltiq/services/ask_chat.py:27-238`
- Modify: `apps/api/joltiq/services/knowledge/service.py:172-232`
- Modify: `apps/api/joltiq/routers/agents.py:90-125`
- Modify: `apps/api/joltiq/routers/devices.py:217-226,303-436`
- Test: `apps/api/tests/test_ask_chat_v9.py`
- Test: `apps/api/tests/test_platform_features.py`

**Interfaces:**
- Consumes: `knowledge.service.search(db, org_id, query, role=..., limit=...) -> list[dict]` and `AgentRun.started_by`.
- Produces: `AskContextSource`, `AskContext`, `build_ask_context(db: Session, run: AgentRun, query: str, limit: int = 6) -> AskContext`, and citation dictionaries shaped as `{document_id, citation, url, source_type, version, published_at, freshness, conflict, excerpt}`.
- Produces: assistant `AgentMessage.data["citations"]` for both streaming and non-streaming Ask responses.

- [ ] **Step 1: Write failing retrieval and safety tests**

Add tests named:

- `test_plain_ask_supplies_only_role_visible_company_context_and_returns_citations`
- `test_plain_ask_labels_ticket_history_as_hypothesis_and_verified_article_as_trusted`
- `test_plain_ask_surfaces_conflicting_sources_without_executable_artifacts`
- `test_plain_ask_treats_injected_article_text_as_untrusted_context`
- `test_stream_done_and_saved_message_contain_the_same_citations`

Assert that a customer never receives staff-only chunks, the model system/context text marks retrieved text untrusted, response citations contain no unauthorized excerpt, approvals/plans remain empty, conflicts are labeled, and streaming/non-streaming persistence matches.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
docker compose run --rm -T -v "$PWD:/workspace" -w /workspace/apps/api \
  -e PYTHONPATH=/workspace/apps/api -e JOLTIQ_ENV=test \
  -e JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:joltiq_app@postgres:5432/joltiq_test \
  -e JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/joltiq_test \
  -e JOLTIQ_REDIS_URL=redis://redis:6379/15 \
  api pytest -q tests/test_ask_chat_v9.py tests/test_platform_features.py -k 'ask or knowledge'
```

Expected: new tests fail because Ask sends no organization context/citations.

- [ ] **Step 3: Implement prompt-safe Ask context**

Define immutable dataclasses in `ask_context.py`:

```python
@dataclass(frozen=True)
class AskContextSource: ...

@dataclass(frozen=True)
class AskContext:
    prompt: str
    citations: tuple[dict[str, Any], ...]

def build_ask_context(db: Session, run: AgentRun, query: str, limit: int = 6) -> AskContext: ...
```

Resolve the requester's active membership role inside the run's organization, call the existing permission-filtered search, label source trust/outcome, cap each excerpt and the total context, escape retrieved material inside explicit untrusted delimiters, and return no source the caller cannot open.

- [ ] **Step 4: Attach context and citations to every Ask path**

Extend `reply`, `stream_plan`, `finish_stream`, and `_save_reply` so the same frozen `AskContext` feeds the model call and saved message. Keep API keys only in the in-memory stream plan and exclude them from events/logs. Extend device quick replies and status messages to carry the same customer-safe citations; do not add any plan/approval side effect.

- [ ] **Step 5: Run focused and cross-tenant tests**

Run the Step 2 command plus:

```bash
docker compose run --rm -T -v "$PWD:/workspace" -w /workspace/apps/api \
  -e PYTHONPATH=/workspace/apps/api -e JOLTIQ_ENV=test \
  -e JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:joltiq_app@postgres:5432/joltiq_test \
  -e JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/joltiq_test \
  -e JOLTIQ_REDIS_URL=redis://redis:6379/15 \
  api pytest -q tests/test_tenancy_rls.py tests/test_device_protocol.py
```

Expected: all selected tests pass; no cross-tenant or staff-only citation is returned.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/ask_context.py apps/api/joltiq/services/ask_chat.py \
  apps/api/joltiq/services/knowledge/service.py apps/api/joltiq/routers/agents.py \
  apps/api/joltiq/routers/devices.py apps/api/tests/test_ask_chat_v9.py \
  apps/api/tests/test_platform_features.py
git commit -m "feat: ground Ask in tenant knowledge"
```

---

### Task 2: Synchronize Windows consent, terminal status, citations, and results

**Files:**
- Modify: `apps/api/joltiq/routers/devices.py:397-476`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs:20-63`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs:110-500`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Tests/SupportConversationTests.cs`
- Modify: `apps/api/tests/test_device_protocol.py`

**Interfaces:**
- Consumes: Task 1 citation payloads and the existing `computer_use` object returned by `GET /agent/v1/support-requests/{run_id}`.
- Produces: `SupportCitation`, citation-bearing quick replies/messages, and `SupportComputerUseStatus.Active` / `CanRetry` fields.
- Produces: `SupportConversationController.ApplyComputerUseStatus(SupportComputerUseStatus? status)` as the single state-reconciliation path.

- [ ] **Step 1: Write failing API and controller tests**

Add tests named:

- `test_terminated_consent_session_returns_inactive_retryable_status`
- `test_consent_timeout_clears_waiting_status_and_fresh_retry_creates_one_session`
- `test_denial_completion_escalation_and_stop_clear_stale_computer_use_ids`
- `ImmediateAndRefreshedRepliesRenderTheSamePermittedCitations`
- `InactiveComputerUseStateClearsWaitingAndAllowsRetry`
- `RepeatedRefreshDoesNotCreateOrForegroundADuplicatePrompt`

Assert exact customer wording, `active=false`, `can_retry=true`, cleared local run/session IDs, one retry request, and no control call from ordinary Ask.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
docker compose run --rm -T -v "$PWD:/workspace" -w /workspace/apps/api \
  -e PYTHONPATH=/workspace/apps/api -e JOLTIQ_ENV=test \
  -e JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:joltiq_app@postgres:5432/joltiq_test \
  -e JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/joltiq_test \
  -e JOLTIQ_REDIS_URL=redis://redis:6379/15 \
  api pytest -q tests/test_device_protocol.py
dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj \
  --filter FullyQualifiedName~SupportConversationTests
```

Expected: new stale-status/citation assertions fail.

- [ ] **Step 3: Make server status authoritative**

Extend `_computer_use_status` to return `active` and `can_retry` derived from the latest session/run terminal states. Return the latest stopped reason for display but never report a terminated session as awaiting consent. Include permitted message citations in the device response; keep internal evidence fields excluded.

- [ ] **Step 4: Reconcile status in one Windows controller method**

Add `SupportCitation` records and citation fields to quick replies/messages. Implement `ApplyComputerUseStatus` so inactive/terminal/null server state clears local computer-use IDs, approval flags, and stale waiting copy; a retry is enabled only when `CanRetry` is true. Preserve the exact request in a fresh consent prompt and keep the prompt dedupe key tied to the new session/run ID.

- [ ] **Step 5: Render concise sources and terminal outcome text**

Update the tray transcript to show source titles/citations beneath the answer and to display the durable outcome labels from the spec. Do not expose provider names, internal session IDs, model keys, or staff-only excerpts.

- [ ] **Step 6: Run focused tests and native build**

Run the Step 2 commands, then:

```bash
dotnet build apps/windows-agent/src/JoltIQ.Agent.Tray/JoltIQ.Agent.Tray.csproj -warnaserror
```

Expected: all tests/build pass and the stale waiting regression is pinned.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/routers/devices.py apps/api/tests/test_device_protocol.py \
  apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs \
  apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs \
  apps/windows-agent/tests/JoltIQ.Agent.Tests/SupportConversationTests.cs
git commit -m "fix: synchronize companion support status"
```

---

### Task 3: Add typed Windows performance diagnostics

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/PerformanceInspector.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs:51-141`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs:19-154`
- Modify: `apps/api/joltiq/services/actions/registry.py:41-106`
- Test: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`
- Test: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`
- Test: `apps/api/tests/test_security.py`

**Interfaces:**
- Produces: typed read-only action `system.performance.inspect` with no parameters.
- Produces: `PerformanceSnapshot PerformanceInspector.Inspect()` and JSON fields `captured_at`, `uptime_hours`, `restart_pending`, `memory`, `system_drive`, `top_processes`, `startup_items`, `warnings`, and per-field `available`/`reason` for unavailable observations.
- Consumes: existing `ActionResult`, signed command context, and local session policy.

- [ ] **Step 1: Write failing registry, local-policy, and provider tests**

Cover:

- real total/available/committed memory obtained from Windows rather than .NET GC limits;
- fixed system-drive totals;
- top processes sorted and capped without command lines, paths, window text, or secrets;
- startup names/publishers with impact only when Windows exposes it;
- pending-restart indicators from fixed registry locations;
- inaccessible registry/process data returns `available=false` rather than a clean/zero claim;
- action is read-only, accepts no extra parameters, and still requires a locally consented active session.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj \
  --filter 'FullyQualifiedName~WindowsProviderTests'
dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/JoltIQ.Agent.Core.SelfTest.csproj
```

Expected: action/provider tests fail because `system.performance.inspect` does not exist.

- [ ] **Step 3: Implement the read-only inspector and dispatch**

Implement `PerformanceInspector.Inspect()` with fixed Windows APIs/registry reads and bounded collections. Register, advertise, dispatch, and locally authorize only the no-parameter action. Redact user-identifying details and represent unreadable data explicitly.

- [ ] **Step 4: Run native and server registry tests**

Run the Step 2 commands and the test-database command from Task 1 for `tests/test_security.py -k action`.

Expected: all selected tests pass and the companion capability manifest contains the action.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/src/JoltIQ.Agent.Windows/PerformanceInspector.cs \
  apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs \
  apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs \
  apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs \
  apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs \
  apps/api/joltiq/services/actions/registry.py apps/api/tests/test_security.py
git commit -m "feat: add Windows performance diagnostics"
```

---

### Task 4: Add device-local safe cleanup inventory, apply, and verify

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/SafeCleanup.cs`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.Tests/SafeCleanupTests.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/api/joltiq/services/verification.py`
- Test: `apps/api/tests/test_verification_rollback.py`

**Interfaces:**
- Produces: `storage.cleanup.inspect` (READ_ONLY) with `category` enum `temp|diagnostic|developer_cache|project_builds`.
- Produces: `storage.cleanup.apply` (DESTRUCTIVE, non-reversible) with only `inventory_id`.
- Produces: `storage.cleanup.verify` (READ_ONLY) with only `inventory_id`.
- Produces: `SafeCleanupService.Scan(CleanupCategory category, DateTimeOffset now) -> CleanupInventory`, `Apply(string inventoryId, CancellationToken ct) -> CleanupResult`, and `Verify(string inventoryId) -> CleanupVerification`.
- Consumes: a device-local encrypted inventory store, administrator-configured development-root IDs, and existing durable command idempotency.

- [ ] **Step 1: Write failing filesystem safety tests**

Use disposable temporary directory trees and fake clocks/process locks. Test exact age thresholds, category roots, recognized project manifests, exclusion of project roots/Git/source/Downloads/credentials/databases/VMs, reparse-point/junction refusal, case/path normalization, locked/in-use skipping, unknown-path refusal, and zero arbitrary path parameters.

- [ ] **Step 2: Write failing inventory lifecycle tests**

Test a 15-minute expiry, inventory hash/token immutability, device-local lookup, single consumption, duplicate command idempotency, changed-file refusal, durable per-item progress, Stop between items, partial failure evidence, and a separate verify read that lists removed, retained, skipped, and failed bytes/items.

- [ ] **Step 3: Run native tests and verify RED**

Run:

```bash
dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj \
  --filter 'FullyQualifiedName~SafeCleanupTests'
```

Expected: compilation/test failure because safe-cleanup types/actions do not exist.

- [ ] **Step 4: Implement fixed-category scanning and encrypted local inventory**

Implement the exact categories/age rules from the spec. Store normalized path, category, length, last-write time, stable file identity when available, and inventory hash under the companion's protected local store. The scan must never follow a reparse point or return a secret-bearing filename/content to the server.

- [ ] **Step 5: Implement idempotent apply and independent verify**

`Apply` resolves only the local inventory ID, revalidates every item immediately before removal, marks durable progress, honors cancellation between items, and returns exact aggregate evidence. `Verify` uses a separate read path and never trusts the apply result. A second apply refuses or returns the already-saved terminal result without deleting anything else.

- [ ] **Step 6: Register and enforce actions on both sides**

Add the three schemas to the server registry and local policy. Keep `storage.cleanup.apply` out of `registry_for_prompt()` because it is destructive; only Task 5's canonical server workflow may create it. Bind its default postcondition to `device_cleanup_inventory_applied`, which dispatches `storage.cleanup.verify` through `verification.py`.

- [ ] **Step 7: Run native, verification, and protocol tests**

Run the Step 3 command, the Core self-test, and the test-database command for:

```text
tests/test_verification_rollback.py tests/test_device_protocol.py tests/test_security.py
```

Expected: all selected tests pass; no server payload can name a cleanup path.

- [ ] **Step 8: Commit**

```bash
git add apps/windows-agent/src/JoltIQ.Agent.Windows/SafeCleanup.cs \
  apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs \
  apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs \
  apps/windows-agent/tests/JoltIQ.Agent.Tests/SafeCleanupTests.cs \
  apps/api/joltiq/services/actions/registry.py apps/api/joltiq/services/verification.py \
  apps/api/tests/test_verification_rollback.py
git commit -m "feat: add bounded Windows safe cleanup"
```

---

### Task 5: Orchestrate the canonical slow-computer Fix workflow and report

**Files:**
- Create: `apps/api/joltiq/services/slow_computer.py`
- Create: `apps/api/tests/test_slow_computer.py`
- Modify: `apps/api/joltiq/routers/devices.py:499-663`
- Modify: `apps/api/joltiq/services/executor.py`
- Modify: `apps/api/joltiq/services/agent.py:2116-2166`
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs`

**Interfaces:**
- Consumes: Tasks 3-4 actions/results and the existing `AgentPlan`, `PlanStep`, approval, executor, verifier, audit, and device session models.
- Produces: `is_slow_computer_goal(text: str) -> bool`, `create_diagnostic_plan(db, run, session) -> AgentPlan`, `create_cleanup_plan(db, run, diagnostic_results) -> AgentPlan | None`, and `build_customer_report(db, run) -> str`.
- Produces: canonical two-phase flow: read-only diagnostics first; then a separately approved cleanup plan containing only device-returned inventory IDs.

- [ ] **Step 1: Write failing canonical-plan tests**

Test that “my computer is slow” and explicit disk/memory variants select the workflow, while unrelated/ambiguous text does not. Assert the diagnostic plan is server-authored from the fixed action list and the cleanup plan contains only returned inventory IDs/categories, exact non-rollback wording, measured byte estimates, and no path/model-authored step.

- [ ] **Step 2: Write failing lifecycle and report tests**

Cover approval required once per exact cleanup step, changed inventory/plan hash superseding approval, no automatic mutation retry, duplicate result idempotency, Stop, offline/expiry, partial cleanup, verify UNKNOWN/FAILED, empty cleanup candidate, and before/after report text. Assert only independently verified success uses **Resolved — independently verified**.

- [ ] **Step 3: Run tests and verify RED**

Run the test-database command for:

```text
tests/test_slow_computer.py tests/test_device_protocol.py tests/test_verification_rollback.py
```

Expected: new slow-computer tests fail because canonical orchestration/reporting does not exist.

- [ ] **Step 4: Implement canonical diagnosis and cleanup-plan construction**

Route the bounded slow-PC intent into the existing signed structured-plan path. Run diagnostics under local session consent. Build cleanup steps only after device results exist and only for non-empty categories selected by the customer. Do not send the inventory contents back as executable parameters and do not let a model add, remove, or rewrite cleanup steps.

- [ ] **Step 5: Bind approval, execution, verification, and outcome**

Use the existing plan hash, approval, execution, command journal, verification, and partial-application mechanisms. Never retry `storage.cleanup.apply` automatically. Re-read disk/performance plus every cleanup inventory. Keep the ticket open as verification pending when symptom evidence is unavailable; resolve only when the declared checks pass.

- [ ] **Step 6: Generate the customer report and tray timeline**

Create a deterministic report containing elapsed time, before/after free disk and memory observations, removed/skipped/failed categories and bytes, explicit preserved classes, non-rollback/rebuild notes, remaining pressure, outcome label, and next safe question. Persist the report as the assistant message and audit summary; render it in the tray without internal IDs.

- [ ] **Step 7: Run server and Windows conversation regressions**

Run the Step 3 tests, all `test_computer_use.py`, all `test_device_protocol.py`, and `SupportConversationTests`.

Expected: all selected tests pass and no ordinary Ask request creates a diagnostic/control artifact.

- [ ] **Step 8: Commit**

```bash
git add apps/api/joltiq/services/slow_computer.py apps/api/tests/test_slow_computer.py \
  apps/api/joltiq/routers/devices.py apps/api/joltiq/services/executor.py \
  apps/api/joltiq/services/agent.py apps/api/joltiq/services/computer_use.py \
  apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs
git commit -m "feat: add verified slow-computer workflow"
```

---

### Task 6: Present citations, conflicts, and durable outcomes in the web portal

**Files:**
- Modify: `apps/web/src/app/(app)/portal/page.tsx:12-208`
- Modify: `apps/web/src/components/types.ts`
- Modify: `tests/e2e/web/ui_smoke.py`
- Create: `tests/e2e/web/pilot_workflows.py`

**Interfaces:**
- Consumes: Task 1 citation metadata, Task 2 synchronized status, and Task 5 outcome/report data.
- Produces: `SourceCard`/source-drawer UI with title, source, version, publication/freshness, permitted excerpt, and **Sources disagree** state; exact outcome labels shared with the tray.

- [ ] **Step 1: Add failing browser tests**

Seed visible and staff-only sources plus a conflict. Assert the customer sees inline markers and can open only permitted source details; staff-only content is absent from DOM/network responses; conflicts are visibly labeled; provider/session internals are hidden; and all seven outcome labels from the spec render exactly.

- [ ] **Step 2: Run web tests and verify RED**

Run:

```bash
cd apps/web && npm run typecheck && npm run build
cd ../.. && python tests/e2e/web/pilot_workflows.py http://127.0.0.1:3000
```

Expected: pilot workflow test fails because metadata/conflict/outcome presentation is incomplete.

- [ ] **Step 3: Implement the source drawer and shared outcome presentation**

Render only API-provided permitted metadata/excerpts. Show stale/conflict warnings without presenting conflicting content as an executable recommendation. Replace generic “Finished” copy with the durable labels and report. Preserve keyboard navigation, `aria-live`, reduced motion, and mobile layout.

- [ ] **Step 4: Run type, build, smoke, and pilot tests**

Run the Step 2 command plus the existing web smoke suite.

Expected: typecheck/build succeed and all browser assertions pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/'(app)'/portal/page.tsx apps/web/src/components/types.ts \
  tests/e2e/web/ui_smoke.py tests/e2e/web/pilot_workflows.py
git commit -m "feat: show trusted sources and fix outcomes"
```

---

### Task 7: Lock the existing FieldDesk browser/SaaS repair and honest escalation

**Files:**
- Modify: `apps/api/tests/test_e2e_browser.py:19-110,306-340`
- Modify only if a failing test proves necessary: `apps/api/joltiq/services/rollback.py`
- Modify only if a failing test proves necessary: `apps/api/joltiq/services/verification.py`
- Modify: `tests/e2e/web/pilot_workflows.py`

**Interfaces:**
- Consumes: existing FieldDesk approved procedure: set dispatch email, set job notifications `on`, save, reload, and verify both persisted values.
- Produces: one named pilot browser workflow with captured pre-state, deterministic rollback, forced buggy-save verification failure, and escalation evidence.

- [ ] **Step 1: Add/strengthen failing acceptance assertions**

Assert exact approved values, pre-state captured before mutation, persisted reload verification, rollback to both prior values on forced failure, no resolution after buggy save, and customer-visible escalation/rollback wording. Add denial, expired approval, duplicate execution, and STOP assertions if absent.

- [ ] **Step 2: Run FieldDesk tests and observe the actual baseline**

Run:

```bash
docker compose --profile e2e up -d testapp browser-worker
# Then run the test-database API command for tests/test_e2e_browser.py
```

Expected: existing passing behavior stays green; only genuinely missing assertions fail. If all new assertions pass without production changes, do not modify production code.

- [ ] **Step 3: Make only evidence-driven fixes**

If Step 2 exposes a gap, change only the owner service named by the failing assertion. Do not create another browser engine or bypass the existing procedure/policy/verification path.

- [ ] **Step 4: Run FieldDesk API and portal acceptance**

Run the full `test_e2e_browser.py` plus the FieldDesk case in `pilot_workflows.py`.

Expected: success, forced failure, rollback, denial, expiry, duplicate, and Stop paths all match the declared outcomes.

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_e2e_browser.py tests/e2e/web/pilot_workflows.py \
  apps/api/joltiq/services/rollback.py apps/api/joltiq/services/verification.py
git commit -m "test: lock browser pilot repair outcomes"
```

Omit unchanged production files from `git add`.

---

### Task 8: Execute the Windows pilot acceptance and preserve release blockers honestly

**Files:**
- Create: `docs/operations/windows-ai-technician-pilot.md`
- Generate: `docs/operations/evidence/windows-ai-technician-pilot.json`
- Modify: `scripts/validate_windows_release_readiness.py`
- Modify: `scripts/tests/test_validate_windows_release_readiness.py`
- Modify: `PROJECT_STATUS.md`
- Modify: `RELEASE_CHECKLIST.md`

**Interfaces:**
- Consumes: all prior task tests, existing Windows general-computer-use/installer runbooks, signed installer workflow, physical/disposable Windows runner, and independent review evidence.
- Produces: a machine-readable aggregate with named cases, clean snapshot, companion/build version, timestamps, evidence hashes, UI outcome, audit outcome, and pass/fail; release validator refuses missing/unsigned/unreviewed evidence.

- [ ] **Step 1: Write failing release-validator tests**

Require the new aggregate and reject: wrong OS/architecture, reused dirty snapshot, fewer than three consecutive happy runs, missing failure cases, missing companion/server commit, absent evidence hashes, unsigned installer, missing timestamp, incomplete install/upgrade/repair/uninstall, or absent independent-review disposition.

- [ ] **Step 2: Run validator tests and verify RED**

Run:

```bash
python -m pytest -q scripts/tests/test_validate_windows_release_readiness.py
```

Expected: new tests fail because the pilot aggregate is not required yet.

- [ ] **Step 3: Extend—not replace—the release validator and runbook**

Document the six workflows, three reset-snapshot happy runs, and one run for every denial, timeout, offline, duplicate, Stop, partial failure, and forced-verification failure. Reference existing signing, installer lifecycle, general computer-use, and security-review requirements. Make the validator fail closed on missing external evidence.

- [ ] **Step 4: Run all automated gates before physical acceptance**

Run:

```bash
# API full suite against joltiq_test
docker compose run --rm -T -v "$PWD:/workspace" -w /workspace/apps/api \
  -e PYTHONPATH=/workspace/apps/api -e JOLTIQ_ENV=test \
  -e JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:joltiq_app@postgres:5432/joltiq_test \
  -e JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/joltiq_test \
  -e JOLTIQ_REDIS_URL=redis://redis:6379/15 api pytest -q

docker compose run --rm -T -v "$PWD:/workspace" -w /workspace/apps/api api \
  sh -lc 'ruff check joltiq tests && mypy joltiq'
dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj
dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/JoltIQ.Agent.Core.SelfTest.csproj
cd apps/web && npm run typecheck && npm run build
```

Expected: zero failures. Environment-bound browser/SMTP tests must run in their documented service profile rather than be waived.

- [ ] **Step 5: Build and install the signed pilot companion**

Use the existing protected Windows packaging workflow. Record MSI SHA-256, Authenticode chain, trusted timestamp, server/companion commit, install, enrollment, repair, upgrade, rollback/recovery where supported, and uninstall evidence. If production signing inputs are unavailable, stop with `BLOCKED_EXTERNAL_DEPENDENCY`; do not relabel an unsigned internal MSI as pilot-ready.

- [ ] **Step 6: Execute the reset-snapshot Windows 11 matrix**

Run every named case through the customer-facing Ask UI. Save companion logs, screenshots of consent/result, audit timeline, command journal disposition, before/after device evidence, and final ticket/outcome state. Generate the aggregate only from captured evidence; never hand-edit a case to pass.

- [ ] **Step 7: Obtain and attach independent security review disposition**

Review the exact pilot scope, action schemas, cleanup path boundary, consent/STOP, device identity, model data boundary, prompt injection, tenant isolation, update/signing, and evidence retention. Record findings and remediation disposition. Until accepted, keep the release status blocked.

- [ ] **Step 8: Run final release validation**

Run:

```bash
python scripts/validate_windows_release_readiness.py \
  --evidence docs/operations/evidence/windows-public-release-readiness.json \
  --pilot-evidence docs/operations/evidence/windows-ai-technician-pilot.json
git diff --check
git status --short
```

Expected: `PASS` only when all automated, Windows, signing, installer, and review gates are present; otherwise an exact blocker list with no release-ready claim.

- [ ] **Step 9: Commit**

```bash
git add docs/operations/windows-ai-technician-pilot.md \
  docs/operations/evidence/windows-ai-technician-pilot.json \
  scripts/validate_windows_release_readiness.py \
  scripts/tests/test_validate_windows_release_readiness.py \
  PROJECT_STATUS.md RELEASE_CHECKLIST.md
git commit -m "docs: record Windows AI technician pilot acceptance"
```

---

## Self-Review Results

- **Spec coverage:** The plan covers organization answers/citations (Task 1), direct Ask and status (Tasks 1-2), Guide/Fix safety (Tasks 2, 5, 8), slow-PC diagnosis/cleanup/report (Tasks 3-5), browser/SaaS repair (Task 7), visible outcomes (Tasks 2 and 6), and finite release evidence (Task 8). macOS, broad connectors, source-native ACL sync, and expanded domain packs remain explicitly deferred.
- **Step scan:** Each task has a RED test, a bounded implementation owner, a GREEN command, and a focused commit. Task 7 explicitly forbids production edits when strengthened tests already pass.
- **Type consistency:** `AskContext` flows through Ask save/stream/device rendering; server `SupportComputerUseStatus.Active/CanRetry` flows into one controller reconciler; cleanup apply/verify use only `inventory_id`; Task 5 consumes the exact Task 3-4 action IDs.
- **Review Focus:** Prompt injection is owned by Task 1; stale consent by Task 2; filesystem escape/active-file refusal and partial/idempotent cleanup by Task 4; terminal reporting by Tasks 5 and 8.
- **Proportion:** The plan names interfaces, exact decisions, tests, and commands without transcribing implementation bodies.
