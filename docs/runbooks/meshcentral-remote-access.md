# MeshCentral remote support runbook

JoltIQ uses self-hosted MeshCentral as an optional technician remote-desktop transport. JoltIQ remains the system of record for staff authorization, why access was requested, session start/end, supported repair approvals, verification, and ticket updates.

## Start locally

1. Set `JOLTIQ_MESHCENTRAL_URL` to the stable URL reachable by both technicians and endpoints (for the current LAN acceptance server: `https://192.168.0.56:8444`).
2. Run `docker compose --profile remote-access up -d --build meshcentral migrate api web`.
3. Wait for `docker compose --profile remote-access ps meshcentral` to report `healthy`.
4. Open `https://localhost:8444` only for provider setup. The development certificate is self-signed.
5. Create the first MeshCentral account; MeshCentral makes the first account the site administrator. Do not reuse a JoltIQ or workstation password.
6. Enable two-factor authentication for that administrator and create a separate least-privileged technician account.

For a shared or production deployment, replace `localhost` with a stable internal DNS name, use a trusted TLS certificate, replace the development session key, keep registration closed, and restrict network access. The configured URL is deployment-owned and cannot be changed by tenants.

## Enroll a Windows computer

1. In MeshCentral, create a device group for the customer.
2. Confirm its effective policy prompts for Desktop consent and does not grant unattended Terminal or Files access.
3. Download the Windows agent from that device group and install it on the authorized test PC.
4. Copy the device's opaque MeshCentral node id from its URL. Treat it as provider configuration, not customer-facing data.
5. In JoltIQ, open **Devices → Remote support**, enter that node id, and choose **Link computer**. After linking, operators return to JoltIQ for normal support work.

The MeshCentral agent is separate from JoltIQ Companion. MeshCentral provides remote desktop; JoltIQ Companion performs signed structured repairs and independent verification.

## Start support

1. A staff user enters a specific reason in **Devices → Remote support**.
2. JoltIQ records `remote_access.started` in its tamper-evident audit chain and opens `/portal/devices/{device_id}/remote-support`.
3. The operator works from the JoltIQ **Remote support** workspace. The MeshCentral administration UI is setup and emergency-diagnostics only.
4. The transport prompts the person at the PC. No response times out as denial; it never auto-accepts.
5. The JoltIQ workspace says **Waiting for the person at the computer** until the transport actually connects. An active audit session alone is not described as a connected desktop.
6. A visible privacy bar remains on the Windows computer while control is active.
7. The operator selects **End support** in JoltIQ. This ends the audit session idempotently even if the embedded desktop has already disconnected.

If the provider login is missing, the workspace says that staff authentication is required; it does not claim that the customer denied access. If a popup is blocked, JoltIQ opens the same workspace in the current tab.

## Protected Windows prompts

- Session consent, an exact consequential-action approval, and a question that requires the customer are actionable prompts. The companion restores and activates each exact prompt once.
- Status updates never steal focus.
- Windows may refuse programmatic foreground activation. JoltIQ records a structured diagnostic and keeps waiting; it never simulates an Allow click.
- A locked screen is treated as unavailable for local consent. The person at the PC must unlock Windows and approve the prompt locally.
- Remote desktop consent and repair approval are separate. A technician or automated workflow cannot use remote access as a substitute for JoltIQ's signed-action and verification gates.

Remote desktop consent does not approve an automated repair. Destructive or sensitive supported repairs continue through JoltIQ's exact action approval and verification flow.

## Emergency disablement

- Stop new remote sessions: remove `JOLTIQ_MESHCENTRAL_URL` and restart the API.
- Stop the provider: `docker compose --profile remote-access stop meshcentral`.
- Disable one computer: remove or disable its MeshCentral agent/device and end its active JoltIQ audit session.
- Revoke a technician immediately in both JoltIQ and MeshCentral.

## Production requirements

- Trusted TLS and stable DNS reachable by enrolled endpoints.
- MFA for all MeshCentral staff accounts and no shared accounts.
- Backups for `meshcentral-data`; tested restore procedure.
- Restricted technician roles; no terminal/files unless a later policy explicitly adds and tests them.
- Retention and review of JoltIQ audit events and MeshCentral session logs/recordings.
- Independent security review and physical Windows 10/11 acceptance evidence.
