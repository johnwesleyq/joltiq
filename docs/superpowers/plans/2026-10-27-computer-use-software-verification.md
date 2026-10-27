# Computer-Use Software Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Independently verify the approved Adobe-removal computer-use workflow from Windows inventory and resolve its ticket only when Acrobat is absent and Adobe Genuine Service remains present.

**Architecture:** When the screen-control model finishes the narrowly recognized Adobe-removal goal successfully, the server does not end the run immediately. It sends two signed read-only `app.info` commands through the already consented Windows companion, evaluates their device-authored results without another model call, and transitions the linked ticket to verified resolution only when both checks pass. Other computer-use goals retain their existing behavior.

**Tech Stack:** Python, FastAPI service layer, PostgreSQL JSON state, existing signed Windows device protocol, pytest

**Spec:** `docs/superpowers/specs/2026-10-21-joltiq-computer-on-prem-design.md`

## Global Constraints

- The model cannot select, alter, or waive the independent verification contract.
- Verification uses the existing read-only `app.info` device action and the same active, locally consented support session.
- Any missing, failed, malformed, or contradictory device result fails closed and never resolves the ticket.
- Only the narrowly recognized Adobe Acrobat removal goal is eligible in this slice.
- No database migration or new device privilege is introduced.

## Review Focus

- Acrobat still present must leave the ticket unresolved.
- Adobe Genuine Service missing must leave the ticket unresolved.
- Device command failure or malformed inventory must fail closed.
- An unrelated goal must not acquire Adobe-specific behavior.
- Duplicate or late device results must not resolve twice.

---

### Task 1: Server-side completion verifier

**Files:**
- Modify: `apps/api/joltiq/services/computer_use.py`
- Modify: `apps/api/tests/test_computer_use.py`

**Interfaces:**
- Consumes: a successful `finish` tool call, `ComputerUseRun.goal`, and device-authored `app.info` results.
- Produces: two `verify.app_absent` / `verify.app_present` steps, a verified run summary, and a linked ticket transition with `resolution_verified=true` only on full success.

- [ ] **Step 1: Write failing end-to-end tests** proving the eligible Adobe goal pauses for both inventory commands, resolves only when Acrobat is absent and Adobe Genuine Service is present, and fails closed for contradictory inventory.
- [ ] **Step 2: Run the focused tests and verify they fail because completion verification is not dispatched.**
- [ ] **Step 3: Implement the fixed verification contract, sequential device dispatch, strict result parsing, verified ticket transition, and failure summary.**
- [ ] **Step 4: Run the focused tests and the complete computer-use test module.**
- [ ] **Step 5: Commit only this task's files.**

### Task 2: Runtime and physical-device verification

**Files:**
- Modify only if a regression requires an in-scope correction.
- Record: `docs/evidence/computer-use/` after a successful physical run.

**Interfaces:**
- Consumes: Task 1's verifier and the enrolled `JOHNW` companion.
- Produces: automated regression evidence and, when the device is available, a physical Windows inventory verification result.

- [ ] **Step 1: Run computer-use, device-protocol, ticket, model-adapter, and security tests.**
- [ ] **Step 2: Rebuild/restart API and worker containers and verify health.**
- [ ] **Step 3: Run the eligible verification flow on the physical Windows PC; require fresh local consent if a new run is needed.**
- [ ] **Step 4: Record the exact run, command results, ticket state, and any remaining blocker without overstating readiness.**
