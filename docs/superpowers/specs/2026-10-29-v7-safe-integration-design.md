# JoltIQ V7 safe integration design

**Date:** 2026-10-29  
**Source reviewed:** `/Users/johnw/Downloads/joltiq-git-v7.zip`  
**Status:** Proposed for owner approval

## Intent

Use the useful, tested work in V7 without replacing or regressing the newer JoltIQ branch. The existing repository remains the product of record because it contains direct-device help, guided assistance, remote support, the Windows work, and the new organization OIDC/Entra implementation that V7 does not contain.

V7 is treated as untrusted source material and test evidence. Its additions are ported into the current architecture with red-green tests, forward-only migrations, and a full regression gate. No existing feature, customer record, device enrollment, procedure, or migration is deleted.

## What V7 contributes

V7 contains useful additions that are not yet present on the current branch:

- stronger independent verification and rollback state;
- browser and device rollback orchestration;
- approval-request reminders and notification preferences;
- Slack and Microsoft Teams approval alerts with labelled local mocks;
- redesigned approval, run, queue, knowledge, integrations, and settings screens;
- a development-only headless companion and local demo fixtures;
- expanded Playwright coverage and operational documentation.

These capabilities should be integrated, not copied wholesale.

## Current evidence for V7

The archive is structurally valid and `docker compose config` succeeds. Validation was run in an isolated temporary directory with a real PostgreSQL database using RLS and Redis.

- API: 253 tests collected; 250 passed and 3 were intentionally skipped.
- API lint: Ruff passed.
- API typing: failed with 20 MyPy errors across computer use, Slack, approval notifications, admin settings, verification, rollback, and agent orchestration.
- Web: `npm ci`, TypeScript, production build, and CSP configuration check passed; 39 routes were generated.
- Production-mode Playwright: 8 passed, 10 skipped because the approval/mock credentials and seeded pending approval were not supplied, 0 failed.
- Development-mode Playwright currently conflicts with the production CSP because React development mode needs `eval()`; the documented production-mode scenario is clean, but the default test harness should be corrected so it cannot report false failures.
- Dependency audit: one high-severity production advisory in `zod < 4.6.6`; the available fix crosses a major version and must be upgraded with regression testing rather than forced blindly.
- Live Slack, Teams, Freshdesk, AI-provider, and Windows acceptance were not proved by this local run.

## Merge strategy

### 1. Keep the current branch canonical

Do not replace the current tree with V7. Preserve all current commits and schemas, including OIDC migration `0009_oidc_sso.py` and its security/login UI.

### 2. Rebase V7 migrations forward

V7 also starts at migration `0009`, so its migrations cannot be copied with their original numbers. Port them after the current migration head in dependency order:

1. verification/rollback/lease;
2. rollback availability;
3. approval notifications;
4. Slack/Teams dispatch state.

Each migration must be tested as an upgrade from a database containing existing users, devices, integrations, sessions, tickets, Freshdesk rows, and OIDC records. Existing rows and defaults must survive.

### 3. Port behavior in bounded increments

Implement and verify in this order:

1. verification and rollback models/services/API;
2. browser/device rollback execution and UI evidence;
3. approval reminders and notification preferences;
4. Slack and Teams connectors plus labelled development mocks;
5. headless companion as a development-only fixture;
6. UI components and page redesigns, merged with the existing SSO/admin-security navigation rather than replacing it;
7. Freshdesk mock pieces that strengthen the already-approved Freshdesk hardening plan.

Each increment starts from V7 tests, adapts them to the current models, observes the expected failure, implements the smallest compatible behavior, and commits only after focused and regression tests pass.

### 4. Fix V7 quality gates during the port

- Resolve all new MyPy errors instead of weakening MyPy settings.
- Upgrade or otherwise remediate the Zod advisory with typecheck, build, Playwright, and UI regression evidence.
- Make Playwright use the intended external production server when `SMOKE_BASE_URL` is set; its fallback development server must use a development-compatible CSP or avoid treating the known React development warning as a production failure.
- Run the previously skipped approval, Mailpit, Slack, and Teams scenarios with seeded data and labelled mocks.
- Keep local fixture providers and mocks disabled in production.

## User experience to preserve

The customer can open JoltIQ directly or arrive through a ticket. JoltIQ responds with a specific, empathetic understanding of the problem. The customer chooses **Guide me**, **Fix it for me**, or a human technician. Guide mode has no input authority. Fix mode requires exact-goal consent and step approval, observes the current state, performs bounded actions, independently verifies the result, rolls back when possible, and reports any partial change before escalation.

The V7 UI may improve clarity, but it must not remove the current direct-device flow, organization SSO, remote-support workspace, Windows consent, STOP control, audit trail, or customer-confirmed-versus-independently-verified distinction.

## Non-goals

- No wholesale ZIP overlay or repository replacement.
- No renumbering or rewriting migrations already committed or applied.
- No claim that V7, Windows, live AI, Freshdesk, Slack, Teams, Entra, or the product as a whole is production-ready without the corresponding live/VM/security evidence.
- No automatic enabling of external data egress, local test models, mock services, or headless fixtures in production.
- No removal of password login, MFA, break-glass SSO recovery, existing remote support, or Windows companion work.

## Definition of done

The integration is complete only when:

1. all ported V7 behavior has focused red-green tests on the current branch;
2. the full API suite passes with PostgreSQL RLS and Redis;
3. Ruff and MyPy both pass for the entire API tree;
4. web typecheck, production build, dependency audit policy, and all non-live Playwright scenarios pass;
5. disabled connectors produce no external calls;
6. the browser worker, direct-device-help, OIDC, remote-support, Windows protocol/core, and migration-upgrade regression gates remain green;
7. mock results are labelled as mock, live-service blockers remain explicit, and release documentation states exactly what was and was not tested.

Windows disposable-VM acceptance, Authenticode signing, installer lifecycle evidence, live connector credentials, and an independent security review remain external release gates; they are not faked or silently waived by this integration.

## Builder calls

- The current repository is canonical because it contains newer security and direct-device work than V7.
- V7 is integrated in small feature slices, not as one large merge, because its migrations and shared files conflict with current work.
- Production-mode browser evidence is authoritative for the CSP; the development harness still needs repair so local testing is reliable.
- The Zod advisory is handled as a deliberate dependency migration, not with `npm audit fix --force`.
