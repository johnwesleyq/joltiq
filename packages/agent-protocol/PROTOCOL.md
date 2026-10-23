# JoltIQ Windows Companion Protocol — v1

Transport: outbound HTTPS from the device to the API (`/api/v1/agent/v1/*`). The device never opens a listening port.

## 1. Enrollment
1. An authenticated user creates a one-time code in the portal (`POST /api/v1/devices/enrollment-codes`): 12 chars, single use, 15-minute TTL, bound to the user's tenant.
2. The companion generates an **ECDSA P-256** key pair locally. The private key is stored with **Windows DPAPI (CurrentUser / LocalMachine for the service)** and never leaves the device.
3. `POST /agent/v1/enroll` `{code, public_key_pem (SPKI), hostname, os_name, os_version, agent_version, capabilities[]}`
   → `{device_id, org_id, server_public_key_pem, protocol, poll_interval_seconds, capabilities_accepted}`.
   The companion **pins** `server_public_key_pem` and verifies every envelope with it.

## 2. Signed device requests
Every request after enrollment carries these headers:

| Header | Value |
|---|---|
| `X-JQ-Device` | device id |
| `X-JQ-Timestamp` | unix seconds (server allows ±120 s) |
| `X-JQ-Nonce` | 16–64 random chars, single use |
| `X-JQ-Counter` | strictly increasing integer (persisted on device) |
| `X-JQ-Signature` | base64 ECDSA-P256-SHA256 (DER) over the string-to-sign |

String-to-sign (UTF-8, `\n`-joined): `METHOD`, `PATH` (e.g. `/api/v1/agent/v1/poll`), `TIMESTAMP`, `NONCE`, `COUNTER`, `hex(sha256(body))`.

The server rejects a bad signature, a stale timestamp, a reused nonce, a non-increasing counter, or a revoked device. Each rejection is recorded as a security event.

### Direct support requests

An enrolled user can start or continue a support conversation from the tray without first opening the web portal. These calls use the same signed-request headers above.

- `POST /agent/v1/support-requests`
  - Request: `{"client_request_id":"<UUID>","message":"<1..20000 characters>"}`
  - Response (`201`): `{"ticket_id":"<UUID>","ticket_number":123,"run_id":"<UUID>","state":"NEW"}`
  - `client_request_id` is generated once by the companion and reused after a network-uncertain retry. Its uniqueness scope is the authenticated device. The server returns the original response for a duplicate and never creates a second ticket or run.
- `POST /agent/v1/support-requests/{run_id}/messages`
  - Request: `{"message":"<1..20000 characters>"}`
  - Response: the same ticket/run identity fields and current state.
- `GET /agent/v1/support-requests/{run_id}`
  - Response: customer-safe run state, pause reason, outcome stage, and conversation messages. Internal model calls, hypotheses, observations and execution details are not returned.

The server derives tenant, user, customer and device identity from the authenticated device. It refuses revoked devices, devices without an active owner, and any read or write where the run's ticket is not linked to that exact device. Client-supplied identity, ticket, session, action, approval and consent fields are rejected.

Starting or using this conversation grants **no screen visibility, input control, action approval or session consent**. If diagnosis needs the computer, the normal signed `session.begin` flow and local consent dialog still happen separately. No desktop command is eligible before the applicable portal and device-local gates pass.

## 3. Server → device envelopes (`GET /agent/v1/poll`)
```json
{"protocol":"1","payload":"<base64 of exact signed JSON bytes>","body":{...},"signature":"<base64 DER>","alg":"ES256-DER"}
```
The device verifies `signature` over the decoded `payload` bytes with the pinned server key, then parses **`payload`**, not `body`. It rejects the envelope if `expires_at` has passed, if `nonce` was seen before, or if `device_id` doesn't match.

Body types:
- `session.begin` `{session_id, consent, expires_at}` → the companion shows a **consent dialog** to the logged-in user, then calls `POST /agent/v1/sessions/{id}/local-consent {accepted}`. A visible "session active" indicator stays on screen for the rest of the session.
- `action` `{command_id, session_id, action, params, risk, expires_at, nonce}` → the companion re-validates the action against its **local policy** (allowlisted action ids, parameter schemas, allowed processes, registry and file path prefixes, no secret fields). It refuses anything outside an active, locally-consented session. It executes, then `POST /agent/v1/commands/{id}/result {status: done|failed|rejected, result}`. Results are redacted on the device before upload.
- `session.end` → stop immediately and hide the indicator.

