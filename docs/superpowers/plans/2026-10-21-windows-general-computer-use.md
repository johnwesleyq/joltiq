# Windows General Computer Use Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Windows-native observe → decide → act → verify driver plus distinct Guide me and Fix it for me modes through JoltIQ's existing signed device protocol.

**Architecture:** Extend the current durable computer-use run with an explicit mode. A Windows-native adapter captures the interactive virtual desktop, validates target/focus state through UI Automation, injects bounded input through `SendInput` only in Fix mode, and renders non-interactive callouts in Guide mode. `AgentRuntime` durably journals an action and its protected screenshot before transmission, uploads that screenshot before the signed command result, and never repeats an already-journaled input after restart; the existing server loop, local policy, control lease, approvals, audit, storage, and verifier remain authoritative.

**Tech Stack:** .NET 8 (`net8.0-windows`, WinForms, Windows Graphics/GDI capture, UI Automation, Win32 `SendInput`), FastAPI, SQLAlchemy/PostgreSQL, Pydantic, Next.js/TypeScript, pytest, xUnit/self-test harness, WiX v5.

**Spec:** `docs/superpowers/specs/2026-10-21-joltiq-computer-on-prem-design.md`

## Global Constraints

- Windows 10 and 11, 64-bit, one unlocked interactive user; secure desktop, lock screen, user switches, shared/RDP sessions, and ambiguous identity fail closed.
- Guide mode may capture and overlay guidance but must be structurally unable to inject keyboard or mouse input.
- Fix mode requires portal goal approval, native local consent, an active AI control lease, a valid device-bound entitlement hook, local policy approval, and a current screenshot coordinate frame.
- Every mutating input command is signed with the exact frame generation and display-layout hash it was planned against; the Windows companion rechecks both immediately before input.
- Password, OTP, card, private-key, secure-desktop, prohibited key-combination, and unbound-current-focus input is refused on-device.
- STOP prevents the next queued input within 500 ms p95 and 2 seconds maximum on a healthy endpoint and reports any already-started effect.
- No shell, arbitrary registry, arbitrary service, arbitrary installer, or policy action is introduced; structured privileged mutations remain published-procedure actions.
- Customer screenshots and input results remain on the customer control node and use the existing signed protocol, tenant storage, retention, and audit paths.
- Tests are written first; every task ends with an independently reviewable commit.

## Review Focus

- A display is added, removed, rotated, or rescaled after observation: Task 3 tests require frame-generation mismatch refusal before input.
- Windows changes focus between approval and execution: Task 4 tests require bound-target revalidation or refusal, never typing into current unknown focus.
- STOP races a mouse-down, drag, or multi-key action: Tasks 4 and 6 test cancellation, input release, and no later queued input.
- Guide mode receives or replays an input command: Tasks 1, 5, and 7 test structural refusal at API, server dispatcher, local policy, and provider layers.
- Capture reaches lock/UAC secure desktop or another user's session: Tasks 3, 4, and 9 test no screenshot/input, session pause, and evidence-backed failure.

---

### Task 1: Persist mode and the two-stage consent state

**Files:**
- Create: `apps/api/migrations/versions/0006_computer_use_mode.py`
- Modify: `apps/api/joltiq/models/operations.py`
- Modify: `apps/api/joltiq/routers/computer_use.py`
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/api/tests/test_computer_use.py`

**Interfaces:**
- Consumes: existing `ComputerUseRun`, `start(...)`, `ser(...)`, and `/sessions/{sid}/computer-use`.
- Produces: `ComputerUseRun.mode: "guide" | "fix"`; `ComputerUseRun.local_consent_state: "pending" | "approved" | "declined"`; `StartIn.mode`; `start(..., mode: str)`; serialized immutable mode/consent state; `waiting_local_consent` run state between portal approval and execution.

- [ ] **Step 1: Add failing API tests**

Add `test_customer_selects_guide_or_fix_mode`, `test_invalid_mode_and_mode_change_are_refused`, and `test_portal_approval_waits_for_native_run_consent`. Assert the default is `fix` for existing callers, explicit `guide` persists, unknown values return 422, mode cannot change after creation, and neither mode dispatches a screenshot after portal approval alone.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py -k 'selects_guide_or_fix or invalid_mode or portal_approval_waits' -v`

