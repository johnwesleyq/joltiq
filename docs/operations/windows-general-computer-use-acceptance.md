# Windows general computer-use acceptance

## Purpose

This runbook proves JoltIQ's Windows observe → decide → act → verify loop and the different authority of **Guide me** and **Fix it for me**. It does not authorize arbitrary shell, registry, service, installer, or secure-desktop control.

Passing the PowerShell harness validator proves the catalog and fail-closed evidence rules work. It does **not** prove the product behavior. Product acceptance requires every catalog case to pass on the disposable Windows virtual machines below.

## Required lab

- Windows 11 64-bit and Windows 10 64-bit VMs with one unlocked local interactive user.
- Named clean snapshots, reverted before every case. A normal physical PC is useful for native compilation and smoke testing, but is not a substitute for this resettable matrix.
- A test tenant, test ticket, test device enrollment, and a server build pinned to an exact Git commit.
- The exact MSI under test. External-release evidence requires a valid Authenticode signature and timestamp; an unsigned internal build must remain labeled internal-only.
- Notepad and purpose-built local test pages for password/OTP controls. Never use real credentials, customer documents, email, cookies, or production accounts.

## Validate the harness

From Windows PowerShell 5.1:

```powershell
powershell -executionpolicy bypass -file apps\windows-agent\scripts\test-on-windows.ps1 -ValidateComputerUseHarness
powershell -executionpolicy bypass -file apps\windows-agent\scripts\tests\BuildGate.Tests.ps1
```

Both commands must pass before a behavioral run begins.

## Run one case

1. Revert the correct Windows VM to its named clean snapshot.
2. Install the exact companion MSI and enroll only the local test user.
3. Initialize the case, replacing the example values:

```powershell
powershell -executionpolicy bypass -file apps\windows-agent\scripts\test-on-windows.ps1 `
  -GeneralComputerUse `
  -CaseId notepad-fix-win11 `
  -RunId 20261022-001 `
  -SnapshotName win11-24h2-clean `
  -Msi C:\lab\JoltIQCompanion.msi `
  -ServerCommit 0123456789abcdef
```

4. Exercise only the named case. Preserve the exact server/device/ticket IDs and the redacted evidence below.
5. Finalize the bundle:

```powershell
powershell -executionpolicy bypass -file apps\windows-agent\scripts\test-on-windows.ps1 `
  -GeneralComputerUse `
  -CaseId notepad-fix-win11 `
  -RunId 20261022-001 `
  -Finalize
```

6. Revert the VM again before the next case. Never reuse a dirty snapshot or evidence directory.

## Evidence contract

Every bundle must contain:

- `environment.json`: exact OS/build, 64-bit architecture, agent version, server commit, harness commit, and fixture version.
- `snapshot.json`: named snapshot, clean flag, and revert time.
- `installer.json`: exact MSI digest, installed version, signer, and signature status.
- `session.json`: session/run IDs, immutable `guide` or `fix` mode, both consent records, SHA-256 goal hash, and final state. Do not store the raw support goal.
- `commands.json`: signed command IDs, actions, terminal statuses, successful signature checks, and zero accepted unsigned commands.
- `audit.json`: valid audit chain with redacted entries.
- `companion.jsonl`: sanitized companion events only.
- `frames/index.json` plus redacted images: frame generation, layout hash, scaled dimensions, and redaction flag. The index may describe monitor geometry, but not customer content.
- `stop-timing.json`: explicit applicability. STOP cases must record an observed value and prove the 500 ms p95 / 2 second maximum contract.
- `input.json`: requested, executed, and refused actions; zero forbidden actions; zero Guide-mode input; confirmation that held keys/buttons were released.
- `desktop.json`: final process/desktop state and explicit confirmation that no customer content or secrets were collected.
- `result.json`: exact case ID, catalog outcome, pass flag, and server/device/ticket agreement.

The validator rejects missing evidence, malformed hashes, missing frame files, unsafe frame names, raw secrets, common customer-content fields, Guide input, unreleased held input, unsigned commands, incomplete STOP measurements, and outcomes that disagree with the catalog.

## Required behavioral coverage

The catalog is authoritative. It covers repeated Notepad Guide/Fix paths on Windows 11, Windows 10 paths, multi-monitor/DPI mapping, stale layout and capture races, lock/UAC/secure desktop, password and OTP targets, focus theft, enrolled-user/shared/RDP mismatch, both consent declines, all STOP races, offline/expiry/duplicate/modified replay, tray/capture recovery, Guide input refusal and replay, and fresh consent when switching to Fix.

For Guide cases, a person performs every click and keystroke. For Fix cases, only the bounded signed input in the approved run may execute. At every refusal or STOP case, inspect the final desktop—not just the API result—to prove no later or stuck input occurred.

## Acceptance decision

`windows-computer-use-acceptance.json` is generated at the evidence root. Acceptance passes only when it says `passed: true`, every required repetition is valid, no invalid run is present, and the evidence index identifies the exact aggregate and hashes. Until that exists for both disposable OS images, the feature is engineering-complete but not approved for unrestricted customer deployment.
