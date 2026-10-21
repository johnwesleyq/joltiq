# Project status — JoltIQ

**Last updated:** 2026-10-20 · **Task 12 environment:** macOS 26.6 (arm64), Python 3.12.8, Node 24.14.1, Docker 29.7.2, PostgreSQL/Redis in the local Compose network, Playwright Chromium 1194, and .NET 8 SDK in a Linux container. No Windows host and no Git remote.

**Overall:** The Windows Outlook PDF MVP is implemented and its portable security/core tests run, but it is **not ready for customers or a pilot**. Windows CI, Windows 10/11 VM acceptance, licensed classic-Outlook acceptance, installer lifecycle proof, Authenticode signing, and external security review have not happened. Browser/SaaS automation remains the product priority after this Windows internal-acceptance gate; secure `browser.upload` is the next implementation plan.

Status legend: **IT** = IMPLEMENTED_AND_TESTED · **INT** = IMPLEMENTED_NOT_TESTED · **P** = PARTIALLY_IMPLEMENTED · **B** = BLOCKED_EXTERNAL_DEPENDENCY · **NI** = NOT_IMPLEMENTED

## Feature status

| Area | Status | Notes / evidence |
|---|---|---|
| Registration, login, logout, sessions (idle + absolute expiry), CSRF, lockout, rate limits | IT | `test_auth.py` |
| Email verification, password reset (revokes sessions), invitations, roles, org switching | IT | `test_auth.py`, SMTP delivery via local aiosmtpd (`test_platform_features.py`) |
| TOTP MFA | IT | `test_auth.py::test_mfa_totp_enrollment_and_login` |
| OIDC / Microsoft Entra SSO | **NI** | Model column `oidc_subject` exists, but there's no login flow. Needs an Entra app registration. |
| Tenant isolation (app + PostgreSQL RLS), audit hash chain, append-only audit | IT | `test_tenancy_rls.py`, cross-tenant cases in tickets/devices/sessions/approvals tests |
| Native tickets: lifecycle, SLA, comments/internal notes, attachments, search, similar, idempotent create | IT | `test_tickets.py` |
| Knowledge: PDF/DOCX/XLSX/CSV/MD/TXT/HTML/transcripts, URL (SSRF-guarded), versions, hybrid search, citations, permission filtering, delete/reindex | IT (PDF/DOCX/XLSX parsers INT) | `test_platform_features.py::test_knowledge_ingest…`; PDF/DOCX/XLSX parsing code is covered only indirectly |
| Signal extraction (error codes, versions, symptoms, fixes) | IT (rules) | LLM enrichment NI |
| Article drafting after VERIFIED resolution → human review / approve / publish / archive / version; executable only via admin publish | IT | `test_e2e_browser.py` (draft), browser + desktop tests publish executable procedures |
| AI orchestrator state machine, durable pauses, retries, loop detection, escalation | IT | Browser/desktop E2E; denial/verification-failure/emergency-stop scenarios |
| Model adapters: OpenAI, Anthropic, Gemini (structured output), routing/fallback/budgets/costs | **INT (live)** / IT (contract) | Request/response contract tests and router fallback + cost tests pass (`test_model_adapters.py`, mocked HTTP). **No provider keys were available**, so they've never been called live. |
| Live web research (Brave, Tavily), redaction, allowlist, injection/command screening, evidence vs hypothesis | P → **INT (live)** / IT (fixture) | Pipeline tested against a local fixture search over a local test site (`live=false`). No search API key, so live search is untested. |
| Isolated browser worker: contexts, domain allowlist at network layer, actions, auth/MFA/CAPTCHA detection, masked screenshots, protected human input, pause, popups, downloads, read-back | IT | Real Chromium against an authorized local test app (`tests/e2e/testapp`) |
| Browser `upload` action | **P** | In the registry but not implemented in the worker; it returns "unsupported" |
| AI visual-navigation fallback, Stagehand, Browserbase adapter | **NI** | Deterministic selectors only |
| Live viewer, take control, hand back, emergency stop, single control lease (DB-enforced) | IT | `test_e2e_browser.py`, `test_security.py` |
| Policy engine (5 risk levels, tenant rules, consent scope) + approvals (step-hash bound, HMAC, single-use, expiry, supersede) | IT | `test_security.py`, `test_platform_features.py::test_policy…` |
| Independent verifier (browser read-back, device read-back, DB re-fetch, HTTP) | IT | Browser: VERIFIED + FAILED (app "saves" without persisting) both caught |
| Windows companion protocol (enroll, signed requests, nonce/counter replay protection, signed envelopes, revocation, durable journal/outbox) | IT (portable) / **INT (Windows)** | API protocol tests plus .NET Core self-test **20/20**. Native Windows runtime evidence is still absent. |
| Outlook PDF diagnosis, exact published plan, per-step approval, HKCU mutation, safe restart, rollback, and symptom verification | **INT / B** | Implemented with portable/server tests. `IMPLEMENTED_NOT_TESTED` on Windows; `BLOCKED_EXTERNAL_DEPENDENCY` for the required Windows VM matrix. |
| Windows OS providers, DPAPI journal, tray consent/banner/STOP, and tray/SYSTEM separation | **INT / B** | Source and Windows-only xUnit tests exist. They have not run on Windows 10/11. |
| Windows installer (WiX v5), lifecycle, signing, repair/upgrade/uninstall | **INT / B** | Build/release workflows and checks exist but have not run on `windows-latest`; no MSI artifact exists here and nothing is signed. Any local output is unsigned internal-only. |
| Companion authenticated auto-update | **P** | Heartbeat reports latest/required version. No signed downloader yet. |
| **Computer use (screen-driving agent)**: goal approval by the consenting customer, look→act loop, approval / question / “your turn to type” pauses, secret-typing refusal, step limit, budget, stop on session end, per-step screenshots + audit, live screen in the session page | IT (server, fake device + scripted fixture model) | `test_computer_use.py` (6 tests) through the real signed device protocol. Anthropic computer toolset adapter (`computer_toolset_20260801`, legacy `computer_20251124`) **INT (live)**: no key |
| Device screen/input layer for computer use | **NI (by design here)** | Not in this repo. Plug in an established remote-support SDK or an in-house adapter; contract in `apps/desktop-agent/README.md`. Protocol client + local policy for macOS/Windows in `apps/desktop-agent` |
| Bulk import of past tickets (CSV/JSON from Zendesk, Freshdesk, Intercom, HubSpot, Jira, spreadsheets) and help articles (ZIP) with PII redaction | IT | `test_computer_use.py::test_bulk_import…` |
| Memory: successful computer-use runs saved as “Learned fix” knowledge (redacted, staff-only by default) and recalled at the start of the next similar run; chat shares known fixes from history | IT | `test_computer_use.py::test_successful_run_is_remembered…`, `…test_chat_shares_known_fix…` |
| Desktop technician takeover (human driving the remote screen) | **NI** | Browser sessions only |
| Integrations: Freshdesk (pull/push/status, idempotency, conflict detection, retry, rotate, revoke) | IT (mocked) / **INT (live)** | Fake Freshdesk transport. No Freshdesk account available. |
| Generic REST inbound, outbound signed webhooks with retry, email-to-ticket | IT | Local webhook receiver, RFC 5322 messages |
| Zendesk, ServiceNow, Jira SM, HaloPSA, ConnectWise, Intune, NinjaOne, Datto, M365, Google, Slack, Teams | **NI** (documented extension points; API returns 501) | |
| Notifications: in-app, email, webhook, preferences, SSE real-time | IT (SSE: INT) | SSE endpoint built, but no automated test reads the stream |
| Stripe: checkout/portal, webhook signature, idempotent processing, retry, plan/entitlement update | IT (webhooks, signed locally) / **INT (checkout/portal)** | No Stripe test key. Checkout honestly reports `billing_not_configured`. |
| Usage metering (idempotent), server-side entitlements | IT | |
| Reporting metered usage to Stripe | **NI** | Usage is metered internally only |
| White-label branding, public branding by custom domain | IT | Custom-domain TLS/DNS automation NI |
| Analytics dashboard, platform admin (tenants, health, dead letters, costs, security events, suspension) | IT | |
| Observability: JSON logs with redaction + request IDs, Prometheus `/metrics`, OTel tracing hooks, health/readiness | IT (logs/metrics/health) / INT (OTLP export) | |
| Retention jobs | IT (manual trigger) | Scheduler wiring INT |
| Session recording | **NI** | Off by default. There's a flag but no recorder. |
| Upload malware scanning | **P** | Extension, magic-byte, executable, macro and zip-bomb checks. No AV engine. |
| Web app: all portal/console/admin/platform pages, accessible labels, mobile layout | IT | `tsc` clean, `next build` OK, `tests/e2e/web/ui_smoke.py` passes in real Chromium |
| Voice input | INT | Web Speech API; can't run headless |
| Docker images / Compose stack | **B** | `docker compose config` valid. Images not built (registry blocked). |
| CI (GitHub Actions: API, web, windows-latest, protected signed release) | INT | Workflows implemented. Not executed because this checkout has no Git remote; no workflow evidence exists. |
| Production deployment | NI (by design) | Reference k8s manifests only. Nothing deployed. |

