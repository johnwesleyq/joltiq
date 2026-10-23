# Direct Device Help Design

## Intent

An enrolled Windows user can open the JoltIQ companion and describe a problem in ordinary language without first visiting a portal or creating a ticket. JoltIQ responds in the companion, investigates the linked PC, and offers Guide, Fix, browser, or escalation paths under the same controls used for ticket-originated support.

The durable internal ticket remains the audit and workflow record, but it is an implementation detail rather than a form the user must complete.

## User experience

The tray menu includes **Ask JoltIQ…**. It opens a conversation window that identifies the enrolled computer and asks what is wrong. Sending the first message creates a device-linked internal ticket and agent run. Subsequent messages continue that run. The window shows JoltIQ messages, working/waiting/finished state, the internal ticket number, and a link to the web conversation when available.

When screen access is useful, JoltIQ still asks whether the user wants Guide or Fix. Starting the conversation is not permission to view or control the screen. Native session consent and exact action approvals remain separate. STOP remains available from the tray and hotkey.

## Trust and identity

- Every companion request uses the existing signed device protocol with timestamp, nonce, and monotonic counter checks.
- The server derives organization, customer, device, and owner only from the authenticated enrolled device. The companion cannot submit arbitrary identity IDs.
- The created ticket is linked to the device and uses source `device_chat`.
- If the device is revoked, lacks a bound owner, or the bound owner is no longer an active organization member, the request fails closed and tells the user to contact an administrator.
- Device-originated messages are audited as device actions and retained in the same tenant-scoped ticket/run history.
- A direct request never bypasses procedure publication, policy, consent, control leases, local allowlists, verification, or escalation.

## API contract

Under `/api/v1/agent/v1`:

- `POST /support-requests` with `{ "message": string }` creates a device-linked ticket and run and returns `ticket_id`, `ticket_number`, `run_id`, and `state`.
- `POST /support-requests/{run_id}/messages` with `{ "message": string }` continues the nonterminal run or starts a new run on the same ticket after a terminal outcome.
- `GET /support-requests/{run_id}` returns only customer-safe run messages and state when the run's ticket is linked to the authenticated device.

Message limits match the portal chat contract. These endpoints never accept `org_id`, `customer_id`, `requester_id`, `device_id`, `ticket_id`, or action/consent fields from the companion.

## Failure behavior

Offline requests remain in the local compose box and are not described as submitted. Duplicate submission uses a client-generated request ID bound to the device and returns the original result. Revocation, ownership mismatch, terminal runs, unavailable inference, unsupported fixes, and failed verification are reported honestly. No failure silently creates multiple tickets or grants computer access.

## Acceptance

1. A signed enrolled-device request creates exactly one device-linked internal ticket and run.
2. Replaying or repeating the same request ID does not duplicate the ticket.
3. A different or revoked device cannot read or continue the conversation.
4. The tray can start and continue a conversation and display server state without a portal login.
5. The request cannot start screen capture/input until the existing native consent sequence succeeds.
6. Unavailable local inference escalates or reports unavailable; it never routes customer data to an external model in production.

macOS will reuse the server contract later with its own native UI and permissions implementation.