Expected: FAIL because `mode` is not accepted or returned.

- [ ] **Step 3: Add the migration and typed mode contract**

Add non-null `mode` and `local_consent_state` columns with safe server defaults and database check constraints, Pydantic literals, service validation, audit data, and serialized output. Portal approval moves the run to `waiting_local_consent`; only the device approval implemented in Task 5 may call `_begin` and dispatch the first capture. Decline ends the run without a command.

- [ ] **Step 4: Run focused and migration tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py tests/test_tenancy_rls.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/migrations/versions/0006_computer_use_mode.py apps/api/joltiq/models/operations.py apps/api/joltiq/routers/computer_use.py apps/api/joltiq/services/computer_use.py apps/api/tests/test_computer_use.py
git commit -m "feat: add guide and fix computer-use modes"
```

### Task 2: Carry screenshot evidence through the native runtime

**Files:**
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentRuntime.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/CommandJournal.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/DpapiCommandJournal.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Simulator/Program.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`

**Interfaces:**
- Consumes: `IActionProvider.ExecuteAsync`, `ICommandJournal`, `/commands/{id}/screenshot`, `/commands/{id}/result`.
- Produces: `ActionResult(JsonObject Data, byte[]? Screenshot, string? ScreenshotMediaType)`; protected per-command evidence records/files referenced by `CommandJournalEntry`; `PendingCommandReport` with evidence metadata; `AgentClient.SendCommandScreenshotAsync(string commandId, byte[] bytes, string mediaType, CancellationToken)`.

- [ ] **Step 1: Add failing Core tests for result ordering and retries**

Add tests proving screenshot bytes are uploaded before the result, a failed screenshot upload prevents success reporting, a process restart after native input but before either upload resumes the outbox without re-executing the action, duplicate delivery never repeats input, corrupt/missing protected evidence fails closed, acknowledgement removes retained evidence, and non-screen actions remain JSON-only.

- [ ] **Step 2: Run the Core self-test and verify RED**

Run on Windows or the .NET SDK container: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Expected: FAIL because the action contract cannot return binary evidence.

- [ ] **Step 3: Introduce `ActionResult` and signed screenshot upload**

Change `IActionProvider.ExecuteAsync(...)` to return `Task<ActionResult>`. Update every provider/simulator/test implementation. Add `SendCommandScreenshotAsync`; sign the raw body with `Content-Type: image/png|image/jpeg`. Extend the journal so `MarkCompleted` atomically records the terminal JSON result plus a reference, media type, length, and digest for a DPAPI-protected per-command evidence blob. Persist that completed state immediately after the native action and before network transmission. The outbox uploads and verifies the screenshot first, sends the result second, and deletes the local evidence only after server acknowledgement. On restart, a completed entry resumes transmission; it never invokes the provider again. If protected evidence cannot be stored or validated, report a local failed/uncertain outcome and stop the session rather than repeating input.

- [ ] **Step 4: Run Core and Windows provider tests**

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Run on Windows: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release`

Expected: PASS with no warning.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/src apps/windows-agent/tests
git commit -m "feat: upload native computer-use screenshots"
```

### Task 3: Capture and map the interactive Windows desktop

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/DesktopFrame.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/DesktopCapture.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/DesktopSessionGuard.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/JoltIQ.Agent.Windows.csproj`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.Tests/DesktopCaptureTests.cs`

**Interfaces:**
- Consumes: interactive-session facts from `InteractiveSessionInspector`; `max_long_edge` from `screen.capture`.
- Produces: `IDesktopCapture.CaptureAsync(int maxLongEdge, CancellationToken) -> DesktopFrame`; `DesktopFrame` contains encoded PNG, scaled width/height, virtual-desktop bounds, scale factors, display layout hash, and monotonically increasing frame generation.

- [ ] **Step 1: Add failing tests for frame metadata and unsafe sessions**

Test negative virtual-screen coordinates, multiple monitors, DPI scaling, max-edge resizing, stable layout hash, changed-layout generation, locked screen, multiple sessions, RDP/shared sessions, and non-matching enrolled SID.

