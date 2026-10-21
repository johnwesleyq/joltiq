# Browser Runtime Attachment Binding Design

## Intent

Make an approved browser-upload procedure reusable without allowing the AI, a procedure author, or a signed command to choose an arbitrary file. The customer explicitly selects one existing attachment on the JoltIQ ticket when asking JoltIQ to act.

## Contract

- A browser procedure may use only the literal binding `customer_selected_ticket_attachment` for `browser.upload`; hard-coded attachment UUIDs are rejected for newly published typed browser procedures.
- The chat/run request may carry one `attachment_id`. The API resolves it under the caller's tenant and ticket access, records immutable ID/filename/size/SHA-256 metadata in the run context, and never exposes a storage path.
- The published procedure fingerprint covers the binding rule. Plan materialization replaces that rule deterministically with the frozen attachment UUID; the plan hash and exact-step approval therefore cover the actual file.
- The AI never receives authority to select or replace the attachment. A missing, deleted, wrong-ticket, cross-tenant, or changed attachment prevents planning or execution.
- Changing the selected attachment after a plan exists requires a new plan and new approval. Execution retains the existing same-ticket, content-type, size, digest, consent, lease, stop, and audit checks.
- Approval copy identifies the safe filename, size, digest prefix, and destination domain so the customer can make an informed choice.

## Scope

This increment covers API contracts, typed browser-procedure publication, deterministic materialization, approval/audit metadata, portal selection, and local FieldDesk acceptance. It does not claim compatibility with a public SaaS vendor or access to an existing Chrome/Edge profile.

## Failure behavior

Invalid or inaccessible selections return a non-enumerating not-found response. A procedure needing a selection pauses/escalates without creating an executable plan when none was supplied. Any binding mismatch is audited and no bytes reach the browser worker.

## Implementation calls

- Use a new typed browser procedure schema rather than weakening the legacy schema or accepting placeholder strings in the global action registry.
- Keep the resolved UUID in `PlanStep.params` so existing approval hashing and execution remain authoritative.
- Keep the frozen metadata in `AgentRun.context`; the database/storage record is independently revalidated immediately before transfer.
