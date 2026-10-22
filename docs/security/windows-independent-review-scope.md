# Windows independent security review scope

## Purpose and release decision

This packet defines the minimum independent security review and penetration-test scope for the first paid Windows pilot. The reviewer must test the exact Git commit and release artifact hashes and issue a written decision. The Windows public-release gate passes only when an unaffiliated qualified reviewer reports no unresolved Critical or Important release-blocking finding.

An internal review, automated scanner, AI review, self-signed build, simulator result, or review of a different commit is preflight evidence only. It cannot approve release.

## System under review

- Customer-hosted JoltIQ control node: portal, API/worker, PostgreSQL with forced RLS, Redis, object storage, isolated browser worker, update verifier, license client, and customer-controlled local-model gateway.
- Windows companion: interactive-user tray, LocalSystem broker, signed device protocol, local policy, durable command journal, consent UI, guide overlay, capture/input adapter, STOP, and typed privileged procedures.
- Browser/SaaS execution: isolated Playwright context, network allowlist, protected customer login/MFA input, ticket-bound upload, download quarantine, control lease, and independent read-back.
- Trust services: session/command signing, device identity, licensing identity and leases, update signing, procedure publishing, audit chain, and release signing.
- Evidence-producing harnesses and the exact externally signed MSI, tray executable, and broker executable.

## Required artifacts

The reviewer receives read-only access to:

1. exact source commit and dependency lockfiles;
2. signed MSI, installed tray/broker binaries, SHA-256 checksums, signer/timestamp details, build manifest, SBOM, and CI provenance;
3. architecture and data-flow diagrams plus `SECURITY.md`, `packages/agent-protocol/PROTOCOL.md`, and the approved product design;
4. sanitized Windows 10/11 acceptance aggregates and installer-lifecycle evidence;
5. test tenant, disposable Windows VMs, test endpoints, test accounts, test SaaS tenant, and non-production data;
6. deployment, key management, backup/restore, incident response, update, licensing, and evidence-retention runbooks; and
7. a finding tracker with owner, severity, status, fix commit, retest evidence, and residual risk.

Never send production customer tickets, screenshots, documents, credentials, tokens, private keys, model data, or unredacted audit events to the reviewer.

## Threat actors and trust boundaries

The assessment must include an unauthenticated network attacker, malicious ticket/request content, compromised ordinary user, malicious or compromised tenant administrator, compromised enrolled endpoint, replaying network adversary, malicious SaaS page/prompt injection, dependency/build compromise, stolen installer, cloned VM/device activation, and a customer infrastructure administrator who controls host/hypervisor/time.

Review these boundaries independently:

- customer/tenant and organization isolation;
- portal user versus local interactive endpoint user;
- server versus device signing identities;
- tray user process versus LocalSystem broker;
- local screen/input control versus typed privileged device actions;
- isolated browser session versus endpoint browser/customer desktop;
- knowledge/retrieval data versus executable procedures;
- licensing and update endpoints versus operational customer data;
- customer local model gateway versus external network; and
- release signing identity versus runtime/session signing identities.

## Required test areas

### Authentication, authorization, and isolation

- Attempt horizontal and vertical tenant/device/ticket/session access, including direct object reference, RLS bypass, stale role, break-glass misuse, and cross-customer browser/session storage.
- Verify MFA and two-person publication for customer-authored executable procedures.
- Confirm the enrolled user, current interactive user, ticket/device, and consented goal are bound and rechecked before every action.

### Command authenticity, replay, and durable execution

- Forge, alter, reorder, duplicate, expire, and replay commands and results.
- Test idempotency across disconnect, restart, crash, delayed response, and journal recovery.
- Confirm duplicate or ambiguous results cannot be counted twice and cannot close a ticket.
- Attempt to turn learned knowledge, remembered fixes, model output, or screen observations into unauthorized typed privileged actions.

### Consent, control lease, STOP, and session changes

- Bypass portal consent, native consent, per-step approval, session expiry, mode immutability, and control-lease ownership.
- Exercise STOP during queued input, immediately before native input, during held key/mouse actions, and after an irreversible side effect begins.
- Switch users, lock the screen, attach RDP/shared sessions, change displays/layout/scaling, restart adapters, and steal focus.
- Confirm Guide mode cannot inject input and switching to Fix requires a new executable plan and consent.

