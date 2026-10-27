# MeshCentral acceptance evidence

Current local evidence, 2026-10-25:

- MeshCentral 1.3.1 container built and started.
- HTTPS returned `200 OK` at `https://127.0.0.1:8444/` after first-run agent generation.
- Container health reported `healthy`.
- JoltIQ database migration reached `0008`.
- Focused API remote-access tests: 4 passed.
- Web typecheck, lint, and production build passed.
- Full API suite attempt was not green in the API image: browser E2E fixtures are absent from that image, and one pre-existing SMTP/job-order test failed. These failures are outside the remote-access slice; the focused remote-access tests pass.
- MeshCentral 1.3.1 currently reports three high and two moderate transitive npm advisories (`compression`, `node-forge`, and `qs`). No compatible automatic fix is available. Keep this local/internal until the dependency risk is patched or independently accepted.

Physical Windows acceptance is intentionally still **OPEN**. Save screenshots/log exports here for each case:

1. Deny: opening Desktop displays a local consent prompt; choosing Deny prevents viewing/control.
2. Allow: choosing Allow opens the correct PC and a visible privacy bar remains for the session.
3. Disconnect: control stops immediately and the JoltIQ session is ended.
4. Locked/no user: access is refused; no unattended auto-accept occurs.
5. Scope: technician cannot use remote terminal or files under the MVP policy.
6. Audit: JoltIQ contains matching `remote_access.started` and `remote_access.ended` events with device, operator, reason, and timestamps.
7. Repair proof: technician performs one harmless approved test repair, JoltIQ verifies it independently, and the ticket records the outcome.

Do not label public launch ready until all seven cases pass on disposable Windows 10 and Windows 11 machines.
