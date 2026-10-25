# OpenAI Responses Computer Use Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicitly enabled OpenAI Responses API provider to JoltIQ's existing action-by-action computer-control loop.

**Architecture:** A new adapter converts JoltIQ's normalized transcript and latest screenshot into a stateless Responses request with strict function tools and converts one returned function call back into the existing `Turn` interface. Existing routing, consent, approvals, signed commands, frame binding, stop controls, audit, and verification remain authoritative.

**Tech Stack:** Python, FastAPI, HTTPX, PostgreSQL provider configuration, pytest

**Spec:** `docs/superpowers/specs/2026-10-25-openai-responses-computer-use-design.md`

## Global Constraints

- Requests use `store: false` and never log API keys or screenshot bytes.
- Both deployment opt-in and tenant `enabled` plus `approved_for_tenant_data` gates are required.
- Exactly one normalized action is accepted per model turn; unsupported or malformed output fails closed.
- Scripted fixtures are never a silent fallback for a configured live OpenAI run.
- Existing customer approval and secure-input tools remain mandatory and unchanged.

## Review Focus

- Responses output with no function call must fail closed rather than falsely complete.
- Multiple function calls must be rejected because they bypass per-action verification.
- Invalid JSON arguments or an unknown function name must not reach the device.
- A missing screenshot must still allow a safe `screenshot` request but no invented coordinates.
- Production must require deliberate deployment and tenant approvals before sending tenant data externally.

---

### Task 1: OpenAI Responses adapter

**Files:**
- Modify: `apps/api/joltiq/services/ai/computer.py`
- Modify: `apps/api/joltiq/services/ai/providers.py`
- Test: `apps/api/tests/test_computer_use.py`

**Interfaces:**
- Consumes: `OpenAIComputer.turn(model, system, messages, display, max_tokens)` and the existing normalized transcript.
- Produces: an existing `Turn` containing exactly one supported `ToolUse` and usage metadata.

- [ ] **Step 1: Write failing contract tests** for request URL/auth, `store: false`, latest screenshot, strict supported tools, one-action normalization, usage parsing, malformed arguments, unknown actions, missing calls, and multiple calls.
- [ ] **Step 2: Run the focused tests and verify they fail because `OpenAIComputer` does not exist.**
- [ ] **Step 3: Implement the minimal Responses adapter and add `openai` to `SUPPORTED_PROVIDERS`.**
- [ ] **Step 4: Run the focused tests and verify they pass.**

### Task 2: Explicit routing and production gates

**Files:**
- Modify: `apps/api/joltiq/config.py`
- Modify: `apps/api/joltiq/services/ai/providers.py`
- Modify: `apps/api/joltiq/services/ai/router.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Test: `apps/api/tests/test_computer_use.py`
- Test: `apps/api/tests/test_security.py`

**Interfaces:**
- Consumes: `JOLTIQ_ALLOW_EXTERNAL_COMPUTER_USE_MODELS`, encrypted tenant OpenAI credentials, tenant approval, and AI budget routing.
- Produces: OpenAI as an eligible `computer_use` provider only when all gates are satisfied.

- [ ] **Step 1: Write failing routing and production-safety tests** for disabled deployment flag, missing tenant approval, explicit opt-in, default model, and no fixture fallback.
- [ ] **Step 2: Run the focused tests and verify the expected failures.**
- [ ] **Step 3: Add the OpenAI computer-use default, update gate semantics, and document/pass through the environment flag.**
- [ ] **Step 4: Run the focused tests and verify they pass.**

### Task 3: Regression and live-readiness verification

**Files:**
- Modify only if a failing regression requires an in-scope correction.

**Interfaces:**
- Consumes: the completed adapter and routing changes.
- Produces: recorded automated evidence plus an explicit list of live prerequisites.

- [ ] **Step 1: Run `tests/test_computer_use.py`, `tests/test_model_adapters.py`, and the relevant security tests.**
- [ ] **Step 2: Run the complete API test suite and report every failure.**
- [ ] **Step 3: Rebuild/restart API and worker containers and verify health.**
- [ ] **Step 4: Configure a real API key only through the encrypted admin/provider path, then run provider connectivity.**
- [ ] **Step 5: Run the physical Windows acceptance task and independently verify Acrobat removal plus Adobe Genuine Service retention.**