### Desktop and browser input restrictions

- Target password, OTP, card, secure desktop/UAC, lock screen, CAPTCHA, payments, permission changes, public posts/messages, destructive actions, installers, shells, registry, services, and policy controls.
- Attempt stale-frame clicks, coordinate confusion, overlay/input path crossover, multi-monitor escape, clipboard leakage, inaccessible canvas guessing, and prompt injection from screen/page content.
- Escape the browser domain/network allowlist through redirects, subresources, DNS rebinding, alternate schemes, downloads, uploads, service workers, WebSockets, and local-network targets.
- Verify login/MFA customer handoff and secret masking do not persist secrets in tickets, model context, frames, logs, evidence, or browser state beyond policy.

### Journaling, rollback, and verification

- Alter or remove local journal entries, request rollback for an unrelated change, supply server-chosen restore values, and interrupt restore/restart.
- Verify a restore refers only to the device’s own matching journal entry and is independently read back.
- Cause unavailable, failed, partial, and contradictory verification; none may become `VERIFIED` or close the ticket.
- Test safe application restart with unsaved work and confirm the product never force-closes through the approved Outlook procedure.

### Signing, installer, update, and supply chain

- Verify Authenticode chain/timestamp on MSI and installed binaries, publisher identity, downgrade refusal, repair, uninstall cleanup, least privilege, service ACL, and update rollback/recovery.
- Tamper with release manifest, package digest, SBOM identity, migration range, edition, version monotonicity, and update signature.
- Review protected key access, dual control, CI permissions, secret cleanup, provenance, dependency scanning, and incident revocation.

### Licensing and device identity

- Copy activation files, clone images/keys, change MAC/network adapters, roll back time, replay leases, revoke seats, exceed grace, transfer rapidly, and mismatch product edition.
- Confirm a failed license blocks new control but preserves audit export, deactivation, backup, read-only administration, and uninstall without deleting customer data.

### On-premises data boundary and model safety

- Observe and block attempted egress of tickets, screenshots, documents, prompts, responses, embeddings, learned fixes, credentials, audit records, model data, and telemetry.
- Confirm only configured SaaS/integration destinations and optional license/update endpoints are reachable for their explicit purpose.
- Verify the on-prem edition cannot silently fall back to an external model and customer content does not train weights automatically.
- Test malicious tickets, documents, web pages, and knowledge uploads as untrusted data, including instructions to exfiltrate, widen scope, or disable controls.

### Evidence and privacy

- Inject common secret formats and forbidden customer-content fields into every evidence channel.
- Confirm frame redaction, safe filenames, hash integrity, audit-chain integrity, retention, export controls, and absence of private keys/raw tokens.
- Verify acceptance and release readiness fail closed for missing, malformed, stale, mixed-commit, mixed-artifact, or physical-PC-only evidence.

## Methodology and severity

Use source review, configuration review, dependency/SBOM analysis, static and dynamic testing, authenticated and unauthenticated API testing, protocol fuzzing, Windows local privilege and persistence testing, browser isolation testing, network capture/egress testing, installer/update tampering, and manual abuse-case testing. Map findings to CWE and, when appropriate, CVSS, but grade release impact using the product’s safety guarantees.

- **Critical:** plausible compromise causing cross-customer disclosure, arbitrary privileged execution, secret theft, release/update signing compromise, or unapproved control at scale.
- **Important:** bypass of consent/policy/verification, repeatable unauthorized input, meaningful isolation failure, unsafe rollback, or other behavior incompatible with a paid pilot.
- **Minor:** hardening or defense-in-depth issue with no direct release-boundary violation under the tested conditions.

Any unresolved Critical or Important finding blocks the release. Accepted residual risk must be explicit, owned by JoltIQ leadership, time-bounded, and cannot waive the core no-unapproved-input, no-secret-capture, no-cross-customer-disclosure, or no-false-verification guarantees.

## Required deliverables

- executive summary and signed release decision;
- reviewer/firm identity, independence statement, qualifications, dates, and methodology;
- exact commit, artifact hashes, environments, tools, and scope limitations;
- reproducible findings with severity, affected component, evidence, and remediation;
- retest results tied to fix commits/artifact hashes; and
- final list of unresolved findings and residual risks.
