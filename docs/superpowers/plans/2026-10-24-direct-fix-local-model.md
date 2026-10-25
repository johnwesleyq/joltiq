# Direct Fix with Customer-Owned Local Model — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-10-24-direct-fix-local-model-design.md`

## Global constraints

- Work only in the existing `feat/direct-device-help` worktree.
- Use test-driven development: observe each new test fail before implementation.
- Preserve the existing signed command protocol, local consent, STOP, frame binding, action allowlist, and approval controls.
- Do not use the deterministic fixture as evidence for the Adobe Sign acceptance case.
- Do not claim the physical test passed unless the Windows screen and run record prove it.

## Task 1: Local Ollama computer-use provider

**Files:** `apps/api/joltiq/config.py`, `apps/api/joltiq/services/ai/providers.py`, `apps/api/joltiq/services/ai/router.py`, `apps/api/joltiq/services/ai/computer.py`, `apps/api/joltiq/routers/admin.py`, `apps/api/tests/test_computer_use.py`

1. Add failing tests proving an enabled/approved Ollama provider is eligible without an API key, while disabled/unapproved configurations are not.
2. Add failing adapter tests for screenshot conversion, one-action structured output, token metadata, malformed JSON, and unsupported actions.
3. Implement deployment-owned Ollama base URL/model settings, admin metadata/connectivity, routing, and the adapter.
4. Run focused API tests.

**Expected:** focused tests pass; production still refuses external computer-use models but permits explicitly approved local Ollama.

## Task 2: Direct tray Guide/Fix bridge

**Files:** `apps/api/joltiq/routers/devices.py`, `apps/api/joltiq/services/computer_use.py`, `apps/windows-agent/src/JoltIQ.Agent.Core/AgentClient.cs`, `apps/windows-agent/src/JoltIQ.Agent.Tray/SupportConversation.cs`, API and Windows tests

1. Add failing API tests for a signed device starting Guide or Fix from its own ticket, idempotency, and cross-device refusal.
2. Implement a signed endpoint that creates/consents a desktop session and starts a run for the ticket owner. Local native run consent remains pending.
3. Add failing Windows controller tests for mode availability and start behavior.
4. Add Guide/Fix buttons and active-run status to the tray, backed by the signed endpoint.
5. Run focused API and Windows tests.

**Expected:** the same tray conversation exposes Guide/Fix, and choosing either produces a native local-consent prompt before screen capture.

## Task 3: Configure and run the physical acceptance case

1. Pull the configured multimodal Ollama model on the Mac host and verify its local API.
2. Configure the API container to reach only the host Ollama endpoint; enable and approve Ollama for the development tenant.
3. Rebuild/deploy the signed-development Windows companion to the enrolled PC.
4. Start Fix from ticket #1000, accept native consent, and observe the run.
5. Pause for explicit approval immediately before any add-in install/enable action.
6. Capture run/audit evidence and record the honest outcome.

**Expected:** either Adobe Sign becomes visible in Outlook and the run verifies success, or the run fails closed with a concrete, recorded blocker. No simulated success counts.

## Review focus

- No external-model bypass, no tenant-controlled SSRF URL, no API-key requirement for local Ollama.
- No screen capture before native consent.
- The device can start control only for its own ticket and owner.
- All model actions still pass server and companion policy checks.
- Consequential changes still require a distinct in-run approval.
