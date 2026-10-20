# Windows Outlook PDF Crash MVP Design

**Date:** 2026-10-20
**Status:** Approved with owner review corrections
**Product direction:** JoltIQ is a horizontal AI support worker for companies, internal teams, software vendors, service organizations, and MSPs. This is its first production-grade Windows support workflow, not a limit on the markets it serves.

## Intent

Prove that JoltIQ can resolve one real Windows support request without a technician touching the computer:

> “Outlook closes every time I open a PDF attachment.”

The customer may provide consent, approve exact actions, and confirm whether the symptom is gone. JoltIQ diagnoses, acts, verifies, records evidence, and escalates when the evidence or environment does not meet the published procedure.

The workflow must never turn a plausible knowledge match into permission to alter a computer. Diagnosis, procedure integrity, customer consent, local consent, step approval, device policy, action execution, and independent verification remain separate gates.

## Supported environment

- Windows 10 or Windows 11, 64-bit.
- Classic Outlook desktop from Microsoft 365.
- One active interactive user session, with that user’s desktop unlocked.
- A JoltIQ Windows companion enrolled to the correct tenant and device.
- A published executable procedure naming the exact Outlook COM add-in ProgID and the expected crash-module evidence, approved and published by two distinct MFA-authenticated organization admins/owners against the same canonical hash.

The following are unsupported and must escalate without changing the computer:

- New Outlook for Windows.
- Outlook is not installed.
- The named add-in is not installed for the logged-in user.
- The crash evidence does not identify Outlook and the procedure’s named PDF-preview module/add-in, or the required Windows event log is disabled, unreadable, stale, or otherwise unable to support a trustworthy observation.
- The add-in’s effective setting is controlled by machine-wide configuration, Group Policy, or another company policy and cannot safely be changed per user.
- No interactive user is logged in, the screen is locked, or local consent cannot be shown.
- More than one interactive user is logged in, including shared and Remote Desktop hosts.
- The enrolled or consenting user does not match the interactive user whose Outlook profile would be changed.
- The device is revoked, offline beyond the command deadline, outside the supported OS/architecture matrix, or running an incompatible companion version.

## Published procedure contract

One MFA-authenticated administrator approves an executable procedure’s canonical hash, and a second distinct MFA-authenticated administrator/owner publishes that unchanged hash. Both decisions are audited. The procedure contains:

- symptom and environment signals;
- exact diagnostic requirements;
- the expected Outlook faulting application and faulting module;
- the exact add-in display name and ProgID;
- the exact ordered remediation steps and parameters;
- preconditions and unsupported configurations;
- rollback information;
- typed postconditions;
- customer-facing explanations.

For this MVP, the remediation is exactly:

1. Set the published add-in’s per-user `LoadBehavior` to disabled.
2. Safely restart classic Outlook, or start it if it was not running.

The executable plan is copied from the published procedure’s canonical JSON. The AI may select a procedure only after the diagnostic gate passes; it may not author, paraphrase, reorder, add, remove, or alter executable steps or substitute an add-in name. Chat history, remembered resolutions, and learned fixes can rank a candidate only; they cannot create executable desktop steps. The procedure version and canonical hash are bound to the plan and approvals. Any mismatch between the plan and the published procedure escalates before consent or execution.

The Windows MVP does not expose screen-capture or input-control actions. A support session cannot widen the procedure’s allowed actions.

## End-to-end sequence

1. The request creates a ticket linked to an enrolled device.
2. Ticket text and tenant knowledge identify a **candidate** approved procedure; this match is not yet a diagnosis and creates no executable plan.
3. The customer grants portal consent for a diagnostic session, and the interactive Windows user grants local consent.
4. JoltIQ runs only the procedure’s exact read-only diagnostic actions.
5. If every diagnostic condition matches, the server copies the canonical remediation steps into a plan and binds the procedure hash. Otherwise, the session ends with no change and the ticket escalates.
6. The customer reviews and approves each exact sensitive remediation step.
7. The companion re-checks consent, signatures, expiry, device status, local policy, and environment, then performs each step.
8. Independent read-back establishes `FIX_APPLIED_VERIFIED`.
9. The customer attempts the original PDF action; customer confirmation and the post-attempt event-log observation establish or reject `RESOLVED_VERIFIED`.
10. JoltIQ closes only a `RESOLVED_VERIFIED` ticket. Every other terminal path remains open or escalates with the last confirmed computer state.

## Diagnostic gate

Before building an executable plan, JoltIQ collects fresh, read-only facts from the enrolled device:

