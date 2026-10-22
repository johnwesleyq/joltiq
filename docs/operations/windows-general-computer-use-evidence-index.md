# Windows general computer-use evidence index

Last updated: 2026-10-22

## Current verdict

**Release blocked pending the disposable-VM behavioral matrix and production signing.**

The native Windows implementation has passed focused and full automated tests on the enrolled physical Windows 11 PC. The general computer-use acceptance catalog, fail-closed bundle validator, secret scanner, STOP evidence contract, and aggregate completeness gate are implemented. These facts do not replace the required Windows 10/11 snapshot-reset runs.

## Available evidence

| Evidence | Status | Meaning |
|---|---|---|
| Native Windows xUnit suite | Passed: 71/71 before this harness increment | Real Windows capture, input, overlay, provider, policy, restart, and STOP-related unit/integration coverage passed on the physical PC. |
| Core self-test | Passed: 25/25 before this harness increment | Durable command/evidence ordering, recovery, and local policy contracts passed. |
| Guide/Fix API suites | Passed | Consent, immutable mode, Guide translation, device protocol, replay, and switch-to-Fix orchestration passed. |
| Web typecheck/lint/build/smoke | Passed | The two-mode customer controls and persistent Stop/control rail built and ran in a real browser. |
| Computer-use harness contract | Passed on physical Windows: contract validator and BuildGate 11/11 | Catalog completeness, PowerShell 5.1 compatibility, fail-closed evidence validation, secret rejection, and build classification gates passed. |
| Disposable Windows 11 aggregate | Missing | Requires every Windows 11 case and repetition from `computer-use-cases.json`. |
| Disposable Windows 10 aggregate | Missing | Requires every Windows 10 case and applicable failure case. |
| Signed production MSI | Missing | Internal unsigned builds are not customer-release artifacts. |
| Independent security review | Missing | Required before an external unrestricted pilot. |

## Evidence locations

- Catalog: `apps/windows-agent/scripts/acceptance/computer-use-cases.json`
- Runner: `apps/windows-agent/scripts/acceptance/Invoke-GeneralComputerUse.ps1`
- Validator: `apps/windows-agent/scripts/acceptance/EvidenceBundle.psm1`
- Lab procedure: `docs/operations/windows-general-computer-use-acceptance.md`
- Expected aggregate: `windows-computer-use-acceptance.json` at the lab evidence root

No behavioral bundle or aggregate is linked here yet. Add only validator-approved bundles generated on named, freshly reverted disposable VM snapshots. Do not label the physical-PC test folder as a clean-VM aggregate.

## Release rule

Move this verdict to accepted only after all of the following are attached and independently checked:

1. The exact server, harness, and installer hashes.
2. A passing Windows 11 aggregate and Windows 10 aggregate with the catalog's required repetitions.
3. Measured STOP evidence for queued input, the pre-native race, and drag cancellation.
4. Zero secret-bearing or forbidden-content evidence and zero unapproved input.
5. A valid Authenticode signature and timestamp for the external MSI.
6. Independent security-review approval with no unresolved release-blocking findings.
