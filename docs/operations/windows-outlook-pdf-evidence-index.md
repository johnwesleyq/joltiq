# Windows Outlook PDF MVP evidence index

**Recorded:** 2026-10-20 (America/Denver)  
**Branch:** `codex/windows-outlook-mvp`  
**Implementation baseline:** `aace42906eb4ea52de2263b3c271ccced9627222`  
**Branch base:** `dc218d2bcc994991ddd2d5e4d2226caeedc3f5fe`

This index deliberately separates implementation from execution evidence. A source file, test, or CI workflow is not proof that Windows accepted the workflow.

## Current verdict

| Evidence level | Status | Evidence / blocker |
|---|---|---|
| Implemented | **YES** | Tasks 1–11 are committed on this branch. The server, protocol, companion, Windows providers, installer scripts, acceptance harness, and release workflows exist. |
| Simulator-tested | **YES, with a current limitation** | Server security/procedure tests and the cross-platform companion self-test pass. The Task 12 API run skips two simulator-spawning tests because `dotnet` is unavailable natively on this Mac; the companion Core self-test passes in the .NET 8 SDK container. |
| Windows-CI-tested | **NO — BLOCKED_EXTERNAL_DEPENDENCY** | Workflows exist, but this checkout has no Git remote and no `windows-latest` run or downloadable CI evidence exists. |
| Windows-VM-accepted | **NO — BLOCKED_EXTERNAL_DEPENDENCY** | No authorized Windows 10/11 VM evidence bundle exists. None of the 59 required runs has been accepted. |
| Signed | **NO** | No MSI was produced in this checkout and no Authenticode/timestamp evidence exists. Any locally produced package is **unsigned internal-only**. |
| Externally reviewed | **NO** | The focused review below is an implementer self-review. Independent code review and penetration testing remain release blockers. |

**Release decision:** `IMPLEMENTED_NOT_TESTED` for Windows runtime behavior and `BLOCKED_EXTERNAL_DEPENDENCY` for Windows CI, VM acceptance, signing, and external review. Do not deploy this workflow to customers or call it pilot-ready.

## Fresh Task 12 verification

| Check | Result |
|---|---|
| API full suite | **152 passed, 2 skipped, 0 failed** in 64.68 s on the final post-review run. The two skipped tests spawn the .NET simulator, and native `dotnet` is unavailable on this Mac. The plan's literal `.venv/bin/pytest` first failed because the editable package was not on this worktree venv's import path; the corrected invocation used `PYTHONPATH=.` and an isolated test database through the local proxy. |
| API lint | `ruff check joltiq tests` — passed. |
| API types | `mypy joltiq` — passed after correcting branch-introduced type errors. |
| Python dependency audit | **CLEARED:** upgraded `certifi` to `2026.10.19`; the complete API/browser suite passed with 152 passed and 2 expected skips, and `pip-audit -r requirements.lock` reported no known vulnerabilities. |
| Web | `npm run typecheck`, `npm run build`, and `npm audit --omit=dev --audit-level=high` — passed; audit reported zero vulnerabilities. |
| Companion Core | .NET 8 SDK container: **20 passed, 0 failed**. The final security self-review added coverage proving a reused command ID with altered signed parameters is refused instead of receiving cached success. |
| Acceptance harness contract | .NET 8 SDK container: catalog, complete synthetic bundle, missing-evidence rejection, and secret-evidence rejection all passed. This validates the harness, not Windows behavior. |
| Windows build and xUnit | Not run: this is macOS and no Windows runner evidence is available. |
| Windows acceptance | Not run: requires authorized disposable Windows 10/11 VMs and licensed classic Outlook for the real-Outlook case. |

## Windows acceptance matrix

`apps/windows-agent/scripts/acceptance/cases.json` defines **56 cases and 59 required runs**:

- Five clean-snapshot Windows 11 fixture happy-path runs, split across two VMs.
- One Windows 10 fixture happy path.
- One Windows 11 run with licensed classic Outlook and a harmless real add-in.
- Fifty-two diagnosis, consent, identity, delivery, restart, rollback, STOP, side-door, and publication runs.

