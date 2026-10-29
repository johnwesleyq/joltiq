# JoltIQ AI Technician Product Design

## Purpose

JoltIQ is a private AI support worker for ordinary companies. An employee asks a question in plain language from the JoltIQ tray application, web portal, chat integration, ticket, or API. JoltIQ either answers from the company's knowledge, guides the employee through the solution, fixes the problem with approval, or hands a complete evidence package to a person.

The product promise is deliberately simple:

> Ask JoltIQ an IT question or tell it what is broken. It understands the company's environment, helps or fixes it, and proves what happened.

JoltIQ is not limited to MSPs and is not limited to tickets. Tickets are one intake channel. The primary direct experience is an employee opening **Ask JoltIQ** and describing the problem.

## Product shape

JoltIQ is one product with two connected capabilities.

### 1. The company's private support brain

An organization can upload or connect its help articles, policies, runbooks, application documentation, architecture notes, asset context, and past tickets. JoltIQ indexes this material inside that organization's tenant boundary and uses it to answer questions with citations.

Examples:

- “How do I request Salesforce access?”
- “What is our VPN setup process?”
- “Why does our internal application show error 403?”
- “Which AWS role should this service use?”

The organization owns this knowledge. It is exportable, permission-filtered, auditable, and never used to train a shared model or answer another organization's questions.

### 2. The company's AI technician

When an answer requires work on a computer, browser, or supported cloud/SaaS system, the employee chooses one of two modes:

- **Guide me:** JoltIQ observes only what the employee permits and gives one clear step at a time. It can point out where to click, but it has no input authority.
- **Fix it for me:** JoltIQ diagnoses, states the proposed change and expected result, receives exact-goal approval, performs bounded actions through an enrolled companion or approved integration, verifies the result, and reports the evidence.

If JoltIQ cannot establish a safe diagnosis, lacks authority, encounters conflicting evidence, or cannot verify success, it stops and escalates. The human receives the original request, device and application context, evidence, sources, approvals, actions attempted, results, and any remaining or rolled-back changes.

## The core experience

An employee types:

> My computer is slow. Fix it.

JoltIQ:

1. Acknowledges the specific symptom in plain language.
2. Requests permission for safe read-only diagnostics.
3. Measures relevant facts such as memory pressure, disk use, startup load, application resource use, update state, and recent failures.
4. Explains the cause it can support with evidence.
5. Presents a bounded repair plan: exactly what will change, what will not be touched, the risk, expected benefit, rollback limits, and required restarts.
6. Requests approval for the exact plan. A changed plan requires new approval.
7. Performs the approved actions while showing visible progress and a persistent Stop control.
8. Repeats the relevant measurements and checks the original symptom.
9. Reports before/after evidence, storage or memory recovered, settings changed, items preserved, and whether the outcome is independently verified, customer-confirmed, partially applied, or unverified.

This same observe → diagnose → propose → approve → act → verify loop applies to supported Windows, macOS, browser, SaaS, cloud, networking, and developer-support workflows. Different platforms use different diagnostics and procedures; JoltIQ never pretends that one platform's fix applies to another.

## Knowledge and truth rules

JoltIQ does not treat every retrieved sentence as equally trustworthy. It uses the following precedence:

1. **Live verified evidence** determines the current state of the specific device, account, or service.
2. **Published organization policy and approved procedures** determine how that organization wants work performed.
3. **Current authoritative vendor documentation** determines product behavior and supported technical facts.
4. **Organization articles and previously verified fixes** provide context and candidate solutions.
5. **Past tickets and model knowledge** provide hypotheses, never automatic authority to change a system.

Company policy wins for company-specific choices, but it cannot make a false technical claim true. If company documentation conflicts with live evidence or authoritative documentation, JoltIQ explains the conflict, avoids the disputed change, and routes it for review.

Every substantive answer should distinguish:

- what JoltIQ observed;
- which organization or vendor sources support the answer;
- what JoltIQ is inferring;
- how confident it is;
- what the employee should do next.

When reliable sources are unavailable, JoltIQ says so. It does not invent citations, actions, diagnoses, or verification.

## Ingestion and organizational learning

Administrators can add knowledge through file upload, article ZIP, ticket exports, approved URLs, API, and supported helpdesk/document connectors. Initial supported content includes PDF, DOCX, spreadsheets, CSV/JSON tickets, Markdown, text, HTML, and transcripts.

