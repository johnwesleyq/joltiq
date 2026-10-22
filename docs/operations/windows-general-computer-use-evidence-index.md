# Windows general computer-use evidence index

Last updated: 2026-10-22

## Current verdict

**Public launch is blocked.** Production signing, installer lifecycle evidence, disposable Windows 10/11 behavioral matrices, the supported local computer-use model gateway, remediation of the internal security-preflight findings, and an independent security decision are all still required.

The consolidated fail-closed public-launch status is recorded in `docs/operations/evidence/windows-public-release-readiness.json` and explained in `docs/operations/windows-public-release-gates.md`.

The native Windows implementation has passed focused and full automated tests on the enrolled physical Windows 11 PC. The general computer-use acceptance catalog, fail-closed bundle validator, secret scanner, STOP evidence contract, and aggregate completeness gate are implemented. These facts do not replace the required Windows 10/11 snapshot-reset runs.

## Available evidence

| Evidence | Status | Meaning |
|---|---|---|
| Native Windows xUnit suite | Passed: 72/72 | Real Windows capture, input, overlay, provider, policy, restart, and STOP-related unit/integration coverage passed on the physical PC. |
| Core self-test | Passed: 25/25 | Durable command/evidence ordering, recovery, and local policy contracts passed. |
| Guide/Fix API suites | Passed | Consent, immutable mode, Guide translation, device protocol, replay, and switch-to-Fix orchestration passed. |
| Web typecheck/lint/build/smoke | Passed | The two-mode customer controls and persistent Stop/control rail built and ran in a real browser. |
| Computer-use harness and packaging contract | Passed on physical Windows: contract validators and BuildGate 15/15 | Catalog completeness, PowerShell 5.1 compatibility, fail-closed evidence validation, secret rejection, native capability packaging, and build classification gates passed in the final physical-PC rerun. |
| Internal Windows package | Physical-PC artifact recorded | `JoltIQCompanion-1.0.0-x64.msi`, 83,289,883 bytes, SHA-256 `7a5951a52715cbdd16c22afad8e31c68ec4cedb1f7f5fe8cf8c25530ca25f3ae`; unsigned and internal-only. The build manifest record is linked below. |
| Disposable Windows 11 aggregate | Missing | Requires every Windows 11 case and repetition from `computer-use-cases.json`. |
| Disposable Windows 10 aggregate | Missing | Requires every Windows 10 case and applicable failure case. |
| Signed production MSI | Missing | Internal unsigned builds are not customer-release artifacts. |
| Independent security review | Missing | Required before an external unrestricted pilot. |
| Internal security preflight | Release blockers found | The 2026-10-22 fresh-context preflight is linked below. It is not the independent external review. |

## Evidence locations

- Catalog: `apps/windows-agent/scripts/acceptance/computer-use-cases.json`
- Runner: `apps/windows-agent/scripts/acceptance/Invoke-GeneralComputerUse.ps1`
- Validator: `apps/windows-agent/scripts/acceptance/EvidenceBundle.psm1`
- Lab procedure: `docs/operations/windows-general-computer-use-acceptance.md`
- Expected aggregate: `windows-computer-use-acceptance.json` at the lab evidence root
- Physical Task 10 build-manifest record: `docs/operations/evidence/windows-task10-build-manifest.json`
- Public-release readiness manifest: `docs/operations/evidence/windows-public-release-readiness.json`
- Public-release gate procedure: `docs/operations/windows-public-release-gates.md`
- Internal security preflight: `docs/security/windows-internal-security-preflight-2026-10-22.md`
- Independent-review scope and evidence template: `docs/security/windows-independent-review-scope.md`, `docs/security/windows-independent-review-evidence-template.md`
- Original physical-PC artifacts/logs: `C:\Users\admin\j10final\artifacts` (not a release distribution location)

No behavioral bundle or aggregate is linked here yet. Add only validator-approved bundles generated on named, freshly reverted disposable VM snapshots. Do not label the physical-PC test folder as a clean-VM aggregate.

## Release rule

Move this verdict to accepted only after all of the following are attached and independently checked:

1. The exact server, harness, and installer hashes.
2. A passing Windows 11 aggregate and Windows 10 aggregate with the catalog's required repetitions.
3. Measured STOP evidence for queued input, the pre-native race, and drag cancellation.
4. Zero secret-bearing or forbidden-content evidence and zero unapproved input.
5. A valid Authenticode signature and timestamp for the external MSI.
6. Independent security-review approval with no unresolved release-blocking findings.
