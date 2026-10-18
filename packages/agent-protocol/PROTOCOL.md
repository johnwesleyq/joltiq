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
