# JoltIQ Support Platform Design

## Intent

JoltIQ is an AI support worker for ordinary businesses, not only MSPs. A customer describes a problem in plain language and chooses either **Guide me** or **Fix it for me**. JoltIQ should diagnose from evidence, explain what it found, suggest a safe fix, obtain the required consent, act through the enrolled companion, verify the result, and escalate honestly when it cannot prove success.

MeshCentral is an implementation detail used only for optional human remote support. Customers and technicians should enter remote support through a modern JoltIQ workspace rather than the generic MeshCentral administration interface.

## Product boundaries

- JoltIQ owns tickets, identity, permissions, consent, AI reasoning, approved actions, verification, audit history, and customer communication.
- MeshCentral supplies permission-based screen transport for a human technician. It is not JoltIQ's AI and is not an alternate automation path.
- The Windows and macOS companions execute bounded local actions. Browser automation uses an isolated or explicitly shared browser surface.
- Protected operating-system prompts remain under the person's control and cannot be bypassed.
- Learned fixes and uploaded knowledge may improve retrieval and suggestions, but never become executable merely because they were learned.

## Customer experience

The primary interface uses the existing JoltIQ Live Core visual language: deep indigo, ion cyan for observation, volt yellow for active work, verify green for proven outcomes, and stop red for intervention. Remote support opens inside a dedicated JoltIQ workspace showing the computer, session reason, consent state, a persistent Stop/End control, and an audit timeline. Provider names, node IDs, and MeshCentral navigation are hidden from ordinary customers.

The remote canvas is visually subordinate to the safety state. The memorable element is a narrow live-status rail that changes from **Waiting for customer** to **Connected** to **Ended**, with plain-language evidence beside it. The layout is left aligned, restrained, responsive, keyboard accessible, and respects reduced motion.

## Expert knowledge

Knowledge is organized as versioned, tenant-owned packs rather than one claim that the model “knows everything.” Initial domains are:

- Windows and macOS endpoints
- Networking, DNS, Wi-Fi, VPN, firewalls, and identity
- Microsoft 365, Google Workspace, and common SaaS applications
- AWS and cloud operations
- Web applications, APIs, databases, and coding support
- Organization-specific policies, runbooks, architecture, assets, and past verified fixes

Every suggestion carries provenance, applicability checks, confidence, risk, and a verification method. Vendor documentation and tenant runbooks outrank remembered fixes. Conflicting or stale sources are surfaced, not silently merged. Tenant content remains tenant-scoped and is never used to train a shared model.

## Suggest-and-fix behavior

JoltIQ first summarizes the issue and evidence, then presents one of four outcomes: a cited suggestion, a guided procedure, an approved automated repair, or escalation. Suggestions are allowed to be broad; execution is narrow. An executable action must exist in the signed action registry or a separately published procedure, satisfy policy, obtain consent appropriate to its risk, and pass independent verification.

## Delivery tracks and finish line

1. **Support Workspace:** JoltIQ-branded remote session page, hidden provider details, visible consent/session state, audit lifecycle, and reliable prompt foregrounding.
2. **Expert Knowledge:** source connectors, domain packs, freshness/provenance, tenant retrieval, evaluations, and admin controls.
3. **Suggest and Fix:** evidence-based suggestions, applicability checks, approval mapping, action execution, verification, rollback/escalation, and outcome reporting.
4. **Release:** signed installers, Windows 10/11 and supported macOS acceptance matrices, upgrade/uninstall tests, observability, backup/recovery, security review, and operator documentation.

JoltIQ is customer-ready only when the supported workflows pass their physical-device acceptance cases and the interface never claims a fix without verification.