- [ ] **Step 2: Run Windows tests and verify RED**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter DesktopCaptureTests`

Expected: FAIL because capture interfaces do not exist.

- [ ] **Step 3: Implement capture and session guard**

Use the interactive tray process and Windows virtual-screen coordinates. Encode PNG in memory, preserve aspect ratio, calculate reversible coordinate mapping, and return no pixels when the session guard cannot prove one unlocked matching user. Detect layout/DPI/orientation changes through the layout hash and increment the generation.

- [ ] **Step 4: Run the focused Windows tests**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter DesktopCaptureTests`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/src/JoltIQ.Agent.Windows apps/windows-agent/tests/JoltIQ.Agent.Tests/DesktopCaptureTests.cs
git commit -m "feat: capture the consented Windows desktop"
```

### Task 4: Inject bounded input with execution-time safety checks

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/DesktopInput.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/InputTargetGuard.cs`
- Create: `apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop/NativeInput.cs`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.Tests/DesktopInputTests.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/api/tests/test_computer_use.py`

**Interfaces:**
- Consumes: `DesktopFrame` mapping/generation, the current `LocalSession` cancellation token, and server-side computer-use run display metadata.
- Produces: required `frame_generation` and `layout_hash` fields on every `input.*` action except read-only `input.wait`/`input.cursor`; `IDesktopInput.ClickAsync`, `DragAsync`, `MoveAsync`, `MouseAsync`, `TypeAsync`, `KeyAsync`, `ScrollAsync`, `CursorAsync`; every mutating method requires the expected frame generation and layout hash.

- [ ] **Step 1: Add failing safety tests**

Test registry rejection when the frame binding is absent, server enrichment from the latest run display, coordinate mapping, out-of-bounds refusal, stale frame/layout refusal, guide-mode refusal, password/OTP/card/secure UI Automation target refusal, unknown-current-focus typing refusal, prohibited key chords, key/mouse release on cancellation, and no next input after STOP.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter DesktopInputTests`

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py -k frame -v`

Expected: FAIL because native input is absent.

- [ ] **Step 3: Implement UI Automation target binding and `SendInput`**

Require the action registry to validate frame bindings and have `_dispatch` copy them only from the server's latest accepted screenshot metadata, never from model input. Bind click/type operations to the current UI Automation element at the approved coordinate/focus. Refuse `IsPassword`, secure desktop, missing/changed element identity, and unknown focus. Use `SendInput` only after guard approval; use `try/finally` to release held keys/buttons on cancellation. Re-check session/user/layout/license hook immediately before the native call.

- [ ] **Step 4: Run focused and Core policy tests**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter DesktopInputTests`

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py -k frame -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/src/JoltIQ.Agent.Windows/Desktop apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs apps/windows-agent/tests
git add apps/api/joltiq/services/actions/registry.py apps/api/joltiq/services/computer_use.py apps/api/tests/test_computer_use.py
git commit -m "feat: add policy-bound Windows input control"
```

### Task 5: Add per-run native consent and a Guide me overlay with no input authority

**Files:**
- Create: `apps/windows-agent/src/JoltIQ.Agent.Tray/GuideOverlay.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/Program.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/AgentRuntime.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`
- Modify: `apps/api/joltiq/services/devices.py`
- Modify: `apps/api/joltiq/routers/devices.py`
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/api/joltiq/services/actions/registry.py`
- Modify: `apps/api/tests/test_computer_use.py`
- Modify: `apps/api/tests/test_platform_features.py`
- Create: `apps/windows-agent/tests/JoltIQ.Agent.Tests/GuideOverlayTests.cs`

**Interfaces:**
- Consumes: a signed `computer_use.begin` envelope for the exact run/mode/goal; new computer-use actions `guide.highlight` and `guide.clear` with frame generation, rectangle/point, short instruction, and expected control label.
- Produces: signed `computer_use.begin|end` envelopes; action envelopes bound to `computer_use_run_id` and mode; a local per-run authorization grant; click-through, no-activation, always-on-top overlay; typed device response `done|switch_to_fix`; native **I did it**, **Switch to Fix**, and **Stop** callbacks through device response/stop endpoints.

- [ ] **Step 1: Add failing registry, mode, and overlay tests**

Prove guide actions validate bounded coordinates/text and cannot appear in published procedures. Prove portal approval alone cannot dispatch actions; the signed per-run begin envelope displays the exact goal/mode; native accept authorizes only that run/mode; decline ends it; missing, mismatched, replayed, expired, or ended grants fail closed; every computer-use action carries the matching signed run ID/mode; input actions are denied locally for Guide grants; the overlay never activates or captures input; device responses reject ambiguous combinations; and STOP closes it immediately.

