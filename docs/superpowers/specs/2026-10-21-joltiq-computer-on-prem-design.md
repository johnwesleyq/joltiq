# JoltIQ Computer On-Prem Product Design

**Date:** 2026-10-21
**Status:** Approved by owner
**Product direction:** JoltIQ is a customer-owned, application-agnostic AI support worker for ordinary companies, internal support teams, software vendors, service organizations, and MSPs. Its general computer-use engine works tickets across Windows, macOS, and approved browser/SaaS interfaces, including unfamiliar applications. Windows ships first because its companion is farther along; macOS follows through a separate native implementation and acceptance track. Outlook and Microsoft 365 are initial acceptance examples, not product boundaries.

## Intent and finish line

JoltIQ turns an incoming support ticket into one of three honest outcomes: a verified resolution, a documented partial result awaiting the customer, or an evidence-backed escalation to a person. It can either guide the customer through the work or, with explicit permission, operate the computer while the customer watches.

The Windows-first production release is finished when a customer can install the JoltIQ control node inside its own network, activate paid device-bound seats, enroll Windows endpoints, connect approved ticket sources, run a supported local model without sending customer data to a cloud AI provider, and complete real local-computer and browser/SaaS tickets through both customer modes with the security, verification, update, licensing, and operational acceptance gates in this document. The cross-platform program is finished when macOS provides the same product contract through its native permissions and acceptance track. Autonomous weight training and an unlimited catalog of privileged system mutations are not part of either finish line.

This is a fixed product boundary, not a promise that every possible ticket can be repaired. JoltIQ investigates every ticket. It acts when its current capabilities and policy allow it, and escalates the rest without guessing.

## Goals

- Serve ordinary companies as well as MSPs.
- Send a useful, ticket-specific initial response instead of a generic receipt.
- Offer **Guide me** and **Fix it for me** after diagnosis.
- Work across an enrolled Windows desktop and an isolated browser for approved SaaS domains.
- Use the same application-agnostic look/understand/act/observe loop on macOS after the Windows release rather than limiting support to named applications.
- Keep customer operational data and model inference inside the customer network.
- Improve local usefulness from customer-approved knowledge and verified fixes without uncontrolled self-training.
- Sell one active seat per computer JoltIQ may control, with connected and offline activation.
- Deliver vendor features and approved model packages as cryptographically signed, customer-controlled updates.
- Build controls suitable for a future SOC 2 examination without claiming certification before an independent audit.

## Non-goals and anti-goals

- JoltIQ does not silently control a computer, widen an approved goal, or hide its activity.
- A knowledge match, remembered fix, or model suggestion is not authority to make a privileged system change.
- The product does not send tickets, screenshots, documents, prompts, embeddings, learned fixes, credentials, or general telemetry to JoltIQ or an external model provider.
- The local model does not silently retrain its weights from customer content.
- A license failure does not encrypt, delete, or hold customer data hostage. It stops new automation while preserving export, audit, deactivation, and uninstall.
- MAC address alone is not a license identity.
- “SOC 2-ready” must not be marketed as “SOC 2 certified.” Certification requires the applicable controls, evidence period, and independent auditor opinion.
- The first release does not promise arbitrary kernel, firmware, security-policy, payment, or destructive actions.

## Customer experience

### Ticket-specific initial response

JoltIQ responds after it has read the ticket and available permitted context. The message must:

1. apologize for the specific disruption without using a generic canned apology;
2. restate the reported symptom and its practical impact in plain language;
3. identify what JoltIQ will inspect next without pretending it already knows the diagnosis;
4. explain that a safe confirmed fix may be guided or performed with permission;
5. promise an informed escalation when the evidence or capability is insufficient; and
6. communicate that the ticket matters without making an unsupported resolution or timing promise.

For the Outlook example, it should say that it understands Outlook closes when a PDF attachment is opened, that this interrupts the customer's work, and that it will check the Outlook version, preview component, and crash evidence. It must not say only “we received your ticket.”

