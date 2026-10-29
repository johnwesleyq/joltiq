# Project status — JoltIQ

**Last updated:** 2026-10-29 · **Current environment:** macOS development host plus an enrolled physical Windows 11 64-bit PC used for earlier native build/test evidence. The Windows PC is not a disposable snapshot-reset VM. No Windows 10 VM, production signing certificate, or completed external security review is attached.

**Overall:** V7 verification/rollback, lease fencing, approval alerts, Slack/Teams connectors, labelled development fixtures, and the integrated support UI are now merged through source commit `8fc3b9b`. Local API, browser-worker, web build, browser, type, lint, dependency, and Compose-configuration gates pass. Windows-native general computer use remains **not approved for unrestricted customer deployment** until the disposable Windows 10/11 matrix, production signing, installer lifecycle evidence, and independent security review pass.

Status legend: **IT** = IMPLEMENTED_AND_TESTED · **INT** = IMPLEMENTED_NOT_TESTED · **P** = PARTIALLY_IMPLEMENTED · **B** = BLOCKED_EXTERNAL_DEPENDENCY · **NI** = NOT_IMPLEMENTED

## Feature status

| Area | Status | Notes / evidence |
|---|---|---|
| Registration, login, logout, sessions (idle + absolute expiry), CSRF, lockout, rate limits | IT | `test_auth.py` |
| Email verification, password reset (revokes sessions), invitations, roles, org switching | IT | `test_auth.py`, SMTP delivery via local aiosmtpd (`test_platform_features.py`) |
| TOTP MFA | IT | `test_auth.py::test_mfa_totp_enrollment_and_login` |
| OIDC / Microsoft Entra SSO | IT (contract/local) / **INT (live)** | Work-email discovery, OIDC callback/session binding, MFA continuation, redacted admin configuration, and browser tests pass. A real Entra tenant/app registration has not been live-tested. |
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
| Windows companion protocol (enroll, signed requests, nonce/counter replay protection, signed envelopes, revocation, durable journal/outbox) | IT | API protocol tests plus .NET Core self-test **25/25**; native Windows full suite **72/72** on the physical Windows 11 PC. |
| Outlook PDF diagnosis, exact published plan, per-step approval, HKCU mutation, safe restart, rollback, and symptom verification | **INT / B** | Implemented with portable/server tests. `IMPLEMENTED_NOT_TESTED` on Windows; `BLOCKED_EXTERNAL_DEPENDENCY` for the required Windows VM matrix. |
| Windows OS providers, DPAPI journal, native capture/input, Guide overlay, per-run consent, STOP, and tray/SYSTEM separation | IT / **B (VM matrix)** | Native Windows full suite **72/72** on the physical PC. Disposable Windows 10/11 behavioral acceptance remains missing. |
| Windows installer (WiX v5), lifecycle, signing, repair/upgrade/uninstall | **IT (internal package) / B (external release)** | The physical Windows 11 PC produced an unsigned internal MSI after native build, test, contract, publish, capability-manifest, WiX, and checksum gates. External output is refused without valid signing/timestamp inputs. Production signing and install/repair/upgrade/rollback/uninstall evidence remain missing. |
| Companion authenticated auto-update | **P** | Heartbeat reports latest/required version. No signed downloader yet. |
| **Computer use (screen-driving agent)**: observe → decide → act → verify, exact goal approval, native consent, durable screenshots, pauses, verification and STOP | IT / **B (release evidence)** | Server/API orchestration, Windows native driver, Guide/Fix portal controls and signed protocol are implemented and tested. Live external model use remains untested without an approved provider/local-model deployment. |
| Device screen/input layer for computer use | **Windows: IT / B** · **macOS: NI** | Windows uses native virtual-desktop capture, UI Automation guards and bounded `SendInput`; Guide has structurally no input authority. macOS needs its own native implementation and acceptance plan. The Python desktop package is reference-only, not a production driver. |
| Bulk import of past tickets (CSV/JSON from Zendesk, Freshdesk, Intercom, HubSpot, Jira, spreadsheets) and help articles (ZIP) with PII redaction | IT | `test_computer_use.py::test_bulk_import…` |
| Memory: successful computer-use runs saved as “Learned fix” knowledge (redacted, staff-only by default) and recalled at the start of the next similar run; chat shares known fixes from history | IT | `test_computer_use.py::test_successful_run_is_remembered…`, `…test_chat_shares_known_fix…` |
| Desktop technician takeover (human driving the remote screen) | **NI** | Browser sessions only |
| Integrations: Freshdesk (pull/push/status, idempotency, conflict detection, retry, rotate, revoke) | IT (mocked) / **INT (live)** | Fake Freshdesk transport. No Freshdesk account available. |
| Generic REST inbound, outbound signed webhooks with retry, email-to-ticket | IT | Local webhook receiver, RFC 5322 messages |
| Slack / Teams approval alerts | IT (labelled local mocks) / **INT (live)** | Encrypted/redacted connector settings, host validation, event selection, rate limits, dedupe, deep links, and MOCK viewers are tested. Real workspaces are not live-tested. |
| Zendesk, ServiceNow, Jira SM, HaloPSA, ConnectWise, Intune, NinjaOne, Datto, M365, Google | **NI** (documented extension points; API returns 501) | |
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

V7 integration evidence is indexed in `docs/evidence/v7-integration-acceptance.md`.

