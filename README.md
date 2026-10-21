# JoltIQ

JoltIQ is an AI support technician for MSPs, B2B software support teams, internal IT and small service businesses. It takes in tickets, asks diagnostic questions, searches tenant knowledge, researches unfamiliar problems on the live web, and proposes a typed plan. It asks for consent and approval before acting, then operates an isolated browser or a Windows companion. It verifies each outcome **independently** before it resolves anything, and otherwise escalates to a person.

> Status: development build. The Windows Outlook PDF workflow is implemented but has **not** passed Windows CI, Windows VM acceptance, signing, or external review. It is not customer- or pilot-ready. See `PROJECT_STATUS.md` and `docs/operations/windows-outlook-pdf-evidence-index.md`.

## Repository

| Path | What |
|---|---|
| `apps/api` | FastAPI backend: auth, tenancy (PostgreSQL RLS), tickets, knowledge, AI orchestrator, research, policy, approvals, verifier, sessions, devices, integrations, billing, admin. Alembic migrations in `apps/api/migrations`. Tests in `apps/api/tests`. |
| `apps/web` | Next.js 15 / React 19 / Tailwind 4 / TanStack Query web app: marketing, auth, customer portal, technician console, org admin, platform admin. |
| `apps/desktop-agent` | Cross-platform (macOS/Windows) companion: signed protocol client + local policy for computer use. The screen/input layer is supplied separately (see its README). |
| `apps/windows-agent` | .NET 8 Windows companion: `Core` (protocol, crypto, local policy; cross-platform), `Windows` (UI Automation, registry, event log, services, DPAPI), `Tray` (consent dialog, banner, kill switch), `Broker` (privileged service), WiX installer, PowerShell scripts. |
| `services/browser-worker` | Isolated Playwright/Chromium worker (per-session contexts, domain allowlist at the network layer, secret-field masking). |
| `packages/agent-protocol` | `PROTOCOL.md`, the signed device protocol. |
| `infrastructure` | Dockerfiles, Kubernetes reference manifests, Prometheus config and alerts. |
| `tests/e2e` | Authorized local test app (`testapp/`) and real-browser UI smoke test (`web/ui_smoke.py`). `tests/performance/perf.py`. |
| `docs` | `api/openapi.json`, architecture, security, operations runbook, user guides. |

## Run locally (Docker)

```bash
cp .env.example .env
docker compose up --build              # postgres+pgvector, redis, minio, mailpit, migrate, api, worker, browser-worker, web
open http://localhost:3000             # register an organization
open http://localhost:8025             # mailpit: invitation / verification emails
docker compose --profile e2e up testapp   # optional authorized test target on :8300
```

## Run locally (without Docker)

Prerequisites: Python 3.12+, Node 22, PostgreSQL 16 with `pgvector`, Redis (optional), and the .NET 8 SDK (for the companion).

```bash
# database
createdb joltiq && psql joltiq -c 'create extension vector'
# api
cd apps/api && python -m venv .venv && .venv/bin/pip install -r requirements.lock && .venv/bin/pip install -e .
.venv/bin/python -m playwright install chromium
.venv/bin/alembic upgrade head                       # creates the RLS-enforced joltiq_app role
.venv/bin/uvicorn joltiq.main:app --port 8000 --workers 4
.venv/bin/python -m joltiq.worker                    # jobs + scheduler (separate terminal)
# browser worker
cd services/browser-worker && ../../apps/api/.venv/bin/uvicorn worker.app:app --port 8100
# web
cd apps/web && npm ci && npm run build && JOLTIQ_API_URL=http://127.0.0.1:8000 npm start
```

## Tests

```bash
createdb joltiq_test && psql joltiq_test -c 'create extension vector'
cd apps/api && PYTHONPATH=. .venv/bin/pytest         # unit, integration, security, browser E2E, desktop protocol E2E
cd apps/web && npm run typecheck && npm run build
python tests/e2e/web/ui_smoke.py http://127.0.0.1:3000 ./shots   # real-browser UI smoke (stack must be running)
python tests/performance/perf.py                     # latency/concurrency against a running stack
cd apps/windows-agent && dotnet run --project tests/JoltIQ.Agent.Core.SelfTest   # protocol/policy self-tests (any OS)
pwsh apps/windows-agent/scripts/build.ps1 -Version 1.0.0                          # Windows: build + xUnit + MSI
```

Tests that replace an external service with a local fixture are marked `@pytest.mark.mocked`.

The Windows acceptance gate is separate from compilation and simulation. Run `apps/windows-agent/scripts/test-on-windows.ps1` only on authorized disposable Windows 10/11 VMs and retain the evidence described in `docs/operations/windows-outlook-pdf-acceptance.md`. Until that aggregate passes, any installer is unsigned internal-only.

Browser/SaaS automation remains the next product priority after Windows internal acceptance. Consent-bound `browser.upload` uses a reusable published binding to the attachment explicitly selected by the customer on that ticket; the resolved file is covered by the exact plan and approval. It is tested end to end with real Chromium against the authorized local FieldDesk fixture. Public SaaS compatibility, live-account acceptance, production deployment and external security review remain unproven; see `docs/operations/browser-upload-evidence.md`.

## Configure integrations
- **AI models**: Admin → AI models. Add an OpenAI, Anthropic or Gemini key, tick *Approved to receive our ticket data*, then Test. Without a key, a clearly labelled deterministic **local test model** runs approved procedures only, and it is disabled in production.
- **Web research**: set `JOLTIQ_BRAVE_SEARCH_API_KEY` or `JOLTIQ_TAVILY_API_KEY`.
- **Freshdesk**: Admin → Integrations → Freshdesk (subdomain + API key) → Test → Sync now.
- **Email to ticket**: create an *Email to ticket* integration and point your mail relay at `POST /api/v1/integrations/email/inbound/{id}` with `Authorization: Bearer <secret>`.
- **Stripe**: set test keys, create prices with lookup keys `joltiq_{starter|growth|scale}_{month|year}`, and point a webhook at `/api/v1/billing/webhook`.
- **Windows companion**: Devices → *Add a Windows PC* gives a one-time code. Install the MSI, choose *Enroll this device* from the tray, and enter the code.

## Deploy
See `docs/operations/runbook.md` and `infrastructure/deployment/k8s/joltiq.yaml`. Nothing has been deployed from this repository.