The local model drafts this response. A deterministic ticket-specific fallback is used when inference is unavailable. The initial response never executes an action and never claims a diagnosis that has not passed the diagnostic gate.

The organization explicitly enables automatic initial responses and approves its tone/template during onboarding. Once enabled, the response is sent automatically as JoltIQ's first customer-visible ticket action; it does not wait in a technician approval queue. Schema validation refuses empty acknowledgment language, unsupported diagnosis claims, invented timing promises, confidential internal content, and unsafe instructions. An organization can disable automatic sending and use draft-only mode.

### Diagnosis and mode selection

JoltIQ gathers only the context permitted for the ticket. It presents what it believes the problem is, what it can safely do, what it cannot yet establish, and the exact customer/device/browser involved. If no safe path exists, it escalates immediately with collected evidence.

When a safe path exists, the customer chooses one of two modes:

#### Guide me

- The customer grants screen-observation consent for the named goal.
- JoltIQ explains one step at a time and overlays a visible highlight, pointer, or callout on the exact control.
- The customer performs every click and keystroke. Guide mode cannot issue input commands.
- JoltIQ waits, captures the next permitted observation, confirms the expected state, and then gives the next instruction.
- The customer can ask questions, stop, escalate, or switch to **Fix it for me**. Switching modes requires the fix plan and control consent before any input is sent.

#### Fix it for me

- JoltIQ displays the exact goal, plan, affected applications/domains, expected effects, reversibility, and required pauses.
- Portal consent and native on-device consent are both required.
- JoltIQ performs the approved work visibly while the customer watches.
- A persistent control indicator provides **Stop now**, **Take control**, **Ask a question**, and **Escalate**.
- Stopping prevents the next input promptly and reports any change already made. It never claims an in-progress side effect was undone unless rollback was separately verified.

Both modes use the same diagnostic, policy, audit, verification, and escalation system. Only the holder of the control lease may send input.

### Verification and closure

Clicks completed is not equivalent to problem resolved. Every executable procedure names independent postconditions, and symptom verification is separate from configuration verification. JoltIQ records `VERIFIED`, `FAILED`, `PARTIAL`, or `UNKNOWN`; `UNKNOWN` never closes a ticket. A customer may confirm the real symptom, but a procedure can require additional device, browser, database, event-log, or HTTP evidence. Failed or unavailable verification keeps the ticket open or escalates it with the last confirmed state.

## Execution surfaces

### Windows endpoint

The existing signed companion protocol, replay protection, local allowlist, durable command journal, tray consent, STOP behavior, and tray/broker privilege separation remain authoritative. The missing production component is a Windows screen/input adapter that:

- captures only the consented interactive desktop;
- maps model coordinates to the exact captured frame and display layout;
- exposes a guide-only overlay path that cannot inject input;
- re-checks active consent, control lease, session expiry, logged-in user, screen state, device license, stop state, action type, and parameters before every command;
- refuses secure desktop, lock screen, password/OTP/card fields, prohibited key combinations, and unapproved elevation prompts;
- shows a persistent native control banner and local emergency stop;
- uploads the post-action frame and result through the existing signed protocol; and
- fails closed on display changes, ambiguous targeting, adapter restart, lost connection, duplicate commands, or stale frames.

Low-level screen control is for UI work inside the approved goal. Privileged, policy-sensitive, registry, service, installer, or other structured system mutations continue through typed allowlisted device actions and separately approved procedures. The general screen agent is not a shell and does not bypass the Windows Outlook procedure gate.

### General computer-use engine

The engine is application-agnostic. It combines the current screen image with available browser DOM/accessibility data or native accessibility metadata, the approved goal, the local policy, and recent observations. It proposes one bounded next action, observes the resulting state, and repeats. It does not require a bespoke connector or published procedure for ordinary visible UI navigation.