Ingestion must:

- retain source, version, owner, visibility, timestamps, and review status;
- remove or restrict secrets and sensitive personal data;
- preserve JoltIQ document visibility and role permissions; source-native per-user ACL synchronization is a later connector capability and must not be marketed in the pilot;
- detect duplicate, stale, and conflicting content;
- show indexing failures instead of silently omitting material;
- allow reindex, versioning, archive, delete, and export.

Past tickets are evidence of what happened, not automatically correct instructions. JoltIQ can use resolved tickets to improve retrieval and draft suggested articles. A successful verified repair may also produce a draft article containing symptoms, applicability, diagnosis, actions, verification, and rollback notes. A designated reviewer must approve the draft before it becomes trusted organization knowledge. Executable procedures require the existing stronger publication and integrity controls; ordinary articles never become executable merely because the model learned them.

## Answer behavior

For every new request, JoltIQ chooses the smallest useful response:

- answer directly from permitted company knowledge with sources;
- ask one necessary clarifying question;
- give a guided procedure;
- offer a consented diagnostic;
- offer an approved automated repair;
- escalate with a useful evidence package.

The first response must address the actual request, apologize when appropriate, summarize what JoltIQ understands, and say what it will check or what it needs next. It must not send a generic “ticket received” message.

JoltIQ should answer general IT questions using approved models and authoritative knowledge even when the company has no matching article. Company-specific answers must not be guessed. Access to an article or ticket is always filtered using the requesting person's identity and role before retrieval, before model input, and before citation display.

## Execution boundaries

The model may reason broadly, but authority to act is narrow.

- Ordinary Ask is chat-only and cannot control a computer.
- Guide mode cannot send input actions.
- Fix mode requires an enrolled device or approved integration, exact-goal local consent, policy authorization, and any required step approval.
- Screen content, articles, tickets, websites, and model output are untrusted input and cannot grant new authority.
- Safe read-only diagnostics may be grouped under one clearly described diagnostic approval.
- Mutating actions must come from the signed action registry, a fingerprinted published procedure, or an explicitly supported and policy-checked computer-use action.
- Credentials, MFA, secure-desktop prompts, purchases, destructive changes, and other consequential boundaries remain under the person's control.
- The user always sees when observation or control is active and can stop it immediately.
- Duplicate commands, expired commands, lost connections, retries, and restarts must not cause an action to execute twice.

Knowledge retrieval and computer control remain separate gates. A good answer can suggest a fix; it cannot authorize one.

## Verification and reporting

JoltIQ never marks its own work resolved merely because an action returned success. It checks the changed state and, when possible, the original symptom.

Outcomes are explicitly separated:

- **Answered:** a cited answer was supplied; no change was made.
- **Guided — customer confirmed:** the employee says the guidance fixed the issue.
- **Fix applied — verification pending:** the change occurred, but the symptom is not yet proven resolved.
- **Resolved — independently verified:** state and symptom checks passed.
- **Partially applied:** some changes occurred and are itemized.
- **Rolled back:** the original state was restored and verified.
- **Escalated:** JoltIQ stopped and provided evidence to a person.

The final report uses the clarity of the supplied Codex example: time spent, measured before/after results, exact work performed, exact items preserved, remaining risk, and a clear question before any additional consequential action.

## Interfaces

### Employee

- Windows and macOS tray/menu application with **Ask JoltIQ**, **Guide me**, **Fix it for me**, visible state, sources, results, and Stop.
- Web portal for the same conversation, approvals, device status, and history.
- Optional Slack, Teams, email, ticketing, and embedded/API entry points.

### Organization administrator

- Knowledge sources, indexing health, conflicts, visibility, review, and article approval.
- Supported devices and integrations.
- Model routing, data-boundary approval, budgets, and usage.
- Policy expressed in plain language controls rather than raw JSON.
- Audit, verification evidence, retention, licensing, and security settings.

### Technician

- Escalation queue ordered by urgency and impact.
- Concise diagnosis, evidence, attempted work, current device state, and recommended next action.
- Customer-approved remote support when automation is insufficient.
- Ability to turn a verified solution into a reviewed organization article or executable procedure.

### API customer

