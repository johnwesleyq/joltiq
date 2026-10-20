# Windows Outlook PDF Crash MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Windows-first JoltIQ workflow that proves the Outlook/PDF-preview diagnosis, executes the exact published procedure with consent, preserves rollback state, verifies the original symptom, and produces auditable real-Windows acceptance evidence.

**Architecture:** Extend the existing durable agent state machine with a candidate-procedure diagnostic gate and two-stage outcome verification. Add narrow, typed Outlook inspection and reversible add-in actions to the signed Windows companion; keep policy and evidence evaluation outside the AI. Persist procedure identity, outcome stage, consent copy, command results, local rollback references, and verification evidence so disconnects, duplicates, stops, and partial failures have deterministic outcomes.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/Alembic, pytest, Next.js 16/React 19/TypeScript, .NET 8 Windows Forms/WPF APIs, Windows Event Log/Registry/WTS APIs, WiX 5, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-20-windows-outlook-pdf-mvp-design.md`

## Global Constraints

- Supported target: 64-bit Windows 10 build 19041+ and Windows 11 with classic Outlook from Microsoft 365.
- New Outlook, missing Outlook, locked/no interactive user, multiple interactive users, missing or policy-controlled add-ins, and unconfirmed crash evidence must escalate without a change.
- Executable remediation is copied byte-for-byte from the published procedure’s canonical action/parameter/rationale/postcondition data; the AI cannot author or alter it.
- Portal consent, native local consent, exact step approval, signed protocol validation, local policy, and independent verification remain separate gates.
- Executable procedures require two distinct organization admins/owners with MFA-authenticated sessions: one approves the exact canonical hash and the other publishes that same hash; every decision is audited.
- Never force-close Outlook, execute shell commands, or treat `UNKNOWN` as success.
- The Windows MVP exposes no screen-control actions. Chat history, remembered resolutions, learned fixes, and model output may retrieve or rank a candidate procedure, but can never create an executable desktop plan or bypass the canonical procedure gate.
- Per-user diagnosis, mutation, local journaling, and rollback run only in the logged-in tray process for the diagnosed/enrolled Windows user. The background service/SYSTEM process cannot write or restore HKCU state.
- State-changing commands are idempotent and are not automatically retried; transient read-only diagnostics/checks receive one retry after five seconds.
- Device commands expire after 120 seconds; an unreconciled device outage escalates after five minutes.
- Local STOP prevents new work within one second and targets cooperative cancellation within two seconds; completed changes are reported and offered rollback.
- `FIX_APPLIED_VERIFIED` does not close the ticket. Only customer confirmation plus a healthy, readable crash-log signal and a clean 60-second post-attempt observation may produce `RESOLVED_VERIFIED`; unreadable, disabled, stale, or otherwise unhealthy logging yields `UNKNOWN`.
- If the customer has not tested within 10 minutes, keep the ticket open as `FIX_APPLIED_VERIFIED`, send exactly one reminder, and release the active session. If there is still no structured response 24 hours after the fix, escalate to a technician as `verification_timeout`; never auto-resolve.
- Unsigned installers are internal-test-only. Authenticode-signed binaries and MSI are required before an external pilot.
- Do not weaken or remove existing browser, tenant-isolation, approval, protocol, or audit tests while implementing this workflow.

## Review Focus

- Registry precedence and bitness: HKCU/HKLM, Policies, and 32/64-bit views must identify the effective setting and refuse a user-level write that will not persist. Covered in Task 3.
- Interactive-session ambiguity: locked desktops, no user, console plus RDP, and mismatched enrolled/interactive users must refuse diagnosis/remediation. Covered in Task 3.
- Lost or duplicate results: a completed registry write followed by network loss must resend its recorded result without executing again. Covered in Task 6.
- STOP races: stop before dispatch, during a registry write, and during safe restart must prevent subsequent work and report any atomic change that completed. Covered in Tasks 6 and 10.
- False symptom resolution: a quiet event log without a customer PDF attempt, unhealthy/unreadable crash logging, a customer “works” response followed by a crash, and a deferred test must never close the ticket. Covered in Task 8.

---

### Task 1: Persist Procedure Identity, Outcome Stage, and Consent Purpose

**Files:**
- Create: `apps/api/migrations/versions/0005_windows_outlook_mvp.py`
- Modify: `apps/api/joltiq/models/support.py`
- Modify: `apps/api/joltiq/models/operations.py`
- Modify: `apps/api/joltiq/models/enums.py`
- Modify: `apps/api/joltiq/routers/knowledge.py`
- Test: `apps/api/tests/test_platform_features.py`
- Test: `apps/api/tests/test_tenancy_rls.py`

**Interfaces:**
- Produces: `KnowledgeArticle.procedure_hash: str | None`.
- Produces: hash-bound executable publication approvals recording distinct approver/publisher IDs, MFA-backed session IDs, decisions, and timestamps.
- Produces: `AgentPlan.procedure_article_id`, `procedure_version`, and `procedure_hash`.
- Produces: `AgentRun.outcome_stage` with `UNCONFIRMED`, `FIX_PARTIALLY_APPLIED`, `RESTORED_PENDING_RESTART`, `FIX_APPLIED_VERIFIED`, `RESOLVED_VERIFIED`, and `VERIFICATION_FAILED`.
- Produces: `SupportSession.request_details: dict` for signed, customer-visible purpose and copy before consent.
- Produces: nonterminal run states `AWAITING_DIAGNOSTIC_CONSENT`, `COLLECTING_DIAGNOSTICS`, `AWAITING_SYMPTOM_CHECK`, and `AWAITING_ROLLBACK`.

- [ ] **Step 1: Write migration/model tests**

Add tests that record a hash-bound executable approval and publication by distinct admins, create a bound plan/session, and assert procedure identity, outcome stage, publication evidence, and request details remain tenant-scoped after a database round trip. Add cross-tenant reads for every new tenant column/model path.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_platform_features.py tests/test_tenancy_rls.py -q`

