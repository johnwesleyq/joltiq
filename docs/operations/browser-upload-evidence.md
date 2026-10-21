# Browser Upload MVP Evidence

Date: 2026-10-21

Status: **AUTHORIZED LOCAL BROWSER MVP VERIFIED — NOT CUSTOMER-READY**

## Proven behavior

- `browser.upload` remains a `SENSITIVE`, non-reversible action bound to the existing consent, exact-step approval, approval-signature, lease, idempotency, audit and emergency-stop gates.
- A typed browser procedure can name only a deterministic selector and the fixed `customer_selected_ticket_attachment` binding. Hard-coded attachment IDs are refused at publication.
- The portal makes the customer explicitly select one ticket file. The API freezes its safe metadata on the run, materializes its UUID into the exact plan, and includes that UUID in the plan hash and step approval. Approval copy identifies the safe filename, size, digest prefix and destination domain. The AI cannot choose or replace it.
- A missing, wrong-ticket or cross-tenant selection is refused without falling back to the newest or only attachment. Rebinding an active run is refused.
- Neither the procedure nor the request can provide a server path, object-storage key, URL, filename override or raw bytes.
- The API transfers an attachment only when the run, browser session, ticket and attachment share the same organization and ticket.
- Stored size, SHA-256, extension, magic bytes and content type are revalidated immediately before transfer.
- The authenticated browser worker independently checks size and SHA-256, refuses paused or authentication-blocked sessions, requires exactly one enabled file input, and gives Playwright an in-memory payload.
- Worker and API results contain the attachment ID and safe filename/size/digest metadata, not attachment contents, storage keys or host paths.
- Navigation surfaces a post-navigation login/MFA/CAPTCHA state before a sensitive upload can consume its approval.
- Auto-submit file inputs are allowed to finish navigation before the worker builds its result.
- A fresh browser page independently reloads the SaaS page and reads server-persisted receipt metadata before the ticket can be marked resolved.

## Automated evidence

Run from the repository root with the locked Python 3.12 environment and an isolated PostgreSQL test database:

```bash
PYTHONPATH=services/browser-worker .venv/bin/pytest services/browser-worker/tests/test_upload.py -q
```

Result: 12 passed. Coverage includes successful in-memory transfer, bearer authentication, size/digest checks, pause, login handoff, missing/non-file/disabled/multi-target selectors, post-navigation authentication and auto-submit navigation.

```bash
JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:***@localhost:55432/joltiq_test \
JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:***@localhost:55432/joltiq_test \
JOLTIQ_REDIS_URL=redis://localhost:6379/15 \
.venv/bin/pytest apps/api/tests/test_security.py -q -k browser_upload
```

Result: 5 passed. Coverage includes approval-before-transfer, cross-ticket/cross-tenant/session mismatch, invalid IDs, stored size/digest/content corruption and audit/result redaction.

The full security file additionally passes 57 tests, including typed binding publication, missing-selection refusal, deterministic materialization, same-ticket freezing, post-approval metadata-change refusal and cross-ticket API refusal.

```bash
JOLTIQ_DATABASE_URL=postgresql+psycopg://joltiq_app:***@localhost:55432/joltiq_test \
JOLTIQ_MIGRATION_DATABASE_URL=postgresql+psycopg://postgres:***@localhost:55432/joltiq_test \
JOLTIQ_REDIS_URL=redis://localhost:6379/15 \
.venv/bin/pytest apps/api/tests/test_e2e_browser.py -q -k upload
```

Result: 4 passed using the real API, PostgreSQL RLS, real browser worker, real Chromium and authorized local FieldDesk server. The cases are denial/no receipt, approved authenticated upload with verified server receipt, wrong-ticket refusal/no receipt and emergency stop/no receipt.

## Deliberate limitations

- The external AI provider is replaced by the repository's labelled deterministic local test fixture.
- FieldDesk is an authorized local SaaS-like fixture, not a public vendor account.
- Only deterministic selectors are supported; visual/semantic recovery from redesigned third-party pages is not proven.
- The runtime binding is proven only in the JoltIQ portal and local FieldDesk fixture. Live-vendor approval copy, record targeting and changed-page behavior still require vendor-specific acceptance.
- Uploading to arbitrary public SaaS applications is not claimed. Each supported vendor workflow still needs its own published procedure, sandbox account and acceptance evidence.
- This does not authorize access to a customer's existing Chrome or Edge profile. It operates only inside JoltIQ's isolated browser session.
- Malware scanning beyond the existing file-type, magic-byte, macro and archive checks is not included.
- Production hosting, external penetration testing, privacy/legal review and incident-response exercises remain release gates.

## Next acceptance step

Select one real SaaS sandbox workflow, publish its exact procedure, and run the same approval, authentication, stop, wrong-record, changed-page, failed-verification and audit-evidence cases against that vendor before offering a supervised pilot.
