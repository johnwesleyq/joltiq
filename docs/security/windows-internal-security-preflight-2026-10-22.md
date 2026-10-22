# Windows internal security preflight — 2026-10-22

**Classification:** Internal fresh-context preflight. This is not the independent external security review and does not satisfy that release gate.

**Reviewed range:** `ff7e562..ea955cc`

**Decision:** Release blocked.

## Immediate containment completed

- External computer-use model routing is disabled by default.
- Production configuration now refuses startup when external computer-use models are enabled.
- The signed workflow output and MSI metadata are labeled as a signed candidate, not customer-approved release evidence.
- The public-readiness validator now refuses string-only passed evidence, requires typed hashed JSON attestations, verifies repository-confined files and hashes, binds evidence to the readiness commit/artifact digest, requires gate-specific evidence kinds, and validates the readiness timestamp.

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

### Minor

Protected-input label matching should add PIN, recovery/backup code, private key, SSN, client secret, access token, and API key, while retaining refusal for ambiguous credential dialogs.

## Reviewed strengths

The review found solid foundations in signed and expiring device envelopes, device binding, replay checks, durable command/outbox journaling, Guide input refusal, session/user/lock/remote checks, layout validation, dangerous-key restrictions, held-input release, and DPAPI-protected local rollback state.

## Required retest

Every finding needs a RED→GREEN automated regression plus the complete API/native/security suites. The exact corrected signed artifact and commit then go through disposable Windows 10/11 acceptance and the separate unaffiliated review defined in `windows-independent-review-scope.md`.
