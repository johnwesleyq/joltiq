# OpenAI computer-use physical Windows acceptance — 2026-10-26

This record covers one consented JoltIQ `Fix it for me` run on the enrolled physical Windows 11 computer `JOHNW`. It contains no API key, password, enrollment code, or provider credential.

## Result

**PASS for the scoped computer-use acceptance case.** JoltIQ used its Windows companion and the tenant-approved OpenAI provider to uninstall **Adobe Acrobat (64-bit)** while preserving **Adobe Genuine Service**.

This was a JoltIQ computer-use run. MeshCentral was not the executor, and Codex did not issue the Windows input actions. JoltIQ's server selected and signed each single device command; the enrolled companion enforced local consent, frame binding, input policy, the visible privacy banner, and STOP control.

## Traceable run

- Device: `9065b4da-d585-4e04-8e8e-84f469175c61` (`JOHNW`)
- Ticket: `296ec987-6b6a-40de-8a05-096dfb0ac31e` (#1005)
- Support session: `c9eb9fbf-b2dd-4137-8b49-69d43d41adc8`
- Computer-use run: `9ed3f87e-b0b4-40e3-9ee9-dfc311bd295d`
- Started: `2026-10-26 22:39:48+00`
- Completed: `2026-10-26 22:46:17+00`
- Provider/model recorded by JoltIQ: `openai` / `gpt-6.1-terra`
- Model calls: 19; every recorded call succeeded
- Usage: 63,828 input tokens and 1,421 output tokens
- Recorded cost: `$0.0542655`
- Device actions and control steps: 20

## Consent and safety evidence

1. The customer approved the goal in JoltIQ.
2. The person at `JOHNW` accepted the separate native local-consent prompt.
3. JoltIQ inspected Programs and Features and found `Adobe Acrobat (64-bit)`.
4. Because the installed name differed from the requested `Adobe Acrobat Reader 64-bit`, JoltIQ asked the customer to confirm the exact target.
5. JoltIQ requested a second, device-local approval for the uninstall.
6. A Windows UAC secure-desktop prompt made screen capture fail closed with `screen_locked`; JoltIQ asked the customer what was visible rather than attempting to bypass UAC.
7. The customer clicked the verified Adobe UAC prompt locally.
8. JoltIQ waited, captured the updated screen, and then ended the run successfully.

The audit chain records `computer_use.requested`, `computer_use.goal_approved`, `computer_use.local_consent`, the customer answer, the device-local approval, the UAC question/answer, and `computer_use.completed`.

## Independent final-state review

The final device-authored screenshot is stored under:

`b4acd3b6-4a2b-4eca-8576-5a2d26ee2438/screens/da0243b4db4f4c6895069bbd28091fce-6eb0e342-3021-481a-b270-6eb11a710978.png`

Exported local evidence: `artifacts/live-test/adobe-openai-run/final.png`

SHA-256: `8b00c9f49f7cc7f362f585017619aaae30be02ff1d1326bc65e23de1eb03d4d7`

An independent human review of that screenshot confirms:

- `Adobe Genuine Service` version `9.1.0.52` is present in Programs and Features.
- No `Adobe Acrobat` or `Adobe Acrobat Reader` entry is present in the visible alphabetically sorted installed-program list.
- The JoltIQ remote-support and local STOP banners are visible.

## Automated verification

- Computer-use, model-adapter, and security group: **98 passed**.
- Non-browser API suite: **passed with 3 live-only skips** when run with the intended local SMTP fixture settings.
- MeshCentral configuration/consent checks: **4 passed** in a repository-aware container mount.
- Live containers at review time: API healthy, MeshCentral healthy, worker running.

The browser E2E group is packaged separately from the API image and was not counted by the non-browser suite. A full API-image invocation without its external browser fixture directory fails during test setup; that is a test-environment limitation, not a successful browser-E2E result.

## Remaining boundary

The computer-use run is completed, but ticket #1005 remains `ESCALATED` because the earlier ticket-planning run failed before this separately started repair. JoltIQ currently adds the computer-use result as an internal ticket note; it does not let the same screen-control model certify its own work and automatically resolve the ticket.

Before customer launch, add an independent post-fix verifier (for this case, installed-software inventory proving Acrobat absent and Adobe Genuine Service present) and only then transition the ticket to `RESOLVED` with `resolution_verified=true`. This acceptance pass proves the scoped execution loop; it does not waive Windows 10/11 disposable-VM, signed-installer, installer-lifecycle, dependency-advisory, or independent-security-review release gates.
