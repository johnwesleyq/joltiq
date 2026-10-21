# Windows Outlook PDF MVP acceptance

This is the release gate for the first JoltIQ autonomous Windows workflow. It must be run on authorized, disposable Windows VMs. Simulator and macOS results do not count as Windows acceptance.

## Supported release target

- Windows 10 or 11, 64-bit, with one unlocked local console user.
- Classic Outlook from a licensed Microsoft 365 installation. New Outlook is deliberately unsupported.
- Five clean-snapshot happy-path runs on Windows 11 across two independently built VM images, one happy path on Windows 10, every catalogued failure case, and one Windows 11 run with licensed classic Outlook, a disposable profile, and a harmless real COM add-in.
- The included fixture is only for repeatable registry and Application-log testing. It registers a throwaway HKCU add-in identity and emits a synthetic matching or nonmatching event. It does not load into Outlook and does not prove real Outlook restart behavior.
- An unsigned installer is allowed only for isolated internal MVP testing and must be recorded as `NotSigned`. A production pilot or customer deployment requires an Authenticode-valid installer. Never relabel an unsigned run as production acceptance.

## Safety and privacy

Create a named clean VM snapshot before every run. Use test tenants, test mailboxes, disposable Outlook profiles, and harmless attachments. Do not collect email bodies, attachment contents, customer documents, credentials, cookies, tokens, private keys, or complete unredacted event records. Screenshots must be cropped or redacted before entering the bundle.

The Windows companion exposes no screen-control action in this MVP. A remembered or learned fix cannot create an executable desktop plan. Those controls have dedicated acceptance cases in `cases.json`.

Run the mutation as the enrolled interactive user from the tray process. Never run the companion tray as Administrator or SYSTEM. The diagnostic and the immediate pre-write identity check must name the same SID.

## Preflight

1. Revert the VM to the named snapshot and record the UTC time.
2. Connect the VM only to the isolated JoltIQ test environment.
3. Install the exact MSI under test. Record its SHA-256, product version, signature state, Windows edition/display version/build, agent version, server commit, harness commit, and fixture version.
4. For the licensed-Outlook case, record the exact Outlook version, confirm licensing, create a disposable profile, and install the approved harmless real COM add-in.
5. Validate and list the harness:

   ```powershell
   pwsh apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1 -List
   pwsh apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1 -ValidateHarness
   ```

6. Initialize one run. Evidence defaults to `%ProgramData%\JoltIQ\AcceptanceEvidence`, outside the repository:

   ```powershell
   pwsh apps/windows-agent/scripts/test-on-windows.ps1 `
     -CaseId happy-fixture-win11-a `
     -RunId win11-a-001 `
     -SnapshotName win11-a-clean `
     -ServerCommit 0123456789abcdef `
     -Msi C:\Builds\JoltIQCompanion.msi
   ```

The command refuses unknown cases, reused run IDs, non-Windows hosts, 32-bit Windows, and an unnamed snapshot. Fixture actions require an authorized elevated terminal only when Windows requires it to create the synthetic Application event.

## Execute and collect

Use the exact case in `cases.json`. Do not adjust the expected outcome after seeing the result. Export redacted server records into the run directory using these fixed names:

For supported text evidence, sanitize into the bundle instead of copying raw exports: `Import-Module .\EvidenceBundle.psm1; Protect-JoltIqEvidenceFile -Source <export> -Destination <bundle-file>`. Review the sanitized output; the final validator also rejects known secret forms.

| File | Required proof |
| --- | --- |
| `environment.json` | Exact OS, build, agent, server, harness, fixture, and—when applicable—Outlook versions |
| `snapshot.json` | Named clean snapshot and revert time |
| `installer.json` | Install state, MSI hash, product version, and signature status |
| `procedure.json` | Published definition hash, hash match, publisher MFA, and distinct second admin |
| `workflow.json` | Ticket, run, and session IDs and final states |
| `commands.json` | Signed command IDs, statuses, expiry/duplicate disposition, and signature-check result |
| `audit.json` | Ordered audit entries and verified chain result |
| `companion.jsonl` | Redacted companion events and any already-completed change; no secrets |
| `registry.json` | Typed HKCU value before, after, and independent read-back, including registry view |
| `events.json` | Readable/enabled/recording health plus redacted event observations; `raw_event_payload_collected` must be false |
| `stop-timing.json` | Target and observed stop latency, or explicit `applicable: false` |
| `screenshots/index.json` | Index of at least one redacted screenshot and what it proves |
| `desktop.json` | Final Outlook/process/desktop state and `customer_content_collected: false` |
| `result.json` | Catalogued expected result, `passed: true`, and `server_device_ticket_agree: true` only after manual comparison |

A green UI is not evidence. Ticket, run, session, command journal, device state, audit chain, registry, event observations, and desktop state must agree. A missing item, malformed JSON, secret-like content, forbidden customer content, wrong expected state, failed STOP target, or disagreement fails the run.

Finalize the run only after all artifacts are present:

```powershell
pwsh apps/windows-agent/scripts/test-on-windows.ps1 `
  -CaseId happy-fixture-win11-a `
  -RunId win11-a-001 `
  -EvidenceRoot C:\ProgramData\JoltIQ\AcceptanceEvidence `
  -Finalize
