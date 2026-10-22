# Windows internal security preflight — 2026-10-22

**Classification:** Internal fresh-context preflight. This is not the independent external security review and does not satisfy that release gate.

**Reviewed range:** `ff7e562..7f89ed4`

**Decision:** Release blocked.

## Immediate containment completed

- External computer-use model routing is disabled by default.
- Production configuration now refuses startup when external computer-use models are enabled.
- The signed workflow output and MSI metadata are labeled as a signed candidate, not customer-approved release evidence.
- The schema-version-1 public-readiness validator is monitoring-only and now always refuses launch promotion. Typed hashed JSON attestations remain inventory, not trusted release proof.

These changes prevent the reviewed cloud screenshot path and weak readiness labels from being used as a public-launch path. They do not provide the still-missing supported local model gateway or resolve the remaining findings below.

## Unresolved release-blocking findings

### Critical

1. **Scoped approval and deterministic policy for general computer-use mutations.** Model-produced input is not yet bound to a signed, expiring, single-use approval over the exact action, normalized parameters/target, frame digest, plan hash, and expected effect. The broad native goal consent is not a substitute.

### Important

1. **Broker client authentication.** The LocalSystem named-pipe broker currently permits the broad interactive SID and needs a trusted companion identity plus a signed one-time privileged command and broker replay journal.
2. **Acceptance provenance.** Operator-authored booleans are not lab-controller attestations. VM/image/snapshot/revert identity, installer signature/timestamp, common commits/artifact hashes, frame decoding, and signed source logs must be independently derived.
3. **Frame content binding.** Input binds to frame generation and display geometry but not a device-authored pixel digest, age limit, and capture-time accessibility/window identity checked immediately before input.
4. **Endpoint URL policy.** Native `url.open` needs a signed per-run origin allowlist, private/loopback/link-local/metadata refusal, port policy, redirect handling, and fresh approval for a new origin.
5. **Verification and learned-fix publication.** Model-declared success must not become `VERIFIED` or searchable learned guidance without independent typed postconditions and authorized publication.
6. **Production local intelligence.** A customer-controlled local vision/computer-use gateway is required before computer use can be enabled in production. The containment change intentionally fails closed until it exists.
7. **Trusted release evidence.** Schema version 2 must verify actual signed artifacts, an existing exact Git commit, lab-controller-signed disposable-VM reset provenance, OS-specific catalog completeness, decoded/redacted images, and an independently signed report. Hand-authored attestation fields cannot authorize promotion.
8. **Acceptance harness provenance and platform binding.** Both acceptance runners currently trust an operator-supplied snapshot name and do not prove the VM identity/reset or each case's declared operating system. Aggregation must consume trusted controller receipts and refuse mixed or reused hosts.
9. **Installer lifecycle assertions.** Lifecycle evidence must hash both MSIs, prove the installed version before and after upgrade, and accept downgrade refusal only for the expected Windows Installer product-version error—not any nonzero exit.
10. **Evidence-image validation.** The bundle validator currently trusts redaction metadata and filename dimensions without decoding or inspecting the image; its self-test even uses text with a PNG extension. Evidence cannot be release-grade until image format and redaction provenance are independently verified.

### Minor

Protected-input label matching should add PIN, recovery/backup code, private key, SSN, client secret, access token, and API key, while retaining refusal for ambiguous credential dialogs.

## Reviewed strengths

The review found solid foundations in signed and expiring device envelopes, device binding, replay checks, durable command/outbox journaling, Guide input refusal, session/user/lock/remote checks, layout validation, dangerous-key restrictions, held-input release, and DPAPI-protected local rollback state.

## Required retest

Every finding needs a RED→GREEN automated regression plus the complete API/native/security suites. The exact corrected signed artifact and commit then go through disposable Windows 10/11 acceptance and the separate unaffiliated review defined in `windows-independent-review-scope.md`.
