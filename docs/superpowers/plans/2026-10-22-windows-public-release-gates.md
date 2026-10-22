# Windows Public Release Gates Implementation Plan

> **For Codex:** Execute this plan inline with `superpowers:executing-plans`. Do not relabel physical-PC, simulated, self-signed, or author-generated evidence as public-release evidence.

**Goal:** Close every Windows public-launch gate that can be completed with the available authorized infrastructure, and leave exact, auditable blockers for anything requiring a purchased identity certificate, disposable VM images, or an external reviewer.

**Spec:** `docs/superpowers/specs/2026-10-21-joltiq-computer-on-prem-design.md`

**Architecture:** Reuse the existing signing, package, lifecycle, and acceptance harnesses. Add only release-evidence orchestration and documentation needed to make the outcome machine-checkable. Public launch remains fail-closed unless every artifact is Authenticode-valid and timestamped, both disposable OS matrices pass, installer lifecycle passes, and an independent security report has no unresolved release blocker.

**Global constraints:**

- Never generate or use a self-signed certificate as public-release evidence.
- Never expose, copy, or record private keys, passwords, tokens, customer data, or unredacted screen contents.
- A physical Windows PC may supply engineering smoke evidence, but never substitutes for snapshot-reset disposable VMs.
- An internal code review does not substitute for the independent external security review required by the specification.
- Do not publish, distribute, or call an artifact customer-ready while any gate is incomplete.

---

## Task 1: Inventory the release environment and formalize gate evidence

**Files:**
- Create: `docs/operations/windows-public-release-gates.md`
- Create: `docs/operations/evidence/windows-public-release-readiness.json`
- Modify: `docs/operations/windows-general-computer-use-evidence-index.md`

**Steps:**

1. Inspect the Windows host for OS/build, installed code-signing certificates with private keys, Windows SDK `signtool`, Hyper-V/Windows Sandbox, and registered VMs. Do not print certificate private material.
2. Write a fail-closed readiness manifest whose four gates have explicit status, evidence references, and blocker text.
3. Add a validator test first for the manifest contract, watch it fail, then implement the validator/readiness document and make it pass.
4. Update the evidence index to point to the readiness manifest without changing any incomplete gate to complete.

**Expected:** The manifest validates, and unavailable prerequisites are recorded as blockers rather than inferred as success.

## Task 2: Produce and verify a signed Windows release artifact

**Files:**
- Reuse: `apps/windows-agent/scripts/sign.ps1`
- Reuse: `apps/windows-agent/scripts/build.ps1`
- Reuse: `.github/workflows/windows-release.yml`
- Modify: readiness/evidence documents only if no implementation defect is found

**Steps:**

1. If an authorized organization code-signing certificate with private key is present, build the tray and broker, sign both with SHA-256 and trusted timestamping, build the MSI, sign the MSI, and run `Assert-JoltIqAuthenticodeFile -RequireTimestamp` on all three.
2. Record artifact hashes, signer subject, certificate thumbprint, validity dates, signature status, and timestamp status without recording private material.
3. If the certificate is absent, keep the gate blocked and document the exact procurement/import step and protected CI secrets required. Do not substitute a self-signed certificate.

**Expected:** Either cryptographically verified signed evidence exists, or the readiness manifest clearly identifies the missing certificate as an external prerequisite.

## Task 3: Verify installer install, repair, upgrade, downgrade refusal, and uninstall

**Files:**
- Reuse: `apps/windows-agent/scripts/test-on-windows.ps1`
- Reuse: `.github/workflows/ci.yml`
- Modify: readiness/evidence documents

**Steps:**

1. Build the previous and current MSI versions from the same reviewed source lineage.
2. Run `test-on-windows.ps1 -InstallerLifecycle` on a clean disposable Windows VM, exercising fresh install, repair, upgrade, downgrade refusal, service identity/ACL, tray launch, and uninstall cleanup.
3. Save the JSON output and artifact hashes. A physical-host run may be recorded separately as a smoke test but cannot satisfy this gate.

**Expected:** Lifecycle JSON passes on a named clean snapshot and is referenced by the readiness manifest, or the gate stays blocked for lack of a disposable VM.

## Task 4: Run disposable Windows 10 and Windows 11 acceptance matrices

**Files:**
- Reuse: `apps/windows-agent/scripts/acceptance/Invoke-GeneralComputerUse.ps1`
- Reuse: `apps/windows-agent/scripts/acceptance/Invoke-OutlookPdfMvp.ps1`
- Reuse: `docs/operations/windows-general-computer-use-acceptance.md`
- Reuse: `docs/operations/windows-outlook-pdf-acceptance.md`
- Modify: readiness/evidence documents

**Steps:**

1. Discover registered disposable Windows 10 and Windows 11 VMs and their clean named snapshots.
2. For each required case, revert before execution, run only with test identities/data, collect redacted evidence, and validate the bundle.
3. Generate the aggregate acceptance JSON and require `passed: true` for both operating systems. Include the licensed classic-Outlook case required by the existing acceptance definition.
4. If either VM/image/snapshot is absent, leave this gate blocked and give the exact image/snapshot requirements.

**Expected:** Validator-approved aggregates exist for both Windows 10 and Windows 11, or the readiness manifest remains blocked with exact missing infrastructure.

## Task 5: Obtain an independent security decision

**Files:**
- Create: `docs/security/windows-independent-review-scope.md`
- Create: `docs/security/windows-independent-review-evidence-template.md`
- Modify: readiness/evidence documents

**Steps:**

1. Prepare a reviewer packet covering threat model, architecture, signing/update chain, tenant/device boundaries, consent, command authenticity, replay/idempotency, STOP behavior, local journaling/rollback, browser/desktop input restrictions, evidence redaction, licensing, and on-prem network boundaries.
2. Run a fresh-context internal security-focused code review as a preflight and fix any Critical or Important implementation findings with RED→GREEN tests.
3. Require the final independent report to identify reviewer/firm, dates, tested commit/artifacts, methods, findings, retest outcomes, and a signed release decision.
4. Do not mark the gate complete from the author’s or an affiliated internal review. If no independent reviewer report is available, leave it blocked and hand off the prepared packet.

**Expected:** Either a qualifying independent report has no unresolved release-blocking finding, or an audit-ready packet exists and the gate remains explicitly blocked.

## Task 6: Recompute readiness and verify fail-closed launch status

**Files:**
- Modify: `docs/operations/evidence/windows-public-release-readiness.json`
- Modify: `docs/operations/windows-general-computer-use-evidence-index.md`

**Steps:**

1. Run all local automated suites and evidence validators.
2. Recompute each gate strictly from referenced evidence.
3. Set `public_launch_ready` true only if all four gates pass; otherwise list every remaining owner/action without optimistic language.
4. Commit the evidence and run a final whole-branch review.

**Expected:** The repository provides one honest source of truth for launch readiness, with no incomplete gate hidden by an engineering smoke result.

## Review Focus

- Any path that lets unsigned or untimestamped artifacts be labeled external-ready.
- Any path that lets a physical PC, simulator, reused snapshot, or incomplete catalog count as disposable-VM acceptance.
- Installer upgrade/downgrade/cleanup false positives.
- Evidence containing secrets or customer content.
- Internal review being presented as independent external security review.
- Readiness becoming true when evidence files are missing, malformed, stale, or refer to different artifact hashes/commits.
