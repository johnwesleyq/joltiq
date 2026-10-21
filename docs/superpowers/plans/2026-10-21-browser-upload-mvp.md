# Secure Browser Upload MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute consent-bound, approval-gated uploads of same-ticket attachments into deterministic file inputs in JoltIQ's isolated browser.

**Architecture:** The API resolves and revalidates a ticket attachment only after existing execution gates pass, then transfers bytes and integrity metadata to a bearer-authenticated worker endpoint. The worker independently enforces session state, authentication handoff, size, digest, filename, and file-input constraints before using a Playwright in-memory file payload; only safe metadata enters results and logs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/PostgreSQL RLS, HTTPX multipart, Playwright 1.56, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-browser-upload-mvp-design.md`

## Global Constraints

- Preserve the existing action registry: `browser.upload` is `SENSITIVE`, non-reversible, and accepts only `selector` plus `attachment_id`.
- Do not accept server paths, storage keys, URLs, filename overrides, or raw content from a procedure.
- Never log file bytes, storage paths, protected input, or attachment contents.
- Require the attachment, run, session, and ticket to belong to the same organization and ticket.
- Revalidate stored size, SHA-256, extension, magic bytes, and configured upload size before worker transfer.
- Use a Playwright in-memory file payload; create no worker-side staging file.
- Preserve consent, approval signature/hash, lease, idempotency, authentication handoff, emergency stop, and audit behavior.
- Upload execution is not resolution; an application-specific independent verification remains mandatory.

## Review Focus

- Cross-ticket or cross-tenant attachment identifiers must fail before any bytes reach the worker (Task 2 tests).
- Storage corruption or record/content mismatch must fail closed and record no successful execution (Task 2 tests).
- A paused browser session or login/MFA/CAPTCHA page must not receive the file (Task 1 tests).
- Non-file, disabled, multi-target, or missing selectors must fail without mutating the page (Task 1 tests).
- Upload metadata may be audited, but file bytes and storage paths must never appear in worker/API logs or execution results (Tasks 1 and 2 tests).

---

### Task 1: Authenticated In-Memory Worker Upload

**Files:**
- Create: `services/browser-worker/tests/test_upload.py`
- Modify: `services/browser-worker/worker/app.py`
- Modify: `infrastructure/docker/browser-worker.Dockerfile`

**Interfaces:**
- Consumes: authenticated worker session, deterministic selector, filename, content type, expected SHA-256, multipart bytes.
- Produces: `POST /sessions/{sid}/upload` returning `{ok, upload: {filename, size, sha256}, page}` or a safe refusal result.

- [ ] **Step 1: Write failing worker tests**

Add tests proving a real Playwright page receives the exact in-memory payload and filename; bad bearer token, oversized bytes, wrong SHA-256, paused session, auth page, missing selector, non-file input, disabled file input, and selector matching multiple inputs are refused without setting a file. Assert state/log responses contain neither file bytes nor host paths.

- [ ] **Step 2: Run worker tests and verify RED**

Run: `PYTHONPATH=services/browser-worker .venv/bin/pytest services/browser-worker/tests/test_upload.py -q`

Expected: FAIL because `/sessions/{sid}/upload` does not exist.

- [ ] **Step 3: Implement worker upload endpoint**

Add a multipart endpoint using `UploadFile`/`Form`; bounded chunk reading; `hashlib.sha256`; safe basename normalization; exact-one enabled `input[type=file]` validation; existing pause/auth detection; and `locator.set_input_files({"name": ..., "mimeType": ..., "buffer": data})`. Append only actor/action and safe metadata to the session log. Pin `python-multipart==0.0.32` in the worker image.

- [ ] **Step 4: Run worker tests and worker lint**

Run: `PYTHONPATH=services/browser-worker .venv/bin/pytest services/browser-worker/tests/test_upload.py -q && .venv/bin/ruff check services/browser-worker/worker services/browser-worker/tests`

Expected: all tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit**

Commit message: `feat: add guarded browser worker uploads`

### Task 2: Same-Ticket Attachment Resolution and Dispatch

**Files:**
- Create: `apps/api/joltiq/services/browser_uploads.py`
- Modify: `apps/api/joltiq/services/browser_client.py`
- Modify: `apps/api/joltiq/services/executor.py`
- Modify: `apps/api/tests/test_security.py`

**Interfaces:**
- Consumes: `load_for_execution(db: Session, run: AgentRun, session: SupportSession, attachment_id: str) -> BrowserUpload`.
- Produces: immutable `BrowserUpload(filename: str, content_type: str, size: int, sha256: str, data: bytes)` and `browser_client.upload(session_id, selector, upload) -> dict[str, Any]`.

- [ ] **Step 1: Write failing API security tests**

Add tests proving: a valid same-ticket attachment is passed to the worker only after approval; another ticket/org attachment is refused; a session linked to another ticket is refused; missing/invalid UUID is refused; recorded size/SHA mismatch and revalidation failure are refused; and results/audits contain no bytes or storage key.

- [ ] **Step 2: Run focused API tests and verify RED**

Run: `cd apps/api && ../../.venv/bin/pytest tests/test_security.py -q -k browser_upload`

Expected: FAIL because secure attachment resolution and upload dispatch do not exist.

- [ ] **Step 3: Implement attachment resolution and HTTP transfer**

Create the immutable upload value, perform explicit organization/ticket/session checks, call tenant-scoped `storage.get`, compare recorded size and SHA-256 using constant-time comparison, and call `storage.sniff`. Add multipart HTTPX transfer with filename/content-type/digest fields. Special-case `browser.upload` in the browser executor only after all existing gates and before generic `browser_client.act`.

- [ ] **Step 4: Run focused and surrounding tests**

Run: `cd apps/api && ../../.venv/bin/pytest tests/test_security.py tests/test_tickets.py tests/test_procedures.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: bind browser uploads to ticket attachments`

### Task 3: End-to-End Approved SaaS Upload and Evidence

**Files:**
- Modify: `tests/e2e/testapp/app.py`
- Modify: `apps/api/tests/test_e2e_browser.py`
- Modify: `README.md`
- Create: `docs/operations/browser-upload-evidence.md`

**Interfaces:**
- Consumes: Task 1 worker endpoint and Task 2 API execution interface.
- Produces: a real-Chromium, real-server acceptance path that independently reads the authorized fixture's persisted upload metadata.

- [ ] **Step 1: Write failing end-to-end tests and fixture contract**

Extend FieldDesk with an authenticated single-file upload form and server-side receipt metadata. Add an executable procedure containing `browser.navigate` then `browser.upload`, assert upload approval is required, deny first and prove no receipt, then approve in a fresh run and assert the fixture independently reports the exact filename, size, and SHA-256. Add wrong-ticket and emergency-stop coverage where no receipt occurs.

- [ ] **Step 2: Run browser E2E and verify RED**

Run: `cd apps/api && ../../.venv/bin/pytest tests/test_e2e_browser.py -q -k upload`

Expected: FAIL because the fixture and worker/API upload path are incomplete.

- [ ] **Step 3: Complete fixture, acceptance path, and honest documentation**

Implement only the fixture behavior required by the test. Document commands, evidence, limitations, and remaining live-SaaS/external-review gates. Update the README to state that approved same-ticket uploads work in the isolated browser while public SaaS compatibility remains unproven.

- [ ] **Step 4: Run full verification**

Run: `cd apps/api && ../../.venv/bin/pytest -q`

Run: `.venv/bin/ruff check apps/api/joltiq apps/api/tests services/browser-worker/worker services/browser-worker/tests tests/e2e/testapp`

Expected: full API/browser suite passes and Ruff reports no errors.

- [ ] **Step 5: Commit**

Commit message: `test: prove approved browser attachment upload`