Expected: FAIL because the new columns and run states do not exist.

- [ ] **Step 3: Add migration `0005` and mapped fields**

Add nullable procedure-binding columns for legacy data, a non-null `outcome_stage` defaulting to `UNCONFIRMED`, non-null `request_details` defaulting to `{}`, and tenant-scoped hash-bound executable publication evidence. Preserve PostgreSQL RLS behavior and downgrade symmetry.

- [ ] **Step 4: Serialize the new fields**

Expose article procedure hash, plan procedure binding, run outcome stage, and session request details only through the existing tenant-checked serializers.

- [ ] **Step 5: Re-run the focused tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_platform_features.py tests/test_tenancy_rls.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/migrations/versions/0005_windows_outlook_mvp.py apps/api/joltiq/models apps/api/joltiq/routers/knowledge.py apps/api/tests/test_platform_features.py apps/api/tests/test_tenancy_rls.py
git commit -m "feat: persist procedure-bound Windows outcomes"
```

### Task 2: Validate and Canonicalize Executable Procedures

**Files:**
- Create: `apps/api/joltiq/services/procedures.py`
- Modify: `apps/api/joltiq/routers/knowledge.py`
- Modify: `apps/api/joltiq/services/agent.py`
- Modify: `apps/api/tests/test_platform_features.py`
- Modify: `apps/api/tests/test_e2e_desktop.py`
- Test: `apps/api/tests/test_procedures.py`

**Interfaces:**
- Produces: `ProcedureDefinition` and nested `DiagnosticStep`, `RemediationStep`, and `RollbackDefinition` Pydantic models.
- Produces: `validate_executable_body(body: dict) -> ProcedureDefinition`.
- Produces: `canonical_payload(definition: ProcedureDefinition) -> dict`.
- Produces: `procedure_hash(definition: ProcedureDefinition) -> str` using existing `canonical_json` and `sha256_hex`.
- Produces: `load_published_procedure(article: KnowledgeArticle) -> ProcedureDefinition` that rechecks the stored hash.

- [ ] **Step 1: Write canonical-contract tests**

Test that map ordering does not change the hash; action, parameter, rationale, postcondition, diagnostic expectation, add-in ProgID, or ordering changes do. Test that executable publication rejects missing rollback data, untyped diagnostics, computer-use primitives, unsupported actions, and an Outlook procedure without exact `addin_progid` and `faulting_module`. Require two distinct admin/org-owner identities, MFA enabled and passed in both sessions, and matching canonical hashes; reject self-approval, technicians, missing/failed MFA, a changed hash, and reuse of an approval for another version.

- [ ] **Step 2: Verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_procedures.py tests/test_platform_features.py -q`

Expected: FAIL because no strict procedure contract or stored hash exists.

- [ ] **Step 3: Implement procedure validation and hashing**

Support schema version `2` for strict desktop procedures. Keep existing published browser procedures readable, but require schema v2 for any newly published executable desktop procedure. Canonicalize only validated fields; never hash arbitrary presentation metadata.

- [ ] **Step 4: Enforce the contract at publish and load time**

On executable approval, validate, compute, and store the exact hash with the first admin’s MFA-backed decision. Publication requires a second distinct admin/org owner in an MFA-backed session and the unchanged hash; both events are audited. On execution, recompute and compare; a missing or changed hash raises a safe procedure-integrity error and creates an audit security event.

- [ ] **Step 5: Convert the Outlook E2E fixture to schema v2**

Give the fixture exact diagnostic action/expectations, exact remediation steps, rollback definition, customer messages, add-in display name/ProgID, and faulting module.

- [ ] **Step 6: Run focused tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_procedures.py tests/test_platform_features.py tests/test_e2e_desktop.py -q`

Expected: PASS, with the desktop E2E still simulator-labelled.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/services/procedures.py apps/api/joltiq/routers/knowledge.py apps/api/joltiq/services/agent.py apps/api/tests
git commit -m "feat: bind executable plans to canonical procedures"
```