## Tests actually run (this environment)

| Command | Result |
|---|---|
| `cd apps/api && PYTHONPATH=. .venv/bin/pytest` | **152 passed, 2 skipped, 0 failed** in 64.68 s on the final post-review run. The two simulator-spawning tests skipped because native `dotnet` is unavailable on this Mac. |
| `ruff check joltiq tests` / `mypy joltiq` | Passed; mypy reports no issues in 80 source files. |
| .NET 8 container: `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest -c Release` | **20 passed**, 0 failed. The final security self-review added a fail-closed test for a reused command ID with altered signed parameters. |
| .NET 8 container: Outlook fixture `validate-harness` | Four contract checks passed: complete catalog, valid synthetic bundle, missing evidence rejected, secret-bearing evidence rejected. This is not Windows acceptance. |
| `dotnet build/test` Windows / Tray / Broker / Windows xUnit / WiX | **BLOCKED_EXTERNAL_DEPENDENCY**: not run on Windows. |
| `apps/web: npm run typecheck && npm run build` | Passed; Next.js generated all 35 routes. |
| `tests/e2e/web/ui_smoke.py` against a running stack | Passed: 23 pages, register → knowledge upload → chat → ticket → queue → sign-out redirect, no page errors or 5xx |
| `tests/performance/perf.py` (20 concurrent, 4 API workers, same 2-vCPU box as DB + browser + web) | ticket list p50 1.03 s / p95 1.33 s (~19 rps); search p50 1.03 s; create p95 3.0 s; browser session start p50 109 ms (Chromium launch 817 ms); crashed-worker job reclaimed in 0.5 s. Single-request latency is 10–15 ms, so these figures are CPU-bound by this sandbox. |
| `pip-audit -r requirements.lock` | **No known vulnerabilities found** after upgrading `certifi` to `2026.10.19`. The post-upgrade container run also passed the complete API/browser suite: 152 passed, 2 expected skips. |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities. |
| Windows acceptance | **0/59 required runs**; no aggregate or real-Windows evidence exists. |

