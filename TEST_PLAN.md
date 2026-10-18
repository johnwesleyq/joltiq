# Test plan

| Layer | Location | Runs where | Notes |
|---|---|---|---|
| API unit + integration (real PostgreSQL, RLS role) | `apps/api/tests/test_*.py` | Any Linux / CI | Rebuilds `joltiq_test` from migrations per session |
| Security | `apps/api/tests/test_security.py`, `test_tenancy_rls.py`, `test_device_protocol.py` | Same | Approval bypass, tamper, replay, SSRF, secrets, RBAC, lease |
| Browser E2E (real Chromium + authorized test app) | `apps/api/tests/test_e2e_browser.py`, `tests/e2e/testapp` | Same (needs Playwright Chromium) | Known issue → VERIFIED; verification failure; denied approval; unfamiliar issue → research → escalation; cross-tenant; emergency stop |
| Desktop E2E (real .NET protocol code, simulated OS) | `apps/api/tests/test_e2e_desktop.py`, `JoltIQ.Agent.Simulator` | Needs .NET 8 SDK | **Simulated OS** (labelled fixture) |
| Model adapter contracts | `apps/api/tests/test_model_adapters.py` | Same | Request/response shapes; router fallback; costs |
| Companion core | `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest` | Any OS | 10 cases |
| Windows providers / DPAPI | `apps/windows-agent/tests/JoltIQ.Agent.Tests` (xUnit) | **Windows only** | Not yet run |
| Windows acceptance (install, enroll, session, stop, repair, uninstall) | `apps/windows-agent/scripts/test-on-windows.ps1` | Authorized Windows 11 host | Not yet run |
| Web | `npm run typecheck && npm run lint && npm run build`; `tests/e2e/web/ui_smoke.py` | Node 22 + running stack | |
| Performance | `tests/performance/perf.py` | Running stack | Report p50/p95, browser start, worker recovery |
| Live (credentials) | to add: `@pytest.mark.live` | With keys | AI providers, search, Stripe, Freshdesk |

Rules: a test that substitutes a local fixture for an external service is marked `mocked`. Never record an unexecuted test as passed. Record exact commands and counts in `PROJECT_STATUS.md`.