### Task 3: Add Typed Windows Outlook Diagnostic Inspection

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/InteractiveSessionInspector.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/OutlookInspector.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`
- Test: `apps/api/tests/test_security.py`

**Interfaces:**
- Produces action: `outlook.pdf_crash.inspect` (`READ_ONLY`).
- Consumes params: `{addin_progid: string, faulting_module: string, lookback_hours: 1..168, since?: ISO-8601}`.
- Produces result sections: `windows`, `interactive`, `outlook`, `addin`, `crash`, `supported`, and `unsupported_reasons`.
- `addin` reports scope, registry view, original value/kind, policy override, and `changeable` without exposing arbitrary registry reads.
- `interactive` binds a stable enrolled-user identity to the single unlocked tray session; `crash` reports log readability, service/configuration health, newest event timestamp, and whether the observation window is trustworthy.

- [ ] **Step 1: Write policy and Windows-provider tests**

Add literal fixtures for classic Outlook present/absent, New Outlook present, add-in absent, HKCU 32/64 views, HKLM add-in, HKCU/HKLM Policies overrides, Event ID 1000 matching/nonmatching modules, disabled/unreadable/stale Application logging, locked/no user, enrolled/logged-in user mismatch, and multiple active sessions. The production mutation each test catches is a false `supported=true`, a false clean crash observation, or a wrong effective setting/user.

- [ ] **Step 2: Verify RED on cross-platform policy tests**

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest`

Expected: FAIL because the action is not locally allowlisted.

- [ ] **Step 3: Implement local-policy and server-registry schemas**

Mirror parameter validation in C# and Python. Restrict module and ProgID patterns; cap event lookback; prohibit arbitrary event-log or registry targets.

- [ ] **Step 4: Implement Windows inspection**

Use WTS APIs for active interactive sessions and stable user SID, input-desktop/lock detection for the current session, App Paths and installed-product evidence for classic Outlook, AppX evidence for New Outlook, explicit `RegistryView.Registry32/Registry64`, and Application Error/Windows Error Reporting event fields. Probe that the required log is enabled, readable, current, and capable of supporting the requested observation window. Return structured facts and reasons; do not decide ticket success on the device.

- [ ] **Step 5: Run Windows tests on a Windows runner**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests -c Release`

Expected: PASS on Windows 10/11. On macOS, run only the Core self-test and record the Windows suite as pending CI evidence.

- [ ] **Step 6: Run server security tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_security.py -q`

Expected: PASS, including rejection of malformed diagnostic parameters.

- [ ] **Step 7: Commit**

```bash
git add apps/windows-agent/src apps/windows-agent/tests apps/api/joltiq/services/actions/registry.py apps/api/tests/test_security.py
git commit -m "feat: inspect Outlook PDF crash prerequisites"
```

### Task 4: Gate Canonical Planning on Consented Device Diagnosis

**Files:**
- Modify: `apps/api/joltiq/services/agent.py`
- Modify: `apps/api/joltiq/services/ai/schemas.py`
- Modify: `apps/api/joltiq/services/ai/fixture.py`
- Modify: `apps/api/joltiq/services/devices.py`
- Modify: `apps/api/joltiq/services/sessions.py`
- Create: `apps/api/joltiq/services/customer_messages.py`
- Modify: `apps/api/tests/test_e2e_desktop.py`
- Modify: `apps/api/tests/test_security.py`

**Interfaces:**
- Produces: `select_candidate_procedure(...) -> article_id | None`; the model may select only an evidence-provided ID.
- Produces: `queue_procedure_diagnostics(db, run, procedure, session) -> list[DeviceCommand]`.
- Produces: `evaluate_diagnostic_gate(procedure, command_results) -> DiagnosticDecision` with exact mismatch reasons.
- Produces: `build_canonical_plan(db, run, article, procedure, session) -> AgentPlan` without a model-authored step.
- Consumes: Task 2’s validated definition and Task 3’s typed diagnostic result.

- [ ] **Step 1: Rewrite the desktop E2E expectation first**

Assert sequence: candidate match → portal diagnostic consent → native local consent → typed diagnostics → exact canonical plan → per-step approvals. Assert no `AgentPlan` exists before diagnostics pass and no model call can alter the add-in ProgID. Feed the agent a remembered/learned past fix and matching chat instructions, and assert they can select only a published candidate ID: neither can create executable steps or reach a desktop action outside the procedure gate.

- [ ] **Step 2: Add mismatch/unsupported tests**

Table-test New Outlook, Outlook absent, add-in absent, wrong module, policy-controlled setting, locked/no user, enrolled/logged-in user mismatch, multiple users, unhealthy/unreadable crash logging, and missing diagnostic result. Each must end `ESCALATED`, with no remediation command and the exact safe customer message.