- [ ] **Step 2: Run API and Windows tests and verify RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py tests/test_procedures.py -k 'guide or computer_use' -v`

Run on Windows: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter GuideOverlayTests`

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Expected: FAIL because guide actions and overlay do not exist.

- [ ] **Step 3: Implement the guide-only protocol and overlay**

Add typed guide actions marked `computer_use=True`. Have `pending_for` send a signed `computer_use.begin` for a portal-approved run awaiting native consent. The tray shows its exact goal and Guide/Fix permissions, then posts accept/decline to a device-authenticated run-consent endpoint. Accept records the run's native consent, installs a local grant, and starts the first capture; decline ends it. Bind all later action envelopes to that run ID and immutable mode, and send `computer_use.end` when the run terminates so the tray clears the grant/overlay. Do not trust UI state or action parameters for mode. Local policy accepts `guide.*` only for an active Guide grant and rejects every `input.*`; Fix grants reject `guide.*`. Render the highlight without activation. Extend the device response schema with `switch_to_fix: bool`, require exactly one applicable response, and wire only the three declared customer callbacks.

- [ ] **Step 4: Run the focused tests**

Run the commands from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/joltiq/services/actions/registry.py apps/api/joltiq/services/devices.py apps/api/joltiq/routers/devices.py apps/api/tests apps/windows-agent/src apps/windows-agent/tests
git commit -m "feat: add input-free Windows guide mode"
```

### Task 6: Expose screen and input capabilities through the Windows provider

**Files:**
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Windows/WindowsActionProvider.cs`
- Modify: `apps/windows-agent/src/JoltIQ.Agent.Tray/Program.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Tests/WindowsProviderTests.cs`
- Modify: `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest/Program.cs`

**Interfaces:**
- Consumes: Tasks 2-5 `ActionResult`, desktop capture/input, guide overlay bridge, and session mode.
- Produces: advertised capabilities for `screen.capture`, `input.*`, `url.open`, and `guide.*`; exact action dispatch with post-action capture and display metadata.

- [ ] **Step 1: Add failing provider integration tests**

Assert each capability appears only when its native dependency is healthy; capture returns PNG and display metadata; every successful input returns a post-action frame; cancelled/blocked input returns no false success; guide actions never call the input adapter; URL opening is HTTP(S)-only.

- [ ] **Step 2: Run provider tests and verify RED**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release --filter WindowsProviderTests`

Expected: FAIL because the provider does not advertise or dispatch computer-use primitives.

- [ ] **Step 3: Integrate desktop services into `WindowsActionProvider`**

Inject capture/input/overlay dependencies for tests. For mutating actions, capture the result state after execution. Include `width`, `height`, virtual bounds, layout hash, and frame generation in result data. Keep privileged broker capabilities unchanged.

- [ ] **Step 4: Run all Windows unit and Core tests**

Run: `dotnet test apps/windows-agent/tests/JoltIQ.Agent.Tests/JoltIQ.Agent.Tests.csproj -c Release`

Run: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release`

Expected: PASS with no warnings.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/src apps/windows-agent/tests
git commit -m "feat: connect Windows computer-use capabilities"
```

### Task 7: Orchestrate Guide mode without executing model input requests

**Files:**
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/api/joltiq/routers/devices.py`
- Modify: `apps/api/tests/test_computer_use.py`
- Modify: `apps/api/tests/test_device_protocol.py`

**Interfaces:**
- Consumes: persisted `run.mode`, computer tool proposals, `guide.highlight`, device `done|switch_to_fix|stop` responses.
- Produces: Guide dispatcher that translates the next model click/type intent into a non-input instruction/highlight, pauses for customer completion, requests a new screenshot, and requires a new Fix run/approval to switch modes.

- [ ] **Step 1: Add failing end-to-end Guide tests**

Use the scripted model to propose click/type actions. Assert the device receives only `screen.capture` and `guide.*`, never `input.*`; “I did it” causes clear + fresh capture + next model turn; stale highlight frames are rejected; replay is idempotent; switch-to-Fix terminates Guide, releases its control state, and creates a new pending Fix run that cannot dispatch until fresh portal approval and native consent are recorded.