## Known risks and limitations
- Per-tenant audit hash chain uses an advisory lock, which serializes writes within one tenant (visible in create p95). Consider batching or a per-tenant sequence table.
- The local embedder is lexical, not semantic. Retrieval quality without an approved OpenAI key is keyword-grade.
- Browser read-back trusts the customer's web app to reflect persisted state on reload. Apps with client-side caches need app-specific postconditions.
- `browser.upload` is advertised in the registry but unsupported in the worker.
- The SSRF guard validates DNS and then lets httpx resolve again (small rebinding window). Production must route fetches through an egress proxy that enforces public-only IPs (noted in `ssrf.py`).
- The focused Task 12 review was an implementer self-review, not an external security review. No penetration test has been performed. Don't make compliance claims.
- Windows results are unproven until the 59-run acceptance aggregate passes; simulator, synthetic bundle, and macOS results do not substitute.

## Needed from the owner
1. AI provider key(s): OpenAI / Anthropic / Gemini, plus approval to send ticket data to them.
2. Brave Search or Tavily API key.
3. Stripe test account: secret key, webhook secret, and prices with lookup keys `joltiq_{plan}_{month|year}`.
4. Freshdesk sandbox (subdomain + API key) for live adapter acceptance.
5. Disposable Windows 10 and Windows 11 64-bit VMs, licensed classic Outlook on at least one Windows 11 VM, a connected Windows CI runner, and an Authenticode code-signing certificate.
6. Production infrastructure choices and approval: managed Postgres, Redis, S3, domain + TLS, secrets manager.
7. SMTP provider credentials.

## Where to resume (next tasks, in order)
0. Clear the Windows internal gate: connect the Git remote, run Windows CI, then run all 59 acceptance executions and produce a passing aggregate. Sign and timestamp the MSI before any pilot.
1. Implement secure, consent-bound `browser.upload` as the next browser/SaaS priority. Do not enable arbitrary screen control in the Windows Outlook MVP.
2. With credentials: run live smoke tests for each AI provider (Admin → AI models → Test), live research, Stripe checkout and webhook, and Freshdesk sync. Add `@pytest.mark.live` tests.
3. Implement OIDC (Entra) login (`/auth/oidc/{start,callback}` with PyJWT/JWKS validation).
4. Choose and review any future device screen/input layer separately; it is outside this Windows Outlook MVP.
5. Build Docker images in CI; run `docker compose up` acceptance.
6. External security review / penetration test.