- [ ] **Step 3: Verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_security.py -q`

Expected: FAIL because the current agent plans before procedure-specific diagnosis.

- [ ] **Step 4: Split candidate selection from executable planning**

Have internal search expose immutable candidate IDs/hashes. The model may select a candidate ID and explain a hypothesis, but executable steps come only from `build_canonical_plan` after diagnostics.

- [ ] **Step 5: Create the diagnostic session before planning**

Populate `SupportSession.request_details` with the spec’s portal/local messages and named add-in. Pause in `AWAITING_DIAGNOSTIC_CONSENT` until both consent gates pass, then queue only canonical read-only diagnostics.

- [ ] **Step 6: Evaluate the gate and bind the plan**

Require every expected literal fact. Copy canonical remediation exactly, persist article/version/hash, and recompute the plan hash from canonical full steps. Escalate on integrity mismatch or any unknown/unsupported result.

Explicitly disable registration/dispatch of `screen.capture`, `input.*`, and other computer-use actions in the Windows MVP runtime. A consented support session does not widen the Outlook procedure’s action set.

- [ ] **Step 7: Run focused tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_security.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/joltiq/services apps/api/joltiq/services/ai apps/api/tests/test_e2e_desktop.py apps/api/tests/test_security.py
git commit -m "feat: require proven diagnosis before Windows planning"
```

### Task 5: Make Add-in Changes Reversible and Outlook Restart Strictly Safe

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Core/CommandJournal.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/OfficeAddinManager.cs`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.FixtureApp/JoltIQ.Agent.FixtureApp.csproj`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.FixtureApp/Program.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/api/joltiq/services/verification.py`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`

**Interfaces:**
- Produces: injectable `ICommandJournal` mutation-record contract used by the action provider; Task 6 supplies its persistent DPAPI implementation and outbox behavior.
- Changes action: `office.addin.set_load_behavior` refuses missing or policy-controlled targets, saves complete typed pre-state only in the PC’s local journal, and returns its `source_command_id` plus confirmed/redacted `before`/`after` evidence. Server-side evidence is never authoritative for restore.
- Produces action: `office.addin.restore_load_behavior` (`SENSITIVE`) consuming only `{source_command_id}`; the tray resolves that ID to its own authenticated local journal entry and restores the typed pre-state stored there. No registry value, key, view, or pre-state is accepted from the server.
- Changes action result: `app.restart` returns `was_running`, `close_requested`, `started`, `restarted`, `responding`, and `safe`; it never force-kills.
- Both add-in actions run only inside the tray process after re-checking that its current Windows SID matches the enrolled/diagnosed SID. The background service/SYSTEM path has no handler or permission for these actions.

- [ ] **Step 1: Write Windows action tests**

Cover existing user add-in, missing add-in, policy override, wrong registry view, exact local-journal restore including “value originally absent,” missing/wrong/already-used source command IDs, attempts to send registry state from the server, tray/service execution identity, enrolled/logged-in SID mismatch immediately before write, process initially absent, clean close, ignored close/save prompt, executable missing, launch failure, and responding/nonresponding launch.

- [ ] **Step 2: Verify RED**

Run on Windows: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests -c Release`

Expected: FAIL because current code creates missing registry keys, has no restore action, and does not return the required restart evidence.

- [ ] **Step 3: Implement `OfficeAddinManager`**

Resolve the effective key/view found by diagnostics, re-check policy and the current tray SID immediately before write, capture typed pre-state through the injected local command-journal contract, write the single value, and read it back. Refuse the mutation if the pre-state record cannot be saved. Restore accepts only the earlier change command ID, loads its locally saved typed pre-state, verifies the same procedure/device/user/action lineage, refuses missing or mismatched entries, restores once, and independently reads the value back. Never serialize authoritative restore state into a server command. Unit tests use an in-memory journal; Task 6 wires durable production storage before the workflow can be enabled.

- [ ] **Step 4: Harden safe restart**

Use normal close only, retain the existing 20-second limit, report an ignored prompt without force, start classic Outlook when absent, and verify a responding main window. Use the fixture app to deterministically simulate clean and refused closes.

- [ ] **Step 5: Add independent read-back evaluation**

Teach verification to evaluate exact add-in state and safe restart fields without trusting the mutating action’s own success flag.

- [ ] **Step 6: Re-run Windows and server tests**

Run on Windows: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests -c Release`

Run: `cd apps/api && .venv/bin/pytest tests/test_security.py tests/test_e2e_desktop.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/windows-agent/src apps/windows-agent/tests apps/api/joltiq/services/actions/registry.py apps/api/joltiq/services/verification.py apps/api/tests
git commit -m "feat: make Outlook remediation safe and reversible"
```

### Task 6: Persist Command Results and Survive Duplicates, Disconnects, and Stops

