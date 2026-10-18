# Implementation plan

Milestones, their dependencies and acceptance gates. ✅ = gate met in this environment, ⚠ = met except for an external blocker, ☐ = open.

| # | Milestone | Depends on | Acceptance gate | State |
|---|---|---|---|---|
| 1 | Repo, infrastructure, schemas, docs, CI | — | Migrations apply. Compose config valid. CI defined. | ✅ (image builds blocked: registry) |
| 2 | Auth, tenancy, roles, UI | 1 | Auth tests and RLS cross-tenant tests pass. All UI pages render in real Chromium. | ✅ (OIDC ☐) |
| 3 | Tickets, knowledge, citations | 2 | Lifecycle/SLA/search tests. Ingest → hybrid search with citations and permission filter. | ✅ |
| 4 | Orchestrator, typed plans, escalation | 3 | State machine drives the E2E. Invalid or unverifiable plans escalate. | ✅ |
| 5 | Live research | 4 | Redaction, allowlist, injection screening, evidence vs hypothesis. | ⚠ fixture only (no search key) |
| 6 | Browser automation, viewer, takeover | 4, 8 | Real Chromium E2E: consent → approval → sign-in handoff → VERIFIED. | ✅ (upload action ☐, AI-vision fallback ☐) |
| 7 | Windows companion | 4, 8 | Protocol attack tests and .NET self-tests. Desktop E2E via real protocol code. | ⚠ Windows runtime and MSI blocked |
| 8 | Policy, approvals, leases, verifier | 2 | Bypass, tamper, lease and verification-failure tests. | ✅ |
| 9 | Integrations, email, webhooks, notifications | 3 | Freshdesk (fake transport), SMTP, signed webhooks with retry, email intake. | ⚠ live Freshdesk untested |
| 10 | Billing, usage, entitlements, branding | 2 | Stripe-signed webhooks idempotent with retry. Entitlements enforced. | ⚠ checkout needs Stripe key |
| 11 | Analytics, observability | 4 | Dashboard, metrics, health, platform admin. | ✅ |
| 12 | Hardening, E2E, performance, deploy prep | all | Security suite, perf script, audits clean, k8s reference manifests. | ✅ in scope; external review ☐ |

Next tasks are listed in `PROJECT_STATUS.md` → "Where to resume".