Businesses can embed Ask, Guide, Fix, status, approvals, and result retrieval through tenant-scoped APIs and webhooks. API use is metered, but it uses the same identity, knowledge, consent, policy, execution, and verification gates as the first-party interfaces. The API is not a bypass around local consent or organization policy.

## Deployment, ownership, and business model

JoltIQ can be sold as managed SaaS, customer-hosted/on-premises, or API usage. A customer-hosted deployment can keep model inference, screenshots, tickets, and knowledge inside the customer's approved network boundary. JoltIQ supplies signed feature updates; the customer retains ownership of its knowledge and operational data.

Endpoint Fix/Guide capability consumes a licensed device seat. Knowledge-only users and API traffic may be licensed separately. Device identity must use a durable cryptographic enrollment identity rather than relying only on a MAC address, which is mutable and may be duplicated. License enforcement can bind an entitlement to an enrolled device identity while allowing an audited administrator replacement workflow for legitimate hardware changes.

## Existing system and implementation direction

This design consolidates the product already in the repository; it does not authorize a rewrite. The existing tenant/RLS model, knowledge ingestion, ticket imports, model router, Ask chat, Guide/Fix controls, policy and approval engine, signed device protocol, Windows companion, browser worker, verification, audit, learned-fix drafting, MeshCentral integration, billing, and administration surfaces are retained.

The implementation should remove gaps between those pieces and present one coherent experience. MeshCentral and model-provider names remain implementation details. The product must not expose a collection of disconnected demos or require an ordinary employee to understand sessions, runs, procedures, providers, or remote-control infrastructure.

## Finite delivery scope

The next customer-testable milestone is not “support everything.” It is a coherent Windows pilot that proves the product contract using the current stack.

### Pilot workflows

1. **Organization answer:** import a synthetic company article and resolved ticket; ask a company-specific question; receive a permission-filtered answer with clickable citations.
2. **Direct general answer:** ask a general Wi-Fi, Microsoft 365, AWS, or Python question; receive a useful answer that distinguishes general guidance from company policy.
3. **Guide mode:** ask for help with a Windows problem; receive one step at a time; Guide has no input authority; customer confirmation closes the request accurately.
4. **Slow-computer diagnostic and safe cleanup:** diagnose low disk space and memory pressure, propose only safe bounded cleanup/settings actions, obtain approval, apply them, measure before/after state, and report what was preserved.
5. **Browser/SaaS workflow:** complete one approved change in the isolated FieldDesk fixture, verify the persisted result, and roll back/escalate on forced verification failure.
6. **Honest escalation:** unsupported, conflicting, denied, expired, offline, stopped, or unverified work ends with a useful evidence package rather than a false success.

### Exact pilot calls

The first commercial pilot is a customer-hosted Windows pilot on the customer's approved network. It may use an approved cloud model only when the tenant administrator has explicitly enabled that provider and its data boundary; otherwise it uses a validated customer-controlled model. Managed SaaS remains supported by the architecture but is not the first release target.

The supported endpoint for this pilot is Windows 11 64-bit with the versioned JoltIQ companion produced by the pilot build. Windows 10 and macOS are not advertised as pilot-supported until their separate existing acceptance requirements pass.

The slow-computer workflow is deliberately bounded. Its diagnostic catalog is:

- operating-system version, uptime, and restart-pending state;
- system drive capacity and free space;
- current memory pressure, committed memory, and top resource-consuming user applications;
- configured startup applications and their measured impact when Windows exposes it;
- inventory sizes for the exact cleanup categories below;
- recent relevant application or resource-exhaustion events when the event source is readable.

Its automatic cleanup catalog contains only categories implemented as locally allowlisted typed actions:

- current-user and Windows temporary files older than seven days, excluding locked or in-use files;
- application crash dumps and ordinary diagnostic logs older than thirty days;
- npm, pnpm, pip, node-gyp, Playwright browser-download, Next.js/SWC, and updater caches that can be reconstructed;
- project-generated `node_modules`, `.next`, `.turbo`, `coverage`, and Python virtual-environment directories only when they are beneath an administrator-configured development root, have a recognized sibling project manifest, are not used by a running process, and are selected explicitly in the approved plan.