Named procedures and integrations improve speed, determinism, independent verification, and access to structured or privileged operations. They do not define the product's application compatibility. When JoltIQ encounters an unfamiliar app, it may inspect and operate its visible interface in Guide or Fix mode if the goal, consent, local policy, and action risk allow it. Ambiguous controls, inaccessible canvases, anti-automation barriers, secure input, insufficient verification, or unsupported privileged changes cause a pause, customer handoff, or escalation—not a fabricated click.

### Browser/SaaS

The isolated Playwright browser worker remains the default SaaS surface. Each session has separate storage, an approved domain allowlist enforced at the network layer, protected human input for login/MFA, masked sensitive fields, quarantined downloads, safe ticket-bound uploads, a control lease, and independent read-back.

JoltIQ may use an existing browser on the endpoint only through the consented Windows screen session. Secrets, MFA, CAPTCHA, payments, public posts/messages, destructive actions, permission changes, and software installation always pause for the customer or are denied by policy. Application-specific procedures may add stricter rules.

Microsoft 365 OneDrive/SharePoint is the first real public SaaS acceptance example because it exercises protected login/MFA, general navigation, customer-selected upload, STOP, control handoff, and independent file read-back. Passing that case does not restrict the engine to Microsoft products or claim universal SaaS compatibility. Salesforce, HubSpot, Google Workspace, Freshdesk, internal portals, and other web applications use the same general engine and receive their own compatibility evidence as they are validated.

### macOS endpoint

The macOS companion implements the same signed protocol, customer modes, control lease, STOP semantics, guide overlay, protected input, audit, and verification contract using native Screen Recording, Accessibility, and Input Monitoring permissions. It is separately signed and notarized and must handle multiple displays, Spaces, scaling, lock/user changes, secure input, permission revocation, stale frames, disconnects, and app restarts. Windows-specific procedures do not carry over. macOS becomes a supported release only after its native adapter, installer/update path, general-app cases, browser cases, and refusal matrix pass on supported macOS versions.

### Broad ticket handling

JoltIQ accepts and investigates every ticket it is authorized to read. It chooses among knowledge answer, guided local work, automatic local work, isolated browser work, mixed local/browser work, a customer question, or escalation. Unsupported platforms, insufficient evidence, unavailable local inference, unsafe actions, expired consent, and failed verification produce a clear escalation rather than fabricated steps.

The first release supports the existing built-in portal, inbound email, generic REST ticket ingestion, and Freshdesk synchronization. Other named service desks remain extension points until they pass their own authentication, threading, attachment, conflict, and live-tenant acceptance suites.

## Customer-owned deployment

### Topology

The default deployment is a customer-hosted control node on a dedicated Linux server or virtual machine inside the customer network. Containerized services include the web portal, API/worker, PostgreSQL, Redis, object storage, isolated browser worker, update verifier, license service client, and a local model gateway. The gateway connects only to a customer-controlled OpenAI-compatible inference runtime on the same host or private network. JoltIQ may ship supported local model bundles where model licensing permits; otherwise an administrator imports approved weights.

One production control node belongs to one customer security boundary. It may contain multiple departments/organizations owned by that customer, but the first release does not let an MSP commingle unrelated client data on one shared node. An MSP deploys a separate node and key hierarchy per client; shared multi-client hosting requires a later threat model and acceptance plan.

Each managed Windows PC runs the JoltIQ companion and consumes one endpoint seat. The control node itself does not consume an endpoint seat. A single-PC small-business package may co-locate the control services and local inference on that PC when it meets the validated hardware profile, but the production default is a separate control node so tickets continue to exist when an endpoint is offline.

Exact CPU, RAM, disk, and GPU profiles are release artifacts derived from benchmark acceptance. A GPU-backed profile is the production recommendation for interactive computer use. A smaller CPU-only profile may be supported with an explicit reduced-speed label; it may not silently fall back to an external model.

### Network boundary