Every accepted run must provide the 14 evidence classes required by the catalog. The aggregate `windows-acceptance.json` must say `passed: true`. There is currently no aggregate file.

## Reproducible source hashes

| File | SHA-256 |
|---|---|
| `apps/windows-agent/scripts/acceptance/cases.json` | `ddf4561b984811d05f0b79ad21a6fbaf7dc4a50a56a7eb39f90e7a086a066bea` |
| `apps/windows-agent/scripts/acceptance/EvidenceBundle.psm1` | `45a88a87c54c9978ad94c31873d2b42a2ba43458482bcc500a454e7ff8137228` |
| `apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1` | `75c64fe92317e9c7300823dbdd682e1c2f14279c49e647218a4beefc73a32302` |
| `apps/windows-agent/scripts/build.ps1` | `711f9fb31fed16b6056f9ffcca2d0a2ac1e02058bd1a41c597927a2369202d72` |
| `apps/windows-agent/scripts/test-on-windows.ps1` | `6debc5f4b8179bf9fc3e85d9dc788bc7785fa503d160f429ed2e93bc19cbfda3` |
| `.github/workflows/ci.yml` | `e75d89b53e3bb1e8f10727daa8ee0301d6fa43fbe8a25e13f94c4881af8f37bc` |
| `.github/workflows/windows-release.yml` | `0f3e12db5656fac317478cab2c7ab2f4a2fa6e35817613e20450083569954883` |

## Focused security self-review

| Review item | Implemented evidence | Remaining proof required |
|---|---|---|
| Two-admin MFA publication and procedure hash | API tests require distinct MFA-backed admins and identical publication hash. | External review and production identity-provider/MFA configuration. |
| Learned/chat fixes cannot execute | Candidate selection accepts only published IDs; dedicated API and acceptance cases reject remembered-fix execution. | Windows VM evidence. |
| Windows screen control excluded | The local desktop allowlist has no screen-control action; Core and acceptance cases cover refusal. | Windows VM evidence. |
| Policy precedence and user identity | HKCU/HKLM policy, interactive session count, screen state, enrolled SID, and just-before-write SID checks exist. | Real Windows 10/11 execution evidence. |
| Tray/SYSTEM separation | User-scoped mutation is routed to the interactive tray; broker allowlist excludes this registry write. | Service/tray process evidence on Windows. |
| Local rollback authority | Restore accepts only `source_command_id` and reads the DPAPI-protected local journal; server-supplied prior values are rejected. | DPAPI/runtime evidence on Windows. |
| Journal, outbox, duplicates, and late results | Core self-test and API protocol tests cover persistence, restart recovery, exact-parameter duplicate matching, expiry, and terminal late-result reconciliation. | Power-loss/offline acceptance cases on Windows. |
| Event-log health and symptom resolution | Unreadable, disabled, stale, or untrustworthy logging returns `UNKNOWN`; symptom resolution is separate from fix verification. | Real Event Log and Outlook crash observation evidence. |
| STOP races | Core tests cover queued and in-progress cancellation with monotonic evidence. | Measured stop timing for all three Windows acceptance race cases. |
| Session identity and signed customer copy | Command/session/device/procedure identity is bound into signed envelopes and exact native copy is passed to the tray. | Windows UI screenshots and signature evidence. |
| Redaction | Server and Core tests cover secrets; evidence bundle rejects known secret forms. | Independent privacy review of real bundles. |

## Next gates

1. Connect a Git remote and run the Windows CI workflow; retain TRX, MSI lifecycle, checksum, and workflow evidence.
2. Run all 59 acceptance executions on disposable Windows 10/11 VMs and produce a passing aggregate.
3. Configure the protected signing environment and produce an Authenticode-valid, timestamped MSI for any pilot.
4. Obtain independent security review.
5. After Windows internal acceptance, implement the separate secure `browser.upload` plan. Browser/SaaS automation remains the product priority; it does not inherit Windows acceptance merely because the safety protocol is reusable.
