# Project status — JoltIQ

**Last updated:** 2026-10-18 · **Build environment:** Linux sandbox (2 vCPU), PostgreSQL 16.15 + pgvector, Redis 7.0, Python 3.13, Node 22, .NET SDK 8.0.131, Playwright Chromium 1194. No Windows host. Docker registry and nuget.org were unreachable.

**Overall:** All 12 milestones have working code. The full browser vertical slice runs end to end on real infrastructure. The desktop path runs end to end through the real protocol code with a **simulated OS**. **Not production-ready:** live credentials, Windows runtime verification, container image builds and a security review are outstanding.

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
| Windows companion protocol (enroll, signed requests, nonce/counter replay protection, signed envelopes, revocation) | IT | `test_device_protocol.py` (Python attacker), .NET `SelfTest` 10/10, `test_e2e_desktop.py` through the .NET runtime |
| Windows OS actions (UIA, registry HKCU, event log, services, processes), DPAPI key store, tray consent dialog / banner / kill switch hotkey, privileged broker | **B** | Source written; **cannot compile on Linux** (Windows Desktop SDK). Needs a Windows build host + an authorized Windows 11 test machine. |
| Windows installer (WiX v5 MSI), signing, repair/uninstall | **B** | `installer/Product.wxs`, `scripts/build.ps1`, `sign.ps1`, `test-on-windows.ps1`. Not built. **Not signed** (no certificate). |
| Companion authenticated auto-update | **P** | Heartbeat reports latest/required version. No signed downloader yet. |
| **Computer use (screen-driving agent)**: goal approval by the consenting customer, look→act loop, approval / question / “your turn to type” pauses, secret-typing refusal, step limit, budget, stop on session end, per-step screenshots + audit, live screen in the session page | IT (server, fake device + scripted fixture model) | `test_computer_use.py` (6 tests) through the real signed device protocol. Anthropic computer toolset adapter (`computer_toolset_20260801`, legacy `computer_20251124`) **INT (live)**: no key |
| Device screen/input layer for computer use | **NI (by design here)** | Not in this repo. Plug in an established remote-support SDK or an in-house adapter; contract in `apps/desktop-agent/README.md`. Protocol client + local policy for macOS/Windows in `apps/desktop-agent` |
| Bulk import of past tickets (CSV/JSON from Zendesk, Freshdesk, Intercom, HubSpot, Jira, spreadsheets) and help articles (ZIP) with PII redaction | IT | `test_computer_use.py::test_bulk_import…` |
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
| CI (GitHub Actions: API, web, windows-latest) | INT | Not executed (no push) |
| Production deployment | NI (by design) | Reference k8s manifests only. Nothing deployed. |

## Tests actually run (this environment)

| Command | Result |
|---|---|
| `cd apps/api && pytest` | **98 passed**, 0 failed (68 s). Includes real-Chromium browser E2E (6), desktop protocol E2E via .NET (2), security (29), model-adapter contracts (8). Tests marked `mocked` use local fixtures for AI, search, Freshdesk, SMTP and webhook receivers. |
| `ruff check joltiq tests` / `mypy joltiq` | All checks passed / no issues in 74 files (mypy caught and fixed an OpenAI-adapter `NameError`) |
| `dotnet run --project apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest` | **10 passed**, 0 failed |
| `dotnet build` Core / Simulator / SelfTest | Succeeded (offline config) |
| `dotnet build` Windows / Tray / Broker / Tests | **BLOCKED**: NETSDK1100, requires Windows |
| `apps/web: tsc --noEmit` / `eslint src` / `next build` (Next 16.4.2) | Clean / clean / built |
| `tests/e2e/web/ui_smoke.py` against a running stack | Passed: 23 pages, register → knowledge upload → chat → ticket → queue → sign-out redirect, no page errors or 5xx |
| `tests/performance/perf.py` (20 concurrent, 4 API workers, same 2-vCPU box as DB + browser + web) | ticket list p50 1.03 s / p95 1.33 s (~19 rps); search p50 1.03 s; create p95 3.0 s; browser session start p50 109 ms (Chromium launch 817 ms); crashed-worker job reclaimed in 0.5 s. Single-request latency is 10–15 ms, so these figures are CPU-bound by this sandbox. |
| `pip-audit -r requirements.lock` / `npm audit --omit=dev` | Clean after upgrades (were: python-multipart, starlette, cryptography, pyjwt, pypdf, pytest, next/postcss) |

## Known risks and limitations
- Per-tenant audit hash chain uses an advisory lock, which serializes writes within one tenant (visible in create p95). Consider batching or a per-tenant sequence table.
- The local embedder is lexical, not semantic. Retrieval quality without an approved OpenAI key is keyword-grade.
- Browser read-back trusts the customer's web app to reflect persisted state on reload. Apps with client-side caches need app-specific postconditions.
- `browser.upload` is advertised in the registry but unsupported in the worker.
- The SSRF guard validates DNS and then lets httpx resolve again (small rebinding window). Production must route fetches through an egress proxy that enforces public-only IPs (noted in `ssrf.py`).
- No security review, penetration test or dependency audit has been performed. Don't make compliance claims.

## Needed from the owner
1. AI provider key(s): OpenAI / Anthropic / Gemini, plus approval to send ticket data to them.
2. Brave Search or Tavily API key.
3. Stripe test account: secret key, webhook secret, and prices with lookup keys `joltiq_{plan}_{month|year}`.
4. Freshdesk sandbox (subdomain + API key) for live adapter acceptance.
5. A Windows 11 test machine (and a Windows build agent or the CI windows-latest runner) and an Authenticode code-signing certificate.
6. Production infrastructure choices and approval: managed Postgres, Redis, S3, domain + TLS, secrets manager.
7. SMTP provider credentials.

## Where to resume (next tasks, in order)
0. Computer use: choose and integrate the device screen/input layer (remote-support SDK or in-house adapter per `apps/desktop-agent/README.md`), then run a live goal with an Anthropic key on a test machine. Add OpenAI computer-use adapter.
1. On Windows: `pwsh apps/windows-agent/scripts/build.ps1 -Version 1.0.0`, fix any compile errors in the Windows / Tray / Broker projects, run `JoltIQ.Agent.Tests`, then `scripts/test-on-windows.ps1` on an authorized Windows 11 host. Record results here.
2. With credentials: run live smoke tests for each AI provider (Admin → AI models → Test), live research, Stripe checkout and webhook, and Freshdesk sync. Add `@pytest.mark.live` tests.
3. Implement OIDC (Entra) login (`/auth/oidc/{start,callback}` with PyJWT/JWKS validation).
4. Implement `browser.upload` in the worker, plus desktop screen viewing.
5. Build Docker images in CI; run `docker compose up` acceptance.
6. External security review / penetration test.