No customer ticket, document, screenshot, prompt, response, embedding, learned fix, credential, audit event, or general product telemetry is sent to JoltIQ, an external AI provider, or any unrelated third party. Local inference makes no cloud model call. Customer content may cross the physical network perimeter only when the customer has configured an internet-hosted ticket, email, integration, or SaaS destination and the content transfer is the requested operation; those flows go directly between the customer deployment and that approved service.

Outbound network access is limited to:

- customer-approved SaaS domains during a customer-approved browser task;
- customer-configured ticket/email/integration endpoints needed for the product to do its work; and
- the JoltIQ licensing and signed-update endpoints only in connected mode.

Every destination is policy-controlled and auditable. Air-gapped mode has no JoltIQ internet dependency; administrators import signed activation and update bundles. Diagnostics exports are created locally, redacted, and leave the network only through an explicit administrator export.

## Local intelligence and improvement

Customer-approved uploads are parsed, redacted according to local policy, indexed, and embedded locally. Verified resolutions may create a learned-fix draft that is staff-only until an authorized reviewer publishes it. Published executable procedures retain the existing two-admin, MFA-backed, canonical-hash gate.

This improves JoltIQ through retrieval, ranking, organization-specific language, verified procedures, and locally evaluated prompts. Learned knowledge remains tenant-owned and exportable. It is never sent to JoltIQ or another customer.

Model weight changes are a separate controlled process. No production deployment performs continuous autonomous fine-tuning. A future local fine-tuning feature would require a versioned dataset, explicit administrator approval, evaluation against safety and regression suites, signed provenance, staged rollout, and one-click rollback. Until that feature is separately designed and accepted, “improves itself” means local retrieval and approved procedure improvement, not weight mutation.

## Device-bound commercial licensing

JoltIQ is licensed to a company for a purchased number of active managed-computer seats. Every endpoint on which JoltIQ can enter **Fix it for me** mode consumes one seat. Guide-only observation on an enrolled endpoint also consumes a seat because it uses the installed companion and support service. Server capacity, retention, integrations, high availability, and premium model bundles may distinguish editions without changing the endpoint identity rule.

On enrollment, the companion creates a non-exportable asymmetric device key in TPM-backed or OS-protected storage. The active seat is bound to the public key, customer, product edition, and an auxiliary hardware fingerprint. MAC addresses are hashed supporting signals only; they are not the primary identity because they are mutable and spoofable.

Connected mode receives a short-lived vendor-signed lease through the restricted licensing endpoint. Air-gapped mode imports a signed activation bundle with an explicit renewal period. Every controlled session verifies the local entitlement and device proof. Copying an activation to a different device key fails closed and produces an audit event. Clock rollback, cloned identity, invalid signature, expired grace period, revoked seat, and edition mismatch block new automation.

An authorized customer license administrator can deactivate and transfer a paid seat for hardware replacement. Transfer events are audited and rate-limited; suspicious rapid movement requires vendor-assisted review in connected mode or a new offline entitlement. License failure never deletes customer content: read-only administration, audit export, seat deactivation, backup, and uninstall remain available.

The licensing threat model prevents accidental reuse, ordinary image cloning, copied activation files, changed network adapters, replay, and endpoint-level tampering. It does not claim perfect enforcement against a determined customer infrastructure administrator who controls the hypervisor, database, binaries, and time source; contracts and anomaly review complement technical controls at that boundary. Connected leases last 7 days with a 7-day signed grace period. Offline entitlements last 90 days and warn administrators beginning 30 days before renewal. A seat may transfer three times in a rolling 30-day period before connected vendor review or a replacement offline entitlement is required.

## Signed product updates

JoltIQ supports two customer-selected modes:

1. **Connected updates:** the local updater fetches a signed manifest and package through an update-only endpoint. It transmits only the minimum licensing/product version data required for authorization, never operational content or general telemetry.
2. **Air-gapped updates:** an administrator downloads the signed bundle elsewhere, scans it according to company policy, and imports it locally.