## 4. Kill switch
The tray menu item and hotkey call `POST /agent/v1/sessions/{id}/stop`. The local session is cancelled **before** the network call returns, so stopping works even when offline.

## 5. Actions
Action ids, parameters and risk levels are defined in `apps/api/joltiq/services/actions/registry.py` (the single source of truth) and mirrored in `apps/windows-agent/src/JoltIQ.Agent.Core/LocalPolicy.cs`. Privileged actions (`service.restart`) are forwarded over a local named pipe to the separate `JoltIQ.Agent.Broker` Windows service, which enforces its own allowlist. There is no shell action, file write, credential access, or UAC bypass.

## 6. Other endpoints
`POST /agent/v1/heartbeat` (facts, version → update info), `POST /agent/v1/unenroll`.

## 7. Computer use (v1 extension)

Computer use has an immutable mode: `guide` or `fix`. Portal goal approval is necessary but does not start capture. The server first sends a signed `computer_use.begin` envelope containing the exact `computer_use_run_id`, mode, goal, session and expiry. The Windows tray displays those exact values and posts its second accept/decline decision to `POST /agent/v1/computer-use/{run_id}/local-consent`. A local grant authorizes only that run and mode. `computer_use.end`, STOP, expiry, session end or process restart clears it unless the durable journal is resuming an already-executed result.

Every computer-use `action` envelope carries the matching `computer_use_run_id` and `computer_use_mode`. `screen.capture` returns PNG evidence plus:

- monotonically increasing `frame_generation`;
- SHA-256 `layout_hash` for the virtual-display arrangement;
- scaled image dimensions, virtual-screen bounds and coordinate scale;
- display metadata sufficient to map the signed screenshot back to the current desktop.

Mutating `input.*` commands also carry the exact server-accepted `frame_generation` and `layout_hash`; model-supplied bindings are discarded. Immediately before native input, the companion rechecks the logged-in user, interactive desktop, display layout, current UI Automation target/focus, local grant and STOP token. A stale frame, changed layout/focus/user, lock/UAC/secure desktop, RDP/shared session, password/OTP/card/private-key target, prohibited chord or unknown focus is refused locally.

Fix capabilities are `screen.capture`, `input.click`, `input.drag`, `input.move`, `input.mouse`, `input.type`, `input.key`, `input.scroll`, `input.wait`, `input.cursor`, and `url.open`. Guide capabilities are `screen.capture`, `guide.highlight`, `guide.clear`, and `input.wait`; Guide has structurally zero keyboard/mouse injection authority. The highlight is click-through, no-activation and bounded to the signed frame.

For a command with screenshot evidence, the companion completes these durable steps in order:

1. Execute once and persist the terminal result plus a DPAPI-protected image reference/digest.
2. `POST /agent/v1/commands/{id}/screenshot` with signed raw PNG/JPEG bytes.
3. Only after that succeeds, `POST /agent/v1/commands/{id}/result` with the redacted terminal result.
4. Delete local evidence only after server acknowledgement.

Restart or redelivery resumes the outbox; it never repeats journaled native input to recreate evidence. Missing/corrupt protected evidence produces a failed/uncertain terminal outcome and stops the run.

`GET /agent/v1/computer-use/active` returns the active run and any customer pause. `POST /agent/v1/computer-use/{id}/respond` accepts exactly one applicable response (`approved`, `answer`, `done`, or `switch_to_fix`). `done` clears the Guide overlay and requires a fresh capture before model continuation. `switch_to_fix` ends Guide and creates a distinct pending Fix run; it does not widen the old grant and requires fresh portal and native consent.

STOP cancels locally before network notification, releases held keys/buttons in `finally`, blocks the next queued input, and reports any effect already started. The healthy-endpoint target is 500 ms p95 and 2 seconds maximum. No computer-use action adds shell, arbitrary registry/service/file, installer, credential access or UAC bypass authority.