**Files:**
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/CommandJournal.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/DpapiCommandJournal.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentRuntime.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/Program.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`
- Modify: `apps/api/joltiq/services/devices.py`
- Modify: `apps/api/joltiq/routers/devices.py`
- Modify: `apps/api/tests/test_device_protocol.py`

**Interfaces:**
- Extends: `ICommandJournal.TryGet(commandId)`, `MarkRunning`, `MarkCompleted`, `PendingReports`, and `AcknowledgeReport` around Task 5’s mutation-record contract.
- Produces: DPAPI-protected bounded production journal and deterministic in-memory self-test journal.
- Produces: immutable local mutation records keyed by command ID with procedure/device/user/action lineage, typed encrypted pre-state, terminal result, and one-time rollback linkage.
- Changes `AgentRuntime`: duplicate completed commands resend cached results; pending reports flush before new commands; stopped sessions refuse queued work.
- Changes server command TTL from 300 to 120 seconds; conflicting duplicate results create a security event and never overwrite the first terminal result.

- [ ] **Step 1: Add Core self-tests first**

Test duplicate delivery before/after completion, network loss after an atomic action, process restart with a pending report, conflicting duplicate result, stop before dispatch, stop during cancellable work, durable typed pre-state before mutation, one-time rollback lookup by source command ID, and the mutation “journal entry added before action completion.” Assert the action call count remains exactly one and authoritative rollback state never comes from the server.

- [ ] **Step 2: Verify RED**

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest`

Expected: FAIL because `_handledCommands` is memory-only and drops a result after a send failure.

- [ ] **Step 3: Implement the journal/outbox contract**

Persist running/completed state, encrypted typed mutation pre-state, lineage, and the redacted terminal result. Write the mutation record durably before changing HKCU; mark completion before attempting network delivery; acknowledge only after the server accepts it. Bound ordinary retained entries by age/count, but retain an unrolled-back mutation record for the procedure’s support/audit retention period and never discard an unacknowledged report.

- [ ] **Step 4: Wire DPAPI persistence and STOP timing**

Construct the production journal from the tray user’s DPAPI key store. The service may broker signed envelopes but cannot open the per-user journal or invoke HKCU mutation handlers. Gate action dispatch on the active session cancellation token and record monotonic stop timestamps for the acceptance bundle.

- [ ] **Step 5: Harden server reconciliation**

Set the 120-second envelope expiry, accept identical result retries idempotently, reject conflicting terminal results, and keep late results as evidence without turning an escalated run into success.

- [ ] **Step 6: Run protocol tests**

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest`

Run: `cd apps/api && .venv/bin/pytest tests/test_device_protocol.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/windows-agent/src apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest apps/api/joltiq/services/devices.py apps/api/joltiq/routers/devices.py apps/api/tests/test_device_protocol.py
git commit -m "feat: reconcile Windows commands exactly once"
```

### Task 7: Record Partial Application and Offer Exact Rollback

**Files:**
- Modify: `apps/api/joltiq/services/agent.py`
- Modify: `apps/api/joltiq/services/executor.py`
- Modify: `apps/api/joltiq/services/approvals.py`
- Modify: `apps/api/joltiq/services/verification.py`
- Modify: `apps/api/joltiq/routers/agents.py`
- Modify: `apps/api/tests/test_e2e_desktop.py`
- Modify: `apps/api/tests/test_security.py`

**Interfaces:**
- Produces: `mark_partial_application(db, run, failed_step, completed_executions)`.
- Produces: `offer_rollback(db, run, source_execution) -> AgentPlan`, containing exact `office.addin.restore_load_behavior({source_command_id})` and safe `app.restart` steps with separate approvals.
- Consumes: only Task 6’s reconciled source command ID/status; authoritative pre-state remains exclusively in the tray journal.
- Produces outcome stage `FIX_PARTIALLY_APPLIED` and rollback audit/session events.

- [ ] **Step 1: Write partial-failure scenarios**

Test add-in success plus restart failure, rollback restore plus safe restart, restore succeeds/restart fails or is declined, rollback denied, missing local journal entry, device offline, revoked device, rollback failure, and STOP after the registry write. Assert exact original/current state, no automatic undo, source-command-only restore parameters, separate approval binding, independent registry read-back, and a final escalated/open ticket.

- [ ] **Step 2: Verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_security.py -q`

Expected: FAIL because current generic retry planning can retry a state-changing plan and has no rollback offer.

- [ ] **Step 3: Stop retrying state-changing failures**

Change `_execution_failed` so failed read-only diagnostics may retry once, but any plan with a completed state-changing step goes to partial-application handling. Preserve loop detection for all paths.

- [ ] **Step 4: Build and approve the rollback plan**

Create a procedure-bound rollback plan that names only the original change command ID, followed by a safe Outlook restart. Use normal approval signing/consumption. Show the spec’s exact “change has not been undone” message until independent registry read-back passes. If restore succeeds but Outlook cannot be safely restarted, say the original setting is restored and will take effect the next time Outlook starts; do not claim the active Outlook process has reverted.

- [ ] **Step 5: Record final outcomes**

Successful restore is established by independent registry read-back, not Outlook behavior. A successful restore plus restart restores the prior effective state; a successful restore without restart is `RESTORED_PENDING_RESTART`. Neither resolves the Outlook problem. Escalate as safely restored/pending restart; denied/unavailable/failed rollback escalates with explicit remaining state and manual restoration data.