Both paths verify a dedicated offline-rooted vendor signing chain, package digest, product/edition, compatible migration range, SBOM identity, and monotonic release version before staging. The administrator sees release notes, database migrations, model changes, required restart, and rollback limits before approval. Updates are staged, health-checked, auditable, and reversible when the declared migration contract permits. Feature code and approved model packages come from JoltIQ; customer knowledge remains separate and survives upgrades.

## Security and SOC 2 readiness

The product preserves the existing tenant isolation, forced PostgreSQL RLS, MFA, RBAC, signed approvals, append-only hash-chained audit, idempotency, redaction, policy registry, verifier independence, durable execution, and device/server signature model.

The production program additionally requires:

- secure installation defaults and unique secrets; encryption in transit and at rest;
- dual-control release signing with protected keys and timestamped provenance;
- dependency/SBOM generation, vulnerability scanning, patch SLAs, and signed build evidence;
- least-privilege service accounts, local administrator separation, session expiry, and periodic access review;
- backup/restore testing, disaster recovery objectives, health monitoring, and customer-controlled retention;
- security event export, rate limiting, abuse/clone detection, and incident-response runbooks;
- change management linking approved design, tests, review, signed artifact, and release;
- penetration testing and independent review of desktop control, update, licensing, browser, and model boundaries; and
- documented shared responsibility for the customer’s endpoints, firewall allowlists, identity provider, backups, model weights, SaaS permissions, and physical environment.

These controls make the system suitable for a future SOC 2 audit. Marketing may say the product was designed for SOC 2-aligned controls only after counsel/security review; it may say SOC 2 certified only after the relevant independent report exists.

The first release supports Microsoft Entra ID through OIDC for ordinary connected companies plus local accounts with TOTP MFA for air-gapped use and audited break-glass recovery. The ticket requester authorizes the support goal in the portal; the interactive Windows user separately grants native local consent. They may be different people only when organization policy permits remote assistance and the endpoint user sees the requester/goal. A changed user, shared/RDP host, locked screen, or ambiguous interactive identity blocks control. Manager or IT approval may be added by policy for privileged categories.

Vendor-signed built-in procedures can be enabled by a single organization owner because their executable hash was reviewed and signed in the JoltIQ release process. Customer-authored executable procedures still require two distinct MFA-backed organization administrators. A one-person company can use built-in procedures, Guide mode, and non-executable knowledge; publishing a new executable local mutation requires adding a second approver or using a separately audited vendor procedure-review service.

## First-release service levels and scale

- Initial ticket-specific response: p95 within 60 seconds of ingestion when the local model is healthy; deterministic fallback within 10 seconds when it is not.
- Native STOP: prevent the next queued input within 500 ms p95 and 2 seconds maximum on a healthy endpoint; reconcile any already-started side effect separately.
- Screen observation/action round trip: p95 under 3 seconds on the validated local network, excluding the target application's own load time and explicit customer pauses.
- Control node baseline: 100 enrolled endpoints, 10 concurrent active computer/browser sessions, and 25 ticket-ingestion events per second without tenant or control-lease failures.
- Default operational retention: 90 days, customer-configurable from 30 to 365 days; legal hold and longer archival require an edition/policy extension.
- Baseline recovery objective: RPO 24 hours and RTO 4 hours with documented backup/restore validation. High-availability editions may define stronger targets.
- Pilot safety: no unapproved input, cross-customer disclosure, secret capture, duplicate side effect, or falsely verified resolution is an acceptable known failure.

## Failure behavior

