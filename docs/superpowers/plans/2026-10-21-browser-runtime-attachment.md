# Browser Runtime Attachment Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind reusable browser upload procedures to one customer-selected same-ticket attachment without giving the AI file-selection authority.

**Architecture:** Add a typed browser procedure whose fingerprint contains a fixed runtime-binding rule. Freeze the customer's accessible attachment on the run, materialize it into the canonical plan before approval, and reuse the existing upload executor's independent content validation.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/PostgreSQL, pytest, Next.js/React/TypeScript, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-21-browser-runtime-attachment-design.md`

## Global Constraints

- The AI never selects an attachment.
- The resolved attachment UUID is covered by the plan hash and exact-step approval.
- No storage path or file content enters model context, approval records, or audit logs.
- Existing consent, policy, lease, idempotency, STOP, and execution-time validation remain mandatory.

## Review Focus

- Cross-ticket and cross-tenant attachment IDs must be non-enumerating refusals.
- Missing selection must never fall back to the newest or only attachment.
- Rebinding after approval must invalidate the old plan rather than mutate it.
- Deleted or corrupted selected content must fail before worker transfer.
- Legacy hard-coded procedures remain readable but new typed publication must require the binding rule.

---

### Task 1: Typed browser procedure and deterministic materializer

**Files:** `apps/api/joltiq/services/procedures.py`, `apps/api/joltiq/services/browser_uploads.py`, `apps/api/tests/test_security.py`

**Interfaces:** Produces a validated browser procedure contract and `materialize_selected_attachment(...)` returning exact executable steps.

- [ ] Add failing publication/materialization tests for the fixed binding, missing selection, hard-coded UUID refusal, and resolved plan parameters.
- [ ] Run the focused tests and confirm they fail for missing contracts.
- [ ] Implement the typed contract and materializer without relaxing the action registry.
- [ ] Run focused tests and commit.

### Task 2: Freeze selection on run and build canonical browser plans

**Files:** `apps/api/joltiq/routers/agents.py`, `apps/api/joltiq/services/agent.py`, `apps/api/tests/test_security.py`, `apps/api/tests/test_e2e_browser.py`

**Interfaces:** Consumes Task 1 materializer; produces a run context selection and exact approved plan.

- [ ] Add failing API tests for same-ticket selection, wrong-ticket refusal, missing selection, and immutable approval binding.
- [ ] Add `attachment_id` to start/chat input and freeze safe metadata after access checks.
- [ ] Route typed browser procedures through canonical planning and exact approval generation.
- [ ] Run focused API and real-Chromium FieldDesk tests and commit.

### Task 3: Portal selection and evidence

**Files:** `apps/web/src/components/TicketView.tsx`, `apps/web/src/components/types.ts`, web tests if present, `docs/operations/browser-upload-evidence.md`, `README.md`

**Interfaces:** Consumes attachment-aware chat API; produces an explicit customer file selection and updated evidence.

- [ ] Add a failing UI/type test where available, otherwise use typecheck as the pre-change contract failure.
- [ ] Add an attachment selector and include only its UUID in the chat request.
- [ ] Run web typecheck/build, full API/worker suites, lint, and update evidence with honest limits.
- [ ] Commit and perform whole-branch review.