- [ ] **Step 6: Re-run focused tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_security.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/services apps/api/joltiq/routers/agents.py apps/api/tests/test_e2e_desktop.py apps/api/tests/test_security.py
git commit -m "feat: surface partial Windows changes and rollback"
```

### Task 8: Separate Fix Verification from Symptom Resolution

**Files:**
- Modify: `apps/api/joltiq/services/verification.py`
- Modify: `apps/api/joltiq/services/agent.py`
- Modify: `apps/api/joltiq/services/jobs.py`
- Modify: `apps/api/joltiq/routers/agents.py`
- Modify: `apps/api/joltiq/services/customer_messages.py`
- Modify: `apps/api/tests/test_e2e_desktop.py`
- Modify: `apps/api/tests/test_platform_features.py`

**Interfaces:**
- Produces postcondition: `device_outlook_no_matching_crash_since` using Task 3’s inspector in read-only mode.
- Produces endpoint: `POST /api/v1/agents/runs/{run_id}/symptom-check` with `{result: "pdf_opened" | "still_crashes" | "cannot_test"}`.
- Produces handler/jobs: `agent.symptom_observation` after 60 seconds, `agent.symptom_reminder` after 10 minutes, and `agent.symptom_escalation` after 24 hours.
- Produces outcome progression `FIX_APPLIED_VERIFIED` → `AWAITING_SYMPTOM_CHECK` → `RESOLVED_VERIFIED` or escalation.

- [ ] **Step 1: Write two-stage verification tests**

Assert config/process checks alone produce `FIX_APPLIED_VERIFIED` and never `RESOLVED`; customer `pdf_opened` schedules observation; a matching crash fails; no crash after an actual attempt resolves only when log health is trustworthy; disabled, unreadable, stale, or health-unknown logging produces `UNKNOWN`; `cannot_test` keeps the ticket open; arbitrary chat text cannot impersonate the structured response. At 10 minutes send exactly one reminder, keep the ticket open, and release control; at 24 hours without a structured response escalate to a technician as `verification_timeout`.

- [ ] **Step 2: Verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_platform_features.py -q`

Expected: FAIL because the current verifier resolves immediately after config/process checks.

- [ ] **Step 3: Add the symptom-check endpoint and authorization**

Allow only the session/ticket customer to submit one structured response. Persist response timestamp and audit event. Repeated identical submissions are idempotent; conflicting submissions are rejected.

- [ ] **Step 4: Add the observation jobs**

Queue the crash-event read-back after 60 seconds using the attempt timestamp and exact module from the bound procedure. Require Task 3’s explicit event-log health evidence before interpreting “no matching crash” as clean. Schedule one 10-minute reminder and a 24-hour escalation deadline when entering the pause; jobs are idempotent and revoked by a terminal structured response.

- [ ] **Step 5: Enforce final resolution semantics**

Only `pdf_opened` plus a verified healthy and clean post-attempt observation transitions run/ticket to resolved and `resolution_verified=true`. `UNKNOWN` never resolves. `still_crashes` escalates immediately; `cannot_test` and no response retain `FIX_APPLIED_VERIFIED`, release control, and follow the reminder/24-hour escalation schedule.

- [ ] **Step 6: Run focused tests**

Run: `cd apps/api && .venv/bin/pytest tests/test_e2e_desktop.py tests/test_platform_features.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/services apps/api/joltiq/routers/agents.py apps/api/tests/test_e2e_desktop.py apps/api/tests/test_platform_features.py
git commit -m "feat: verify the original Outlook symptom"
```

### Task 9: Deliver Exact Portal, Approval, Tray, Pause, and Failure Copy

**Files:**
- Modify: `apps/api/joltiq/services/customer_messages.py`
- Modify: `apps/api/joltiq/services/devices.py`
- Modify: `apps/api/joltiq/routers/sessions.py`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentRuntime.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/Program.cs`
- Modify: `apps/web/src/components/types.ts`
- Modify: `apps/web/src/components/RunPanel.tsx`
- Modify: `apps/web/src/app/(app)/sessions/[id]/page.tsx`
- Modify: `tests/e2e/web/ui_smoke.py`
- Test: `apps/api/tests/test_platform_features.py`

**Interfaces:**
- Consumes: signed `SupportSession.request_details` in the `session.begin` envelope.
- Produces: native consent model containing exact server-signed title/body/add-in fields; the companion does not invent or paraphrase them.
- Produces UI actions for symptom response and rollback approval using Tasks 7–8 endpoints.

- [ ] **Step 1: Write API/copy contract tests**

Assert every spec message is returned at its named pause/failure, contains the exact add-in display name and ProgID where required, and is absent from unrelated workflows. Include the one-time 10-minute reminder, 24-hour technician handoff, unreadable-log `UNKNOWN`, locally missing rollback record, and `RESTORED_PENDING_RESTART` wording. Reject overlong/untrusted display fields at procedure publication.

- [ ] **Step 2: Extend the real-browser smoke test**

Exercise diagnostic consent, exact step approvals, `FIX_APPLIED_VERIFIED`, symptom buttons, deferred-test state, partial failure, and rollback offer. Assert accessible names and mobile rendering.

- [ ] **Step 3: Verify RED**

Run: `cd apps/api && .venv/bin/pytest tests/test_platform_features.py -q`

Expected: FAIL because current UI and companion use generic messages.

- [ ] **Step 4: Make server-signed copy the source of truth**

Build messages from validated procedure fields in `customer_messages.py`, put them in session request details/envelopes, and render them verbatim in portal and tray. Retain safe default deny on Enter/Escape and consent timeout.

- [ ] **Step 5: Add customer controls and outcome labels**

Render separate diagnosis, partial application, fix applied, and resolved stages; symptom response buttons; remaining-change/rollback status; connection/stop/expiry/revocation explanations.

- [ ] **Step 6: Run web checks**

Run: `cd apps/web && npm run typecheck && npm run build`

Run against a running stack: `python tests/e2e/web/ui_smoke.py http://127.0.0.1:3000 ./shots`