- Local model unavailable: use the deterministic initial-response fallback, pause new diagnosis, and preserve/escalate the ticket.
- Screen adapter unavailable or permission revoked: send no input, end the lease, and explain how to resume.
- Customer chooses Guide me: input commands are structurally impossible for that session mode.
- Customer stops mid-step: prevent subsequent input, reconcile the current screen/device state, and report confirmed changes.
- Device or license identity changes: fail closed before control; do not consume or duplicate the action result.
- Browser leaves its allowlist or requires secrets/MFA/CAPTCHA: block or pause for protected customer input.
- Verification is missing or ambiguous: never close as verified.
- Connected licensing/update endpoint is unreachable: honor only the explicit signed grace period; never switch to cloud inference or bypass signature checks.
- Update fails: retain the previous runnable version or enter a documented recovery state without corrupting customer data.

## Acceptance boundary

The Windows-first production release is complete only when all of the following have repeatable evidence:

1. Ticket-specific first responses pass content and non-hallucination tests, including deterministic local fallback.
2. Guide mode highlights and advances through real Windows and browser tasks without ever injecting input.
3. Fix mode performs real consented Windows and browser tasks with visible control, STOP, take-over, protected input, exact pauses, and independent verification.
4. The existing Windows Outlook procedure passes its complete Windows 10/11 snapshot-reset matrix and licensed classic-Outlook case without weakening its procedure gate.
5. The Windows screen/input adapter passes multi-monitor, scaling, resolution-change, lock/unlock, user-switch, UAC/secure-desktop, stale-frame, disconnect, duplicate, stop-race, and restart-recovery cases.
6. Browser/SaaS acceptance passes against a real Microsoft 365 OneDrive/SharePoint test tenant for navigation, customer-selected upload, protected login/MFA, take-over, STOP, and server-side read-back. This is the initial application-agnostic engine proof, not an application allowlist.
7. Customer data egress tests prove that only approved SaaS/integration and optional license/update destinations are reachable.
8. A supported local model completes the ticket, guide, and fix evaluation suite on published hardware profiles without external model calls.
9. Connected and air-gapped activation prove one-seat/one-device enforcement, legitimate transfer, clone refusal, renewal/grace behavior, and non-destructive license failure.
10. Connected and offline update paths prove signature, digest, version, compatibility, migration, rollback/recovery, and customer-knowledge preservation.
11. Production installer/artifacts are signed and timestamped; CI, SBOM, vulnerability, backup/restore, observability, deployment, and upgrade evidence is retained.
12. Independent security review and penetration testing report no unresolved release-blocking finding.

Passing these gates makes the release ready for a limited paid Windows pilot. Pilot exit requires defined reliability, resolution, escalation, stop-response, and customer-satisfaction targets over the agreed observation period. macOS then receives its own native adapter, permissions, procedures, installer, signing/notarization, and acceptance plan rather than inheriting Windows claims.

The cross-platform product reaches general availability only after both the Windows and macOS tracks pass their platform matrices and the general engine proves unfamiliar-application behavior: complete safe goals when possible, offer Guide mode or customer takeover when interaction becomes ambiguous, and escalate with evidence rather than guess. “Works on everything” is the architectural intent and best-effort operating model; it is not a guarantee that every third-party interface or ticket can be completed.

## Implementation calls made by the builder

- Reuse the existing FastAPI/PostgreSQL state machine, action registry, approvals, verifier, isolated browser worker, Next.js portal, signed device protocol, and Windows companion instead of replacing them.
- Add a local-model provider/gateway and make cloud model adapters unavailable in the on-prem edition by policy and packaging.
- Implement Guide mode as a distinct server/session mode and native overlay protocol, not as a prompt asking the model to avoid clicking.
- Implement Windows screen/input control in the Windows companion and remove the unfinished Python desktop-agent path from the production package once its protocol behavior is covered.
- Treat initial responses as a first-class audited workflow stage with a typed schema and deterministic fallback.
- Introduce licensing and update trust roots distinct from session/command signing keys.
- Default to a separate Linux control node plus paid Windows endpoint seats; support a validated single-PC bundle as a packaging profile, not a different product architecture.
- Preserve existing strict published-procedure execution for structured local changes. General computer use cannot create an arbitrary registry, service, shell, installer, or policy operation.