| Command | Result |
|---|---|
| Focused API computer-use/device/security/procedure suites | **94 passed, 0 failed** after Guide/Fix orchestration. Full post-increment API verification is rerun as part of the release gate. |
| Full repository `ruff check joltiq tests` / `mypy joltiq` | Passed. The protected Windows release workflow reruns both plus the full API suite before signing. |
| Physical Windows 11: Windows xUnit suite | **72 passed, 0 failed**, including real capture/input/provider/overlay and STOP-focused tests. |
| Core self-test | **25 passed, 0 failed**. |
| .NET 8 container: Outlook fixture `validate-harness` | Four contract checks passed: complete catalog, valid synthetic bundle, missing evidence rejected, secret-bearing evidence rejected. This is not Windows acceptance. |
| Physical Windows 11 full packaging gate | Passed under Windows PowerShell 5.1: build with warnings-as-errors; Core self-test **25/25**; Windows xUnit **72/72**; Outlook and general computer-use harness contracts; self-contained tray and broker publishes; native capability-manifest validation; WiX MSI; checksum and internal-classification manifest. Build-gate tests **15/15**. |
| Physical Windows 11 internal MSI | `JoltIQCompanion-1.0.0-x64.msi`, 83,289,883 bytes; SHA-256 `7a5951a52715cbdd16c22afad8e31c68ec4cedb1f7f5fe8cf8c25530ca25f3ae`; explicitly unsigned/internal-only. |
| `apps/web: npm run typecheck && npm run build` | Passed; Next.js generated all 35 routes. |
| `tests/e2e/web/ui_smoke.py` against a running stack | Passed: 23 pages, register → knowledge upload → chat → ticket → queue → sign-out redirect, no page errors or 5xx |
| `tests/performance/perf.py` (20 concurrent, 4 API workers, same 2-vCPU box as DB + browser + web) | ticket list p50 1.03 s / p95 1.33 s (~19 rps); search p50 1.03 s; create p95 3.0 s; browser session start p50 109 ms (Chromium launch 817 ms); crashed-worker job reclaimed in 0.5 s. Single-request latency is 10–15 ms, so these figures are CPU-bound by this sandbox. |
| `pip-audit -r requirements.lock` | **No known vulnerabilities found** after upgrading `certifi` to `2026.10.19`. The post-upgrade container run also passed the complete API/browser suite: 152 passed, 2 expected skips. |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities. |
| Full API suite at `8fc3b9b` | **289 passed, 3 skipped, 0 failed** (292 collected). Skips require live outside services. |
| API static gates at `8fc3b9b` | Ruff passed; MyPy passed across **88 source files**. |
| Browser-worker suite at `8fc3b9b` | **16 passed, 0 failed**. |
| Web browser suite at `8fc3b9b` | **9 passed, 7 explicitly skipped, 0 failed**. Skips are seeded production-shaped/Mailpit/Slack/Teams scenarios requiring a healthy external stack and credentials. |
| General computer-use harness contract on physical Windows / PowerShell 5.1 | Passed; final packaging/build gate **15/15**. |
| General computer-use behavioral acceptance | **0/35 required runs**; no disposable-VM aggregate exists. Physical-PC automated tests do not substitute. |
| Outlook PDF behavioral acceptance | **0/59 required runs**; no disposable-VM aggregate exists. |

## Known risks and limitations
- Per-tenant audit hash chain uses an advisory lock, which serializes writes within one tenant (visible in create p95). Consider batching or a per-tenant sequence table.
- The local embedder is lexical, not semantic. Retrieval quality without an approved OpenAI key is keyword-grade.
- Browser read-back trusts the customer's web app to reflect persisted state on reload. Apps with client-side caches need app-specific postconditions.
- `browser.upload` is advertised in the registry but unsupported in the worker.
- The SSRF guard validates DNS and then lets httpx resolve again (small rebinding window). Production must route fetches through an egress proxy that enforces public-only IPs (noted in `ssrf.py`).
- The focused Task 12 review was an implementer self-review, not an external security review. No penetration test has been performed. Don't make compliance claims.
- Windows automated behavior is proven on the enrolled physical Windows 11 PC, but release acceptance remains blocked until the separate 35-run general computer-use and 59-run Outlook snapshot-reset aggregates pass. Simulator, synthetic-bundle, macOS, and ordinary physical-PC results do not substitute.

## Needed from the owner
1. AI provider key(s): OpenAI / Anthropic / Gemini, plus approval to send ticket data to them.
2. Brave Search or Tavily API key.
3. Stripe test account: secret key, webhook secret, and prices with lookup keys `joltiq_{plan}_{month|year}`.
4. Freshdesk sandbox (subdomain + API key) for live adapter acceptance.
5. Disposable Windows 10 and Windows 11 64-bit VMs, licensed classic Outlook on at least one Windows 11 VM, a connected Windows CI runner, and an Authenticode code-signing certificate.
6. Production infrastructure choices and approval: managed Postgres, Redis, S3, domain + TLS, secrets manager.
7. SMTP provider credentials.

## Where to resume (next tasks, in order)
0. Clear the Windows release gate: run Windows CI and installer lifecycle, run all 35 general computer-use plus 59 Outlook acceptance executions on named clean snapshots, and produce both passing aggregates. Sign and timestamp the MSI before any external pilot.
1. Execute the approved local ticket-worker plan, then the on-prem licensing/model boundary and browser/SaaS completion plans.
2. With credentials: run live smoke tests for each AI provider (Admin → AI models → Test), live research, Stripe checkout and webhook, and Freshdesk sync. Add `@pytest.mark.live` tests.
3. Implement OIDC (Entra) login (`/auth/oidc/{start,callback}` with PyJWT/JWKS validation).
4. Implement and review the macOS native screen/input layer under its separate plan; do not reuse Windows procedures as macOS evidence.
5. Build Docker images in CI; run `docker compose up` acceptance.
6. External security review / penetration test.