It always excludes source files, Git metadata, documents, Downloads, browser profiles, credentials, Trash/Recycle Bin, complete projects, unknown directories, active application caches, Docker/Colima data, databases, virtual-machine disks, and any path outside the local allowlist. The scan produces an immutable inventory token; the apply action may delete only items present in that inventory and selected in the approval. Cache/build-output deletion is reported as non-rollbackable and may require a later redownload or rebuild. JoltIQ measures the categories and free disk space again after cleanup. Memory-only findings may produce guidance or a restart recommendation, but the pilot does not silently terminate applications or rewrite browser settings.

The browser workflow uses the existing FieldDesk notification-settings case: set the approved dispatch email, enable job notifications, save, reload, and verify both persisted values. The forced-failure case uses FieldDesk's buggy-save mode and must restore the captured prior values or explicitly report that rollback failed before escalation.

Employee-facing outcome wording maps to the existing durable states as follows: chat-only completion is **Answered**; guided completion is **Guided — customer confirmed**; an applied change awaiting a symptom check is **Fix applied — verification pending**; successful independent verification is **Resolved — independently verified**; retained partial state is **Partially applied**; successful restoration is **Rolled back**; and every stopped, refused, expired, unsupported, or unverified terminal path is **Escalated** with its reason. The same state and evidence appear in the Ask window and audit timeline.

Knowledge acceptance uses deterministic seeded company and vendor sources for automated tests. Each answer shows inline citation markers and a source drawer containing title, owner/source, version, publication date, freshness warning, and the permitted excerpt. Conflicts receive a visible **Sources disagree** label and do not become executable. Live model/provider evaluations are recorded separately and never substitute for deterministic authorization tests.

### Pilot acceptance

The milestone is complete only when:

- each workflow passes three consecutive times from a reset Windows 11 pilot snapshot through the customer-facing UI, not only through unit tests, and every listed denial, timeout, offline, duplicate-delivery, Stop, partial-failure, and forced-verification-failure case passes at least once;
- the physical Windows companion displays every required consent prompt and result in the Ask window;
- Ask status cannot remain stuck after consent timeout, denial, completion, or escalation;
- before/after evidence is visible to the employee and recorded in the audit timeline;
- company knowledge citations open the permitted source and never cross tenant or document permissions;
- no remembered fix, ticket, article, website, or model response can directly create executable authority;
- retries, disconnects, duplicate delivery, Stop, and partial failure produce the declared safe outcomes;
- a signed pilot installer completes install, enrollment, upgrade, repair, and uninstall on the supported Windows version;
- the existing Windows general-computer-use and installer acceptance runbooks are extended for this bounded pilot rather than replaced, and their required matrix plus the independent security review are recorded honestly.

macOS native control follows as a separate platform milestone using the same product contract and its own native implementation and acceptance evidence. Broad SaaS, cloud, networking, and coding coverage expands through additional knowledge/evaluation packs, connectors, diagnostics, and approved actions after the pilot contract is stable.

## Non-goals for the pilot

- Claiming JoltIQ can safely fix every technology or every problem.
- Unrestricted shell, PowerShell, registry, package, cloud-console, or browser control generated directly by a model.
- Blind self-training from tickets, chats, or model output.
- Replacing authoritative vendor tools where a supported API or management platform is the safer execution path.
- Hiding uncertainty, partial changes, verification failure, or human involvement.
- Finishing macOS native control inside the Windows pilot.

## Product finish line

JoltIQ has a stable product—not merely a technology demonstration—when a company can:

1. create a tenant and connect its identity system;
2. import its knowledge and ticket history with permission boundaries;
3. enroll supported computers;
4. let employees ask questions directly or through existing support channels;
5. receive cited company-specific and general technical answers;
6. choose Guide or Fix and understand the requested authority;
7. watch approved work, stop it, and receive trustworthy verification;
8. escalate with complete context;
9. administer policy, models, usage, licenses, retention, and updates;
10. install, upgrade, recover, audit, and remove the product using documented production procedures.

New domains and actions then become versioned capability packs, not a reason to redesign the core product.

## Builder decisions

The builder may choose internal sequencing, component boundaries, schemas, UI implementation details, and test factoring as long as existing safety boundaries and customer data are preserved. The implementation plan must start with a current-state gap map, reuse existing components, and make the six pilot workflows the only functional expansion required for the next milestone.
