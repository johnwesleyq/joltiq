# JoltIQ support workspace acceptance — 2026-10-26

This record separates automated proof, physical-device observations, and work that remains open. It contains no provider token, enrollment code, API key, or password.

## Build under test

- Branch: `feat/direct-device-help`
- Workspace implementation commits: `27fefcd`, `5cbbc44`, `a491ba5`
- Windows prompt source archive SHA-256: `f8cb5caab1accee3ec0c3f15bfa9f03d3435887fe69893dbcae9d484c326fea1`
- Physical test computer label: `JohnW`
- Windows host architecture observed: x64

## Automated results

| Check | Result | Evidence |
| --- | --- | --- |
| Provider-neutral remote-access API contract | PASS | Focused `tests/test_remote_access.py`: 4 passed. Response includes JoltIQ `workspace_path`; provider credentials are not returned. |
| Branded workspace states | PASS | Playwright covers waiting, active transport, missing staff login, ended, API error, and End support. |
| Workspace launch and popup fallback | PASS | Playwright proves the device page opens `workspace_path`, never the provider transport URL, with same-tab fallback. |
| Web quality gates | PASS | ESLint, TypeScript, Next.js production build, and three focused Playwright scenarios passed. |
| Windows companion compile | PASS | `JoltIQ.Agent.Tray` and `JoltIQ.Agent.Tests` cross-built with 0 warnings and 0 errors. |
| Windows prompt runtime tests | PASS | The checksum-matched source ran on physical Windows: 19 passed, 0 failed, 0 skipped in 16 ms. Output was saved to `C:\Users\admin\Desktop\joltiq-prompt-tests.txt`. |

## Physical Windows acceptance

| Case | Result | Observation / required evidence |
| --- | --- | --- |
| Local Allow is required | OBSERVED | The person at the Windows PC accepted the provider's local permission prompt before control connected. Save the provider/JoltIQ timestamps for a repeatable final run. |
| Workspace connects to the correct PC | PARTIAL | JoltIQ bound the online device `9065b4da-d585-4e04-8e8e-84f469175c61` to the already verified `JohnW` MeshCentral node and created a reason-bound session. The Codex in-app browser blocked the private-network iframe before the provider could display the desktop, so a normal supported browser run is still required. |
| Privacy banner remains visible | OBSERVED | The Windows desktop displayed both the blue remote-support-active bar and red local STOP banner throughout control. |
| Local denial blocks access | OPEN | Repeat from a disconnected session, choose Deny locally, and save the failed-connection and audit outcome. |
| End support closes the audit session | PASS | JoltIQ ended session `e5669530-ef1e-4183-9ddc-c2466d95d7ee`; the database state changed to `ended`, the workspace rendered `SESSION ENDED`, and audit event `416` recorded `remote_access.ended`. |
| Actionable prompt foregrounding | PARTIAL | Native focus, duplicate, status-update, already-visible, and locked-screen logic tests passed on physical Windows. One deployed real consent/approval prompt still needs a screenshot and timestamp. |
| Adobe test preserves Genuine Service | OBSERVED | Programs and Features shows **Adobe Genuine Service 9.1.0.52** installed after Adobe Acrobat was removed. This proves preservation of that component, not the correctness of every uninstall workflow. |

## Audited JoltIQ session

- Device: `9065b4da-d585-4e04-8e8e-84f469175c61` (`JOHNW`)
- Session: `e5669530-ef1e-4183-9ddc-c2466d95d7ee`
- Recorded reason: `Acceptance test: verify local consent, visible privacy notice, correct-device connection, and audited session end on JOHNW.`
- Started: `2026-10-27 04:02:39.707403+00`; audit event `415` is `remote_access.started`.
- Ended: `2026-10-27 04:03:57.27594+00`; audit event `416` is `remote_access.ended`.
- The workspace correctly stayed in its waiting state when the remote transport could not load. It did not claim that the customer's desktop was connected.

## Launch decision

The support-workspace slice is not yet public-launch approved. It becomes accepted when every OPEN or PARTIAL physical case above passes in one traceable run and the provider dependency advisories, signed installer, supported Windows 10/11 matrix, upgrade/uninstall testing, and independent security review are closed or formally risk-accepted.

This decision does not block local development testing. It prevents an incomplete physical run from being described as customer-ready.