Expected: typecheck/build PASS and smoke test records all required pages without page errors or 5xx responses.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq apps/api/tests apps/windows-agent/src apps/web/src tests/e2e/web/ui_smoke.py
git commit -m "feat: show exact Windows support and recovery states"
```

### Task 10: Build a Real-Windows Acceptance Fixture and Evidence Bundle

**Files:**
- Create: `apps/windows-agent/tests/JoltIQ.OutlookCrashFixture/`
- Create: `apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1`
- Create: `apps/windows-agent/scripts/acceptance/EvidenceBundle.psm1`
- Create: `apps/windows-agent/scripts/acceptance/cases.json`
- Create: `docs/operations/windows-outlook-pdf-acceptance.md`
- Modify: `apps/windows-agent/scripts/test-on-windows.ps1`
- Modify: `.gitignore`

**Interfaces:**
- Produces an authorized, harmless fixture that registers a throwaway add-in identity and emits controlled matching/nonmatching Application events without modifying a real Outlook profile.
- Produces a separate licensed Microsoft 365 classic Outlook acceptance case using a harmless real COM add-in in a disposable test profile; no production mailbox or customer data is used.
- Produces one redacted evidence directory per case plus `manifest.json` and aggregate `windows-acceptance.json`.
- Produces a script exit code of zero only when every required case has evidence and expected server/device/ticket states.

- [ ] **Step 1: Write fixture self-tests and evidence-schema tests**

Test snapshot preflight, exact OS/Outlook/agent/procedure versions, registry pre/post capture, audit-chain result, ticket/run/session outcome, event record, consent/failure screenshots, stop timings, final desktop/process state, and secret redaction. A missing evidence class must fail the harness.

- [ ] **Step 2: Verify RED on Windows**

Run: `pwsh apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1 -List -ValidateHarness`

Expected: FAIL until fixture and required-case manifest exist.

- [ ] **Step 3: Implement the fixture and harness**

Cover the complete failure matrix from the spec. Automate cases that do not require secure-desktop interaction; guide and timestamp the human steps for locked screen, consent dialogs, PDF attempt, save prompt, and STOP timing. Include log-disabled, log-unreadable, enrolled-user mismatch at write time, server-supplied rollback-state rejection, missing local rollback record, remembered-fix bypass, screen-control dispatch refusal, and two-admin MFA publication cases.

- [ ] **Step 4: Keep test artifacts out of source control**

Ignore generated VM/evidence output while retaining schemas, redacted golden examples, and scripts. Never collect email bodies, real documents, credentials, or full unredacted event messages.

- [ ] **Step 5: Run internal Windows 11 repeatability acceptance**

Run five clean-snapshot happy paths across two VM images and every failure case. Save the aggregate evidence outside Git and attach only redacted results/checksums to the release record.

Run at least one additional happy path on a licensed Microsoft 365 classic Outlook installation with a harmless real COM add-in and disposable test profile. Prove the real process closes safely, restarts, becomes responsive, applies the setting, and can be restored/restarted from the PC’s local journal. Record the Outlook build/channel and add-in identity in the redacted evidence manifest.

- [ ] **Step 6: Run the Windows 10 release acceptance**

Run one complete happy path plus platform-specific registry/session/safe-close cases on Windows 10 x64 build 19041+.

- [ ] **Step 7: Commit harness code and documentation**

```bash
git add apps/windows-agent/tests/JoltIQ.OutlookCrashFixture apps/windows-agent/scripts docs/operations/windows-outlook-pdf-acceptance.md .gitignore
git commit -m "test: add real Windows Outlook acceptance harness"
```

### Task 11: Enforce Windows CI, Packaging, and Signing Gates

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/windows-release.yml`
- Modify: `apps/windows-agent/scripts/build.ps1`
- Modify: `apps/windows-agent/scripts/sign.ps1`
- Modify: `apps/windows-agent/installer/Product.wxs`
- Modify: `apps/windows-agent/scripts/test-on-windows.ps1`
- Test: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`

**Interfaces:**
- Produces CI artifacts: Core results, Windows xUnit TRX, unsigned internal MSI, SHA-256 manifest, and acceptance-harness validation.
- Produces release artifacts only when tray, broker, and MSI Authenticode signatures are valid and timestamped.

- [ ] **Step 1: Add build-script failure tests/checks**

Make build fail on compile warnings, test failures, missing MSI inputs, missing checksum, an external-release request without signing, or an invalid binary/MSI signature.

- [ ] **Step 2: Verify current Windows CI failure or capture its baseline**

Run the `windows-agent` job on `windows-latest`; save exact restore/compile/test/package diagnostics. Do not infer Windows success from macOS cross-targeting.

- [ ] **Step 3: Strengthen CI artifacts and gates**

Run all Windows projects and harness schema validation, upload TRX/build logs/checksums even on failure, and keep unsigned CI output labelled internal-only.

- [ ] **Step 4: Add the signed release workflow**

Use environment-protected signing secrets/certificate access, verify timestamp/signatures after signing, and refuse to publish on any invalid signature. Do not create or commit certificates or secret values.

- [ ] **Step 5: Verify install lifecycle on a clean VM**

Test install, Start menu/tray, broker ACL/service, repair, upgrade, rollback, and uninstall. Confirm the installer does not silently claim customer readiness when unsigned.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows apps/windows-agent/scripts apps/windows-agent/installer apps/windows-agent/tests
git commit -m "ci: gate Windows companion and signed releases"
```

