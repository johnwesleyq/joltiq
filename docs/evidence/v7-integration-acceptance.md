# V7 safe-integration acceptance evidence

**Date:** 2026-10-29

**Canonical branch:** `feat/direct-device-help`

**Integrated source commit:** `68da9a5` (UI checkpoint `3fb6ddc`)

**Reference archive:** `/Users/johnw/Downloads/joltiq-git-v7.zip` was treated as untrusted reference material; current JoltIQ remained canonical.

## What is locally proven

- Forward-only migrations `0010`–`0013` follow OIDC migration `0009` and preserve tenant/RLS behavior.
- Verification evidence is hash-sealed; rollback is registry-derived and desktop rollback restores only from the companion's own journal reference.
- Browser/device authority is bound to monotonic lease epochs; stale, missing-after-establishment, and future epochs fail closed.
- Approval requests, reminders, outcomes, preferences, race dedupe, Slack, and Teams behavior pass local contract tests.
- Optional connectors are disabled by default. Registration, ticket, approval, device-enrollment, and OIDC-discovery paths produce zero Slack, Teams, Freshdesk, or browser/headless endpoint calls when unset.
- The UI retains work-email SSO, organization security, direct Guide/Fix controls, and remote-support workspace while adding approval-alert settings, exact approval/undo information, confirmation, and verification/rollback run evidence.
- Production CSP remains free of `unsafe-eval`; only the fallback development server receives React's development allowance. An external `SMOKE_BASE_URL` disables that fallback server.

## Commands and results

```text
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
Result: 289 passed, 3 skipped, 0 failed (292 collected)

cd apps/api && .venv/bin/ruff check joltiq tests
Result: All checks passed

cd apps/api && .venv/bin/mypy joltiq
Result: Success, 88 source files

cd apps/web && npm run typecheck && npm run build && npm run test:config
Result: passed; 37 application routes generated

cd apps/web && npm audit --omit=dev --audit-level=high
Result: 0 vulnerabilities

cd apps/web && npx playwright test
Result: 9 passed, 7 skipped, 0 failed

cd services/browser-worker && PYTHONPATH=. ../../apps/api/.venv/bin/pytest -q
Result: 16 passed, 0 failed

docker compose config --quiet
Result: passed
```

The seven browser skips are deliberate credential/environment gates for the production-shaped smoke/flow, Mailpit approval-email, MOCK Slack, and MOCK Teams suites. The self-contained SSO, remote-support, approval, rollback-evidence, and role-navigation tests passed.

## Not proven; still release-blocking

- **Seeded local Docker acceptance:** not run. Existing Docker Desktop services report unhealthy, the macOS Data volume is 98% full, and earlier rebuild attempts failed with containerd/iptables I/O errors. Reclaim space, rebuild the stack, seed the approval, and run the saved `SMOKE_BASE_URL` suites before claiming this gate.
- **Headless C# runtime:** the source/Compose contract tests pass, but this Mac has no `dotnet` executable. No Task 8 .NET runtime result is claimed.
- **Windows public release:** disposable Windows 10/11 acceptance, signed/timestamped MSI, install/repair/upgrade/rollback/uninstall evidence, and licensed classic-Outlook acceptance remain required.
- **Live services:** OpenAI/Anthropic, Entra, Freshdesk, Slack, Teams, Stripe, SMTP provider, and research-provider tests require real credentials and are not proven by local fixtures.
- **Security/compliance:** no independent penetration test or SOC 2 audit has been completed. The controls are designed to support a future audit; they are not a compliance certification.

No OIDC, direct-device help, Guide/Fix, MeshCentral remote-support, browser-worker, Windows companion, procedure, policy, ticket, knowledge, or audit feature was removed by this integration.