```

Finalization hashes every artifact into `manifest.json`. It also rebuilds `windows-acceptance.json`. The aggregate passes only when every required catalog case and repetition has a valid bundle.

## Exact customer wording to verify

The server owns this wording. Capture it at each applicable pause or failure and compare it exactly, including the approved procedure's add-in display name and ProgID.

| Point | Exact text |
| --- | --- |
| Portal consent | “JoltIQ found an approved fix for the Outlook PDF crash and is requesting permission to diagnose and work on this PC. You can stop the session at any time.” |
| Waiting for local consent | “Please accept the JoltIQ prompt on the PC. The read-only check will confirm the Windows user, classic Outlook, the named add-in, company policy, and matching crash evidence.” |
| Native consent | “JoltIQ wants to check Outlook crash records and apply the approved fix on this computer. It will disable {published add-in name} and safely restart Outlook. It will not force Outlook closed. Allow this session?” |
| Disable approval | “Disable {published add-in name} ({published ProgID}) for your Windows account. JoltIQ will save the current setting so it can be restored.” |
| Restart approval | “Safely restart classic Outlook to apply the change. If Outlook does not close normally, JoltIQ will stop and ask for help.” |
| Active banner | “JoltIQ support is active on this PC — STOP ends control immediately.” |
| Diagnostic mismatch | “JoltIQ could not confirm that the approved PDF-preview add-in caused this crash. Nothing was changed. A support person will review it.” |
| Unsupported setup | “This Outlook or Windows setup is not supported by this approved fix. Nothing was changed. A support person will review it.” |
| Consent expired | “Permission for this session expired. JoltIQ stopped and made no further changes.” |
| Device revoked | “This computer is no longer authorized for JoltIQ support. The session was stopped.” |
| Connection lost | “JoltIQ lost contact with this computer. It will not repeat or count an unfinished action. A support person will review the recorded state.” |
| STOP | “The session was stopped. JoltIQ will report any change that had already completed and will not start another action.” |
| Partial application | “The add-in was disabled, but Outlook could not be safely restarted. JoltIQ did not force it closed. The change has not been undone.” |
| Rollback offer | “Restore the add-in setting that was present before this session?” |
| Rollback declined/unavailable | “The original add-in setting has not been restored. The change remains in place, and a support person will restore it manually.” |
| Restored and restarted | “The original add-in setting was restored and verified, and Outlook was safely restarted. The original PDF problem is still open for a support person to review.” |
| Restored, restart pending | “The original add-in setting was restored and verified. Outlook could not be safely restarted, so the restored setting will take effect the next time Outlook starts.” |
| Symptom test | “Please open the same PDF attachment now. What happened?” |
| Test deferred | “The approved fix was applied and checked, but the original problem has not been confirmed resolved. The ticket will remain open.” |
| Reminder | “The approved fix is still applied. Please try the same PDF attachment when you can. If we do not hear back, a support person will review the open ticket.” |
| 24-hour timeout | “The fix remains applied, but JoltIQ could not confirm the original problem was resolved. A support person will review the ticket.” |
| Resolved | “The PDF opened without a new Outlook crash. JoltIQ verified the fix and resolved the ticket.” |
| Symptom persists | “Outlook still appears to crash when opening the PDF. JoltIQ will not try another change automatically. A support person will review it.” |
| Unknown verification | “The PDF test was reported, but JoltIQ could not verify the Windows crash log reliably. A support person will review the open ticket.” |

## Failure and cleanup rules

- Never retry the registry mutation automatically. Duplicate delivery must replay the journaled result without a second effect.
- If Outlook displays a save prompt or will not close normally, do not force it. Record the applied setting and offer rollback.
- A rollback names only the earlier command. The tray restores its own journaled value, reads it back, then separately requests a safe Outlook restart. Missing local lineage is a refusal.
- If the PC disconnects or a command expires, preserve command identity and already-completed effects. Late results must not be counted twice.
- STOP must prevent the next action within the target recorded in `stop-timing.json`; report any atomic write that already completed.
- A quiet log is not clean unless the Application log is readable, enabled, recording, and covers the observation window. Otherwise the symptom result is `UNKNOWN`.
- Remove the fixture with `dotnet run --project <fixture-project> -- remove --progid JoltIQ.OutlookPdfAcceptance`. Revert the VM after copying the redacted bundle to approved test storage.
