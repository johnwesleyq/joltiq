# Secure Browser Upload MVP Design

## Goal

Allow an approved JoltIQ browser procedure to upload an attachment from the procedure's own ticket into a deterministic file input inside the session's isolated browser, without exposing arbitrary server files or creating a path around consent, approval, tenant isolation, audit, stop, and exact-procedure enforcement.

## Trust boundaries

- `browser.upload` remains a `SENSITIVE`, non-reversible registry action and therefore requires the existing exact-step approval before execution.
- The published procedure supplies only `selector` and `attachment_id`. It never supplies a filesystem path, storage key, filename override, URL, or raw file content.
- The API resolves the attachment after policy and approval verification. It accepts only an attachment belonging to the run's organization and ticket, and only when the browser session is linked to that same ticket.
- The API re-reads the object through tenant-scoped storage, revalidates its size, type, magic bytes, recorded size, and SHA-256 digest, then transfers it over the authenticated internal worker connection.
- The browser worker accepts upload bytes only from the authenticated API, applies its own size and SHA-256 checks, rejects paused sessions and authentication pages, and uses Playwright's in-memory file payload. It never accepts or opens a host filesystem path.
- Logs, session events, execution results, and audit events contain attachment identifiers and safe metadata only; never file bytes, local storage paths, or secret form contents.

## Execution flow

1. Planning copies `browser.upload(selector, attachment_id)` exactly from a two-admin-published executable procedure.
2. Existing policy, consent, step approval, approval signature/hash, lease, and idempotency checks run unchanged.
3. The executor validates the run, browser session, ticket, and attachment relationship.
4. The API loads and revalidates the attachment, then sends `selector`, safe filename, MIME type, SHA-256, and bytes to the authenticated browser worker.
5. The worker resolves the deterministic selector, proves it is an enabled file input, sets one in-memory file payload, and returns only safe metadata plus current page state.
6. The existing action execution, session event, and audit paths record the result. The procedure must include a later independent readback or other application-specific verification before JoltIQ can resolve the ticket.

## Refusal behavior

The action fails without setting a file when the attachment is missing, belongs to another ticket or organization, the session/run/ticket relationship differs, stored bytes no longer match the recorded size or digest, content no longer passes upload validation, the file exceeds the configured limit, the target is not a file input, the browser session is paused/stopped/expired, or the page requires login/MFA/CAPTCHA.

## Acceptance boundary

The MVP is complete when automated tests prove worker-side upload and refusal behavior, API-side tenant/ticket/integrity enforcement, exact approval gating, and an end-to-end upload into the authorized local SaaS fixture with independently observed server-side receipt. This does not claim compatibility with arbitrary public SaaS sites or customer readiness; a separate live SaaS pilot and external security review remain required.