1. Confirm Windows version and 64-bit architecture.
2. Confirm one unlocked interactive user session and identify that user.
3. Confirm classic Outlook is installed; explicitly distinguish it from New Outlook.
4. Confirm the exact published add-in is installed in the relevant 32-bit or 64-bit registry view for the interactive user.
5. Read the effective add-in setting and determine whether a user-level change can persist or is overridden by HKLM/Group Policy.
6. Confirm the tray process is running as the enrolled/consenting user whose stable Windows SID owns the Outlook profile.
7. Confirm the required Windows event log is enabled, readable, current, and capable of supporting the requested lookback/observation window.
8. Query recent Windows Application Error/Outlook events and require a crash record that identifies `OUTLOOK.EXE` and the published procedure’s faulting PDF-preview module.

All required facts must be present and match. `UNKNOWN`, missing events, an ambiguous module, a different Outlook failure, or a different add-in escalates as “diagnosis not confirmed.” JoltIQ does not try the fix speculatively.

## Consent and approval

Execution requires every existing security gate:

1. The customer consents to the desktop session in the portal.
2. The interactive user accepts the native Windows consent dialog.
3. The customer approves each sensitive step separately.
4. Every approval remains bound to the exact action, target, parameters, plan hash, procedure version, and expiry.
5. The companion independently validates the signed command, active local consent, session expiry, device status, action allowlist, and parameters immediately before execution.

Consent expiry, device revocation, invalid signatures, duplicate or expired commands, a changed procedure, a changed plan, policy refusal, or emergency stop blocks execution and prevents resolution.

## Execution and recovery

### Disable the add-in

The write runs only in the confirmed logged-in user’s tray process, never in the background service or as SYSTEM. Immediately before writing, re-check the user SID and policy state. The tray durably saves the original registry location, registry view, value kind, existence, and value in its own encrypted local command journal. Write only the published ProgID’s per-user `LoadBehavior`, then read it back through a separate read-only command.

The action is idempotent. A duplicate command returns the recorded original result and must not perform or count the change twice.

### Safely restart Outlook

- If Outlook is running, request a normal close and wait up to 20 seconds.
- Never call `Kill`, force termination, or bypass a save prompt.
- If Outlook remains open or a prompt prevents closing, report failure and escalate.
- If Outlook was not running, locate the registered classic Outlook executable and start it; report this as “started,” not “restarted.”
- After launch, require an Outlook process with a responding main window.

### Partial failure and rollback

If the add-in change succeeds but Outlook cannot be safely restarted, JoltIQ records the exact original and current values and marks the run `PARTIALLY_APPLIED`. It tells the customer that the add-in was disabled but Outlook was not restarted.

The portal offers “Restore the original add-in setting.” Rollback is an explicit, separately approved sensitive plan containing restore followed by safe Outlook restart. The server sends only the original change command ID; the tray restores the typed pre-state from its own journal after verifying procedure/device/user/action lineage. It refuses if no matching local record exists and never accepts registry state from the server. Independent registry read-back verifies the restore. If Outlook cannot then be restarted safely, JoltIQ says the original setting is restored and will take effect the next time Outlook starts. JoltIQ never silently undoes or claims to undo the change. If the device is offline, stopped, revoked, or the rollback fails, the ticket states clearly that the change remains in place and escalates with the manual restoration instructions.

## Verification and ticket state

Application restart alone does not prove the reported problem is resolved. Verification has two levels:

### Fix applied and verified

All of the following must pass:

- Separate read-back shows the published add-in disabled at the effective location.
- Classic Outlook is running and responsive after the safe start/restart.
- The required event log is healthy and readable so symptom verification can be attempted.

This produces `FIX_APPLIED_VERIFIED`; it does not close the ticket as resolved.

### Problem resolved and verified

The customer is asked to open the same PDF attachment and choose one of:

- **The PDF opens now**
- **Outlook still closes**
- **I can’t test this now**

`RESOLVED_VERIFIED` requires “The PDF opens now” plus a healthy/readable log and no new matching crash event during a 60-second observation window following the attempt. Disabled, unreadable, stale, or otherwise unhealthy logging yields `UNKNOWN`, never success. “Outlook still closes” fails verification and escalates. “I can’t test this now” or no response leaves the fix recorded as applied and keeps the ticket open. After 10 minutes JoltIQ sends exactly one reminder and releases the active session; after 24 hours without a structured response it escalates to a technician as `verification_timeout`. Customer confirmation is evidence, but it is not the only evidence.

The ticket model and UI must distinguish at least:

- diagnosis not confirmed;
- approved fix pending;
- fix partially applied;
- fix applied and verified;
- problem resolved and verified;
- verification failed;
- escalated.

Only `RESOLVED_VERIFIED` may close this ticket as a verified resolution.

## Retry, connection, and stop behavior