### Task 12: Full Regression, Security Review Inputs, and Honest Status

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `TEST_PLAN.md`
- Modify: `RELEASE_CHECKLIST.md`
- Modify: `docs/operations/runbook.md`
- Create: `docs/operations/windows-outlook-pdf-evidence-index.md`

**Interfaces:**
- Consumes all prior task outputs.
- Produces an evidence index with hashes/locations and an explicit distinction among implemented, simulator-tested, Windows-CI-tested, Windows-VM-accepted, signed, and externally reviewed.

- [ ] **Step 1: Run the complete API suite**

Run: `cd apps/api && .venv/bin/pytest`

Expected: all tests pass; report exact passed/skipped/failed counts.

- [ ] **Step 2: Run API static/security checks**

Run: `cd apps/api && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq && .venv/bin/pip-audit -r requirements.lock`

Expected: zero lint/type errors; dependency findings are either zero or explicitly recorded and triaged.

- [ ] **Step 3: Run web verification**

Run: `cd apps/web && npm run typecheck && npm run build && npm audit --omit=dev --audit-level=high`

Expected: exit 0 for every command.

- [ ] **Step 4: Run cross-platform companion verification**

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Expected: all Core tests pass.

- [ ] **Step 5: Run Windows verification and acceptance**

Run on Windows: `pwsh apps/windows-agent/scripts/build.ps1 -Version <version>` and the Task 10 acceptance command.

Expected: compile/tests/MSI pass; five Windows 11 snapshot happy paths, one Windows 10 path, and every failure case have complete evidence. If unavailable, mark the work `IMPLEMENTED_NOT_TESTED`/`BLOCKED_EXTERNAL_DEPENDENCY`; do not claim completion.

- [ ] **Step 6: Perform a focused security review**

Review two-admin MFA publication and procedure-hash binding, learned/chat fix non-executability, Windows screen-control exclusion, registry policy precedence, tray/SYSTEM separation, enrolled/interactive SID binding, local-only rollback authorization, result journal/outbox, event-log health, stop races, session identity, redaction, signed-copy origin, and late-result reconciliation. Record open risks for external review.

- [ ] **Step 7: Update project truthfully**

Replace stale counts/status only with fresh evidence. State whether the installer is unsigned internal-only or signed for pilot. Preserve the separate browser/SaaS priority and identify secure `browser.upload` as the next implementation plan after Windows internal acceptance.

- [ ] **Step 8: Commit**

```bash
git add PROJECT_STATUS.md README.md SECURITY.md TEST_PLAN.md RELEASE_CHECKLIST.md docs/operations
git commit -m "docs: record Windows Outlook MVP evidence"
```

## Plan Self-Review

- Spec coverage: procedure integrity, diagnosis, unsupported environments, partial failure, safe restart, retry/idempotency, disconnects, STOP, consent/revocation, platform matrix, messages, signing boundary, real-Windows evidence, and macOS separation each map to a task.
- Step scan: each implementation task starts with a failing behavior test, names the production interface, verifies the focused result, and commits an independently reviewable increment.
- Type consistency: `procedure_hash`, `outcome_stage`, `request_details`, `outlook.pdf_crash.inspect`, local mutation records, `source_command_id`, `RESTORED_PENDING_RESTART`, and symptom-result values are defined once and consumed consistently in later tasks.
- Review focus: the high-risk classes are pinned to explicit tests in Tasks 2–8 and real-Windows evidence in Task 10; STOP races are Tasks 6/10 and false symptom resolution is Task 8.
- Proportion: Outlook-specific OS logic is isolated in Windows classes; generic procedure, state, approval, idempotency, rollback, and verification behavior remains reusable for future Windows and browser/SaaS workflows.