- [ ] **Step 2: Run focused API tests and verify RED**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py tests/test_device_protocol.py -k guide -v`

Expected: FAIL because Guide mode currently follows the Fix dispatcher.

- [ ] **Step 3: Implement the Guide dispatcher**

Branch in `_execute` before `_dispatch`. Convert visual intent to a bounded guide target and redacted instruction, store it as a guide step, dispatch `guide.highlight`, then set `waiting_customer`. Never place the original input action in a device envelope. On completion, clear the overlay, capture, and continue. Audit every translation and refusal.

- [ ] **Step 4: Run the complete computer-use/security API suites**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_computer_use.py tests/test_device_protocol.py tests/test_security.py tests/test_procedures.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/joltiq/services/computer_use.py apps/api/joltiq/routers/devices.py apps/api/tests
git commit -m "feat: orchestrate input-free guided support"
```

### Task 8: Present Guide me and Fix it for me in the portal

**Files:**
- Modify: `apps/web/src/components/DrivePanel.tsx`
- Modify: `apps/web/src/components/types.ts`
- Modify: `apps/web/src/app/(app)/sessions/[id]/page.tsx`
- Modify: `tests/e2e/web/ui_smoke.py`

**Interfaces:**
- Consumes: `mode` API field and existing run/approval/response/stop endpoints.
- Produces: two-mode selector, mode-specific consent copy, guide instruction panel, visible highlight metadata, Fix plan/approval copy, persistent Stop/Take control/Switch mode controls.

- [ ] **Step 1: Add failing UI smoke assertions**

Assert the session page presents both modes, Guide copy says the customer performs every click, Fix copy says JoltIQ controls the PC, selecting Guide sends `mode=guide`, selecting Fix sends `mode=fix`, and active mode/STOP remain visible at mobile and desktop widths.

- [ ] **Step 2: Run typecheck and smoke test to verify RED**

Run: `cd apps/web && npm run typecheck`

Run with the local stack: `python tests/e2e/web/ui_smoke.py`

Expected: smoke assertions FAIL because the selector is absent.

- [ ] **Step 3: Implement the two-mode UI**

Split `DrivePanel` into focused mode-selection, live-screen, customer-pause, and step-log components if needed. Reuse the approved customer wording from the spec. Do not hide mode, consent, STOP, or current control holder behind a disclosure.

- [ ] **Step 4: Run web verification**

Run: `cd apps/web && npm run typecheck && npm run build`

Run: `python tests/e2e/web/ui_smoke.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src tests/e2e/web/ui_smoke.py
git commit -m "feat: add guide and fix support controls"
```

### Task 9: Add physical-Windows acceptance and STOP evidence

**Files:**
- Create: `apps/windows-agent/scripts/acceptance/Invoke-GeneralComputerUse.ps1`
- Create: `apps/windows-agent/scripts/acceptance/computer-use-cases.json`
- Modify: `apps/windows-agent/scripts/acceptance/EvidenceBundle.psm1`
- Modify: `apps/windows-agent/scripts/test-on-windows.ps1`
- Modify: `apps/windows-agent/scripts/tests/BuildGate.Tests.ps1`
- Create: `docs/operations/windows-general-computer-use-acceptance.md`
- Create: `docs/operations/windows-general-computer-use-evidence-index.md`

**Interfaces:**
- Consumes: installed tray companion, local scripted model, consented Windows test desktop, signed command/audit APIs.
- Produces: fail-closed evidence bundles and aggregate for happy paths, Guide refusal, Fix actions, identity/display/focus changes, protected input, STOP races, disconnect/replay, and recovery.

- [ ] **Step 1: Write the case catalog and failing harness contract tests**

Include clean Windows 10/11 runs; Notepad Guide and Fix; multi-monitor/DPI; layout change; lock/UAC; password/OTP target; focus theft; user mismatch; local decline; portal decline; STOP queued/before native/during drag; offline/expiry/duplicate; tray restart; and unsupported secure desktop. Require environment, session, command, screenshot, STOP timing, audit, desktop final state, and result evidence.

- [ ] **Step 2: Run the contract validator and verify RED**

Run on Windows: `powershell -executionpolicy bypass -file apps/windows-agent/scripts/test-on-windows.ps1 -ValidateComputerUseHarness`