- A transient read-only diagnostic or verification command receives one retry after five seconds.
- A state-changing command is not automatically re-executed. Idempotency returns its existing result for duplicate delivery.
- A safe Outlook start may be attempted once after a confirmed clean close; a failed close is never retried or forced.
- Existing loop detection remains active; the same failed action/check pair cannot cycle indefinitely.
- Commands expire after 120 seconds. If the device remains unavailable for five minutes, the run escalates and reports the last confirmed device state.
- A lost result never becomes success. Unknown outcomes remain open until reconciled by command ID or escalated.
- Pressing STOP cancels the local session immediately, prevents any new action from starting within one second, and signals cooperative in-progress work to stop within two seconds. An atomic registry write already completed may not be reversible at that instant; its confirmed pre/post state is reported and rollback is offered.
- The local stop remains effective without network access. Server notification is retried when possible, but no further local work may resume in that session.

## Customer-facing messages

These strings are part of the MVP acceptance contract. Product copy may later be localized, but tests pin the English meaning and required facts.

| Moment | Message |
|---|---|
| Portal session consent | “JoltIQ found an approved fix for the Outlook PDF crash and is requesting permission to diagnose and work on this PC. You can stop the session at any time.” |
| Native Windows consent | “JoltIQ wants to check Outlook crash records and apply the approved fix on this computer. It will disable **{add-in display name}** and safely restart Outlook. It will not force Outlook closed. Allow this session?” |
| Step 1 approval | “Disable **{add-in display name}** (`{ProgID}`) for your Windows account. JoltIQ will save the current setting so it can be restored.” |
| Step 2 approval | “Safely restart classic Outlook to apply the change. If Outlook does not close normally, JoltIQ will stop and ask for help.” |
| Active banner | “JoltIQ support is active on this PC — STOP ends control immediately.” |
| Diagnosis mismatch | “JoltIQ could not confirm that the approved PDF-preview add-in caused this crash. Nothing was changed. A support person will review it.” |
| Unsupported environment | “This Outlook or Windows setup is not supported by this approved fix. Nothing was changed. A support person will review it.” |
| Partial application | “The add-in was disabled, but Outlook could not be safely restarted. JoltIQ did not force it closed. The change has not been undone.” |
| Rollback offer | “Restore the add-in setting that was present before this session?” |
| Rollback restored, restart pending | “The original add-in setting was restored and verified. Outlook could not be safely restarted, so the restored setting will take effect the next time Outlook starts.” |
| Symptom test | “Please open the same PDF attachment now. What happened?” |
| Fix applied, test deferred | “The approved fix was applied and checked, but the original problem has not been confirmed resolved. The ticket will remain open.” |
| Verification reminder | “The approved fix is still applied. Please try the same PDF attachment when you can. If we do not hear back, a support person will review the open ticket.” |
| Verification timeout | “The fix remains applied, but JoltIQ could not confirm the original problem was resolved. A support person will review the ticket.” |
| Verified resolution | “The PDF opened without a new Outlook crash. JoltIQ verified the fix and resolved the ticket.” |
| Symptom persists | “Outlook still appears to crash when opening the PDF. JoltIQ will not try another change automatically. A support person will review it.” |
| Consent expired | “Permission for this session expired. JoltIQ stopped and made no further changes.” |
| Device revoked | “This computer is no longer authorized for JoltIQ support. The session was stopped.” |
| Connection lost | “JoltIQ lost contact with this computer. It will not repeat or count an unfinished action. A support person will review the recorded state.” |
| Emergency stop | “The session was stopped. JoltIQ will report any change that had already completed and will not start another action.” |

## Windows acceptance standard

### Build and packaging

- `Core`, `Windows`, `Tray`, `Broker`, installer, and test projects compile on a Windows CI runner.
- Windows unit/integration tests run on `windows-latest`.
- An unsigned development MSI may be used only on owner-controlled test VMs.
- A valid Authenticode-signed MSI and signed installed binaries are required before any external customer pilot or production release. Signing is therefore a releaseable-MVP gate, not a prerequisite for internal engineering validation.
- Install, repair, upgrade, rollback, and uninstall evidence is saved. Uninstall removes the broker and binaries but does not silently erase audit evidence held by the server.

### Real Windows execution

- The happy path passes five consecutive times on clean Windows 11 snapshots across at least two VM images.
- The happy path also passes once on supported Windows 10 before release.
- Each run begins from a known snapshot and records OS build, Outlook version/channel, companion version, procedure version/hash, and add-in fixture version.
- Repeatable cases use a purpose-built, harmless Outlook add-in/crash fixture or another authorized reproducible fixture; they must not damage a real user profile.
- At least one additional happy path runs on a licensed Microsoft 365 classic Outlook installation with a harmless real COM add-in and disposable test profile, proving safe close/restart/responding checks and local-journal restore/restart against the real application.

Every listed failure condition is run on Windows, not only in the simulator. Required cases include:

