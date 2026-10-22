# Windows independent security review evidence

Complete this document only from the independent reviewer’s signed report. Do not mark the readiness gate passed from an internal or AI review.

## Engagement identity

- Reviewer or firm:
- Primary assessor:
- Independence/conflict statement:
- Qualifications:
- Statement of work identifier:
- Test start/end UTC:
- Report date UTC:
- Report version and SHA-256:
- Signature/attestation method:

## Tested release identity

- Git commit (40 characters):
- Source archive SHA-256:
- MSI filename and SHA-256:
- MSI Authenticode signer/thumbprint/timestamp:
- Tray executable SHA-256 and signature:
- Broker executable SHA-256 and signature:
- Build-manifest SHA-256:
- SBOM filename and SHA-256:
- Windows 10 image/build/snapshot:
- Windows 11 image/build/snapshot:
- Control-node image/version:
- Local-model runtime/model/version:
- Browser-worker image/version:

## Scope and method

- Scope document version/commit:
- Components tested:
- Components excluded and reason:
- Source/configuration review performed:
- Dynamic/API/protocol testing performed:
- Windows local/installer/update testing performed:
- Browser/network/egress testing performed:
- Licensing/device-clone testing performed:
- Tools and versions:
- Test accounts/data classification:
- Limitations:

## Findings

| ID | Severity | CWE/CVSS | Component | Summary | Status | Fix commit/artifact | Retest evidence |
|---|---|---|---|---|---|---|---|
| | | | | | Open / Fixed / Accepted | | |

## Mandatory release assertions

Record `PASS`, `FAIL`, or `NOT TESTED` and cite the report section/evidence.

| Assertion | Result | Evidence reference |
|---|---|---|
| No unapproved endpoint or browser input was achieved | | |
| Guide mode could not inject input | | |
| Consent, policy, lease, user, goal, and session expiry were enforced per action | | |
| Forged, modified, expired, duplicate, and replayed commands failed closed | | |
| STOP prevented subsequent input within the documented contract | | |
| Privileged actions could not bypass typed published procedures | | |
| Rollback used only the device’s matching local journal entry | | |
| Missing/ambiguous verification never closed a ticket | | |
| Tenant, device, ticket, browser-session, and evidence isolation held | | |
| Secrets and forbidden customer content were absent from retained evidence | | |
| Browser allowlist and protected-input boundaries resisted escape | | |
| Operational customer data did not egress the on-prem boundary | | |
| Licensing clone/replay failure was non-destructive | | |
| MSI/binaries/update chain resisted tampering and downgrade | | |
| Acceptance/readiness evidence failed closed when incomplete or inconsistent | | |

## Retest and final disposition

- Critical findings open:
- Important findings open:
- Minor findings open:
- Accepted residual risks and accountable owner:
- Fix/retest completion date UTC:
- Retested commit and artifact hashes:
- Reviewer’s final decision: `APPROVED FOR LIMITED PAID WINDOWS PILOT` / `RELEASE BLOCKED`
- Decision rationale:
- Reviewer signature/attestation:

The JoltIQ release owner separately verifies that the report covers the exact candidate hashes and that no Critical or Important finding remains unresolved before changing `independent_security_review.status` to `passed`.