Expected: FAIL until the complete catalog/evidence validator is implemented.

- [ ] **Step 3: Implement the Windows acceptance harness**

Follow the existing Outlook evidence conventions, PowerShell 5.1 compatibility gate, secret rejection, exact hashes, aggregate completeness, and snapshot-reset honesty. Do not claim a physical PC run is a clean-VM matrix.

- [ ] **Step 4: Run automated and physical acceptance**

Run: `powershell -executionpolicy bypass -file apps/windows-agent/scripts/build.ps1`

Run all catalog cases on disposable Windows 10/11 VMs, reset to named snapshots between cases. Expected: aggregate `passed: true`, STOP within target, zero secret-bearing evidence, and no unapproved input.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/scripts docs/operations/windows-general-computer-use-*.md
git commit -m "test: accept Windows general computer use"
```

### Task 10: Package, verify, and document the Windows release increment

**Files:**
- Modify: `apps/windows-agent/installer/Product.wxs`
- Modify: `apps/windows-agent/scripts/build.ps1`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/windows-release.yml`
- Modify: `packages/agent-protocol/PROTOCOL.md`
- Modify: `apps/desktop-agent/README.md`
- Modify: `PROJECT_STATUS.md`
- Modify: `RELEASE_CHECKLIST.md`

**Interfaces:**
- Consumes: completed Tasks 1-9 and existing signing/build gates.
- Produces: signed Windows companion containing the native driver; CI evidence; updated protocol and honest project status; retirement note for the placeholder Python screen adapter.

- [ ] **Step 1: Add failing build/package checks**

Require the tray artifact to advertise real `screen.capture`, `input.*`, and `guide.*` capabilities; require Windows xUnit, Core, API mode/guide tests, web tests, and acceptance contract tests; refuse external-ready output without valid Authenticode and timestamp evidence.

- [ ] **Step 2: Run the build gate and verify RED**

Run on Windows: `powershell -executionpolicy bypass -file apps/windows-agent/scripts/build.ps1`

Expected: FAIL until the installer, capability manifest, and workflow include the native driver.

- [ ] **Step 3: Update package, CI, protocol, and status documents**

Install the native assemblies/resources with the tray. Document exact session mode, frame generation, screenshot ordering, input refusal, overlay, STOP, and response schemas. Mark the Python desktop package non-production and remove any claim that screen/input is absent only after Windows evidence exists.

- [ ] **Step 4: Run full verification**

Run API: `cd apps/api && PYTHONPATH=. .venv/bin/pytest && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq`

Run web: `cd apps/web && npm run typecheck && npm run build && npm audit --omit=dev --audit-level=high`

Run Windows: `powershell -executionpolicy bypass -file apps/windows-agent/scripts/build.ps1`

Run acceptance contract and confirm the physical/VM evidence index contains exact hashes and no overstated claim.

Expected: all automated suites pass; signed artifact classification is accurate; external pilot remains blocked until the required VM aggregate and independent security review are attached.

- [ ] **Step 5: Commit**

```bash
git add apps/windows-agent/installer apps/windows-agent/scripts .github packages/agent-protocol apps/desktop-agent/README.md PROJECT_STATUS.md RELEASE_CHECKLIST.md
git commit -m "release: package Windows general computer use"
```

## Program boundary after this increment

This plan finishes the Windows-native observe → decide → act → verify surface and its Guide/Fix safety contract. It does not silently redefine that milestone as the whole commercial product. The approved product reaches its finite release target through four subsequent implementation plans, in this order:

1. Local ticket worker: detailed automatic first response, local-model gateway, diagnosis, knowledge retrieval, ticket-source connectors, and escalation.
2. On-prem commercial control plane: device-key seat licensing, connected/offline activation, signed feature/model updates, and network-boundary enforcement.
3. Browser/SaaS completion: isolated browser plus consented existing-browser routing, mixed local/browser tickets, protected customer handoffs, and SaaS acceptance cases.
4. macOS and GA: native macOS capture/input/overlay, signing/notarization, platform acceptance, security review, deployment/backup/monitoring runbooks, and release evidence.

No new product track is added after those four without a separate product decision. Windows can enter a controlled internal pilot after this plan and the real licensing/local-model dependencies are attached; unrestricted customer deployment still waits for the relevant later gates.