- New Outlook;
- Outlook absent;
- add-in absent;
- crash evidence absent or points at a different module;
- event log disabled, unreadable, stale, or observation health unknown;
- policy/machine-controlled add-in;
- locked screen/no interactive user;
- multiple interactive users/RDP;
- enrolled user and interactive tray user differ, including a change immediately before mutation;
- portal denial;
- local denial;
- consent expiry;
- changed/superseded plan approval;
- invalid signature;
- revoked device;
- duplicate command;
- expired command;
- connection loss before action, after the registry write, and before result delivery;
- ignored Outlook save prompt/safe-close failure;
- add-in write failure;
- restart/start failure;
- verification read-back failure;
- symptom still present;
- symptom test deferred;
- STOP before a step, during the registry step, and during restart;
- rollback accepted, declined, unavailable, and failed.
- rollback state supplied by the server, local rollback record missing, and restore succeeds but restart is pending;
- remembered/learned/chat fix attempts to bypass publication, and Windows screen-control dispatch is refused;
- executable publication self-approval, missing MFA, hash mismatch, and valid two-admin publication.

For every case, save a redacted evidence bundle containing:

- test identifier and environment manifest;
- companion log and stop timing;
- signed command IDs and statuses;
- server audit-chain verification;
- plan/procedure hashes and approvals;
- ticket/run/session final states;
- registry pre/post state where relevant;
- relevant Windows event records;
- screenshots of consent, pause, failure, rollback, and resolution messages;
- final process and desktop state;
- installer signature and installation state when applicable.

No test passes solely because the UI says it passed. The evidence must agree across server state, device state, audit records, and the customer-visible outcome.

## Broader product fit

The implementation must use generic procedure, device, policy, approval, execution, verification, and escalation primitives. Outlook-specific diagnosis belongs in typed Windows capabilities and this procedure, not in a tenant or MSP-only workflow. Organizations can later publish different approved roles and procedures for internal IT, SaaS support, customer operations, onboarding, or service delivery.

User-facing product language should prefer **organization**, **company**, **support team**, **customer/employee**, and **support person**. “MSP” remains one supported deployment model.

### Browser and SaaS priority

Browser-based SaaS work is a core JoltIQ execution surface, not a secondary enhancement. Most target companies perform support and service work in browser applications, so the roadmap must maintain browser automation alongside the Windows companion.

After the Windows Outlook workflow reaches its internal acceptance gate, the next required delivery is the incomplete browser/SaaS path, beginning with secure `browser.upload` support for approved ticket attachments. Browser work must retain the same consent, exact-procedure, approval, idempotency, independent-verification, human-authentication handoff, emergency-stop, and audit requirements as native Windows work.

The product is not considered broadly ready for company support roles until it can complete approved workflows that cross both surfaces—for example, diagnose or collect evidence on a Windows computer, then update a SaaS ticket, CRM, admin console, or customer portal in the isolated browser with independently verified results.

## Non-goals for this MVP

- Arbitrary AI-authored Windows fixes.
- Unrestricted screen control, shell execution, or force-closing applications.
- Supporting New Outlook with this procedure.
- Proving every future Windows support workflow.
- A macOS implementation of this procedure. macOS retains the protocol and safety principles but requires its own first workflow, native actions, and acceptance design.
- Production deployment, compliance certification, or security claims before external review.

## Owner decisions incorporated

- Windows is first; macOS follows with a different procedure and acceptance test.
- JoltIQ is not MSP-only and should ultimately cover company support/service roles.
- Browser and SaaS automation is a first-class product priority. Secure attachment upload and cross-surface Windows-to-SaaS workflows follow the Windows internal acceptance gate and must not be dropped from the roadmap.
- Diagnosis must be proven rather than inferred from a knowledge match.
- The executable plan is an exact published procedure, not an AI paraphrase.
- Fix application and symptom resolution are separate states.
- Partial changes, stop behavior, retries, disconnections, and unsupported environments are explicit and testable.
- Real Windows evidence is required for the happy path and every failure path.

## Builder calls made in this design

- Customer confirmation plus a healthy/readable 60-second crash-event observation is the symptom-level verification gate; a quiet event log without an attempted PDF open or trustworthy logging is insufficient.
- No response triggers one reminder at 10 minutes and technician escalation at 24 hours; it never resolves automatically.
- Rollback accepts only a source command ID and uses the tray user’s encrypted local journal, followed by safe restart or an explicit pending-restart state.
- Executable procedures require two distinct MFA-authenticated organization admins/owners against the same canonical hash.
- Read-only transient operations get one retry; state-changing operations rely on idempotency and are not automatically re-executed.
- The offline escalation limit is five minutes and individual commands expire after 120 seconds.
- Local STOP targets one second to prevent new work and two seconds for cooperative cancellation.
- Five consecutive Windows 11 snapshot runs across two VM images define repeatability; Windows 10 receives at least one full release acceptance run.
- Unsigned packages are restricted to owner-controlled engineering VMs; Authenticode signing is mandatory for an external pilot.
