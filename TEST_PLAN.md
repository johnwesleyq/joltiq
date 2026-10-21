# Test plan

| Layer | Location | Runs where | Notes |
|---|---|---|---|
| API unit + integration (real PostgreSQL, RLS role) | `apps/api/tests/test_*.py` | Any Linux / CI | Rebuilds `joltiq_test` from migrations per session |
| Security | `apps/api/tests/test_security.py`, `test_tenancy_rls.py`, `test_device_protocol.py` | Same | Approval bypass, tamper, replay, SSRF, secrets, RBAC, lease |
| Browser E2E (real Chromium + authorized test app) | `apps/api/tests/test_e2e_browser.py`, `tests/e2e/testapp` | Same (needs Playwright Chromium) | Known issue → VERIFIED; verification failure; denied approval; unfamiliar issue → research → escalation; cross-tenant; emergency stop |
| Desktop E2E (real .NET protocol code, simulated OS) | `apps/api/tests/test_e2e_desktop.py`, `JoltIQ.Agent.Simulator` | Needs .NET 8 SDK | **Simulated OS** (labelled fixture) |
| Model adapter contracts | `apps/api/tests/test_model_adapters.py` | Same | Request/response shapes; router fallback; costs |
| Companion core | `apps/windows-agent/tests/JoltIQ.Agent.Core.SelfTest` | Any OS | 20 cases; Task 12 passed 20/20 in a .NET 8 SDK container |
| Windows providers / DPAPI | `apps/windows-agent/tests/JoltIQ.Agent.Tests` (xUnit) | **Windows only** | `IMPLEMENTED_NOT_TESTED`; no Windows CI evidence |
| Windows installer lifecycle | `apps/windows-agent/scripts/test-on-windows.ps1 -InstallerLifecycle` | Windows 10/11 64-bit VM | Install, tray/service start, ACLs, repair, upgrade/downgrade preservation, uninstall; not yet run |
| Windows Outlook acceptance | `apps/windows-agent/scripts/test-on-windows.ps1`, `scripts/acceptance/cases.json` | Authorized disposable Windows 10/11 VMs | 56 cases / 59 required runs, including five Windows 11 snapshot happy paths, Windows 10, licensed classic Outlook, all failure cases, and 14 evidence classes; **0 accepted runs currently** |
| Web | `npm run typecheck && npm run lint && npm run build`; `tests/e2e/web/ui_smoke.py` | Node 22 + running stack | |
| Performance | `tests/performance/perf.py` | Running stack | Report p50/p95, browser start, worker recovery |
| Live (credentials) | to add: `@pytest.mark.live` | With keys | AI providers, search, Stripe, Freshdesk |

Rules: a test that substitutes a local fixture for an external service is marked `mocked`. Never record an unexecuted test as passed. A simulator or synthetic evidence-bundle test is not Windows acceptance. Record exact commands and counts in `PROJECT_STATUS.md` and `docs/operations/windows-outlook-pdf-evidence-index.md`.
