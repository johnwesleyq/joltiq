# MeshCentral Remote Access Integration Plan

> **Execution:** Inline in the existing `feat/direct-device-help` worktree. Follow TDD and preserve all unrelated dirty changes.

**Goal:** Replace JoltIQ's unreliable custom technician screen-control path with a self-hosted MeshCentral remote-desktop provider while keeping JoltIQ responsible for identity, authorization, consent policy, audit history, supported repair automation, verification, and ticket updates.

**Architecture:** MeshCentral is an optional deployment service and the remote transport only. A JoltIQ device may be bound to one MeshCentral node. A staff user with `devices.manage` can start an audited remote-access session and receive a provider launch URL. MeshCentral must independently prompt the person at the Windows PC before desktop access and must never auto-accept. JoltIQ's existing companion remains the only executor for approved structured repairs; remote access is for visible technician takeover, not a side door for executable AI plans.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic/PostgreSQL RLS, Next.js/React, Docker Compose, MeshCentral 1.3.1.

**Spec:** `docs/superpowers/specs/2026-10-21-joltiq-computer-on-prem-design.md`

---

## Global constraints

- MeshCentral is disabled unless a deployment-owned URL is configured.
- Never accept a provider URL, credentials, or host from a tenant request.
- Never place MeshCentral credentials or login tokens in the database, audit data, browser bundle, or logs.
- Only staff with `devices.manage` may bind nodes or start/end remote sessions.
- Customer/local consent is mandatory in MeshCentral configuration: desktop prompt on; timeout deny; no unattended auto-accept.
- Terminal and file access are outside this first slice and disabled by policy.
- Every start, denial, and end is auditable and tenant-scoped.

## Task 1: Deployment-owned MeshCentral configuration

**Files:** `docker-compose.yml`, `.env.example`, `infrastructure/meshcentral/config.json`

1. Add a Compose `remote-access` profile with a pinned MeshCentral image, persistent data, health check, and localhost development port.
2. Configure framing, forced desktop consent, visible privacy notification, timeout denial, no public account creation, and no terminal/file consent paths.
3. Add API environment settings for enablement and public base URL.
4. Verify the rendered Compose configuration with `docker compose --profile remote-access config`.

## Task 2: Tenant-safe remote access domain model

**Files:** `apps/api/joltiq/models/operations.py`, `apps/api/joltiq/models/__init__.py`, `apps/api/migrations/versions/0008_remote_access.py`

1. First add API tests proving an unbound device cannot start remote access, customers cannot bind or start it, and one tenant cannot see another tenant's binding/session.
2. Add `RemoteAccessBinding` and `RemoteAccessSession` models with provider, node id, operator, reason, state, timestamps, and tenant ownership.
3. Add migration, indexes, constraints, and RLS policies matching the existing tenant model.
4. Run the focused tests and migration rebuild.

## Task 3: Audited remote-access API

**Files:** `apps/api/joltiq/config.py`, `apps/api/joltiq/routers/devices.py`, `apps/api/joltiq/services/remote_access.py`, `apps/api/tests/test_remote_access.py`

1. Add failing tests for binding, start, idempotent active-session reuse, end, sanitized URL construction, disabled-provider behavior, and audit events.
2. Implement deployment-owned URL validation and fixed MeshCentral desktop route construction.
3. Add bind/status/start/end endpoints under `/devices/{id}/remote-access` guarded by `devices.manage`.
4. Return only the launch URL and session metadata; never provider credentials.
5. Run focused API tests.

## Task 4: Technician workflow in the JoltIQ device page

**Files:** `apps/web/src/app/(app)/portal/devices/page.tsx`, `apps/web/src/lib/api.ts` (types only if needed)

1. Add a device-row Remote support action for staff.
2. Add a small modal/panel that shows whether the device is bound, captures a required reason, starts the audited session, and opens MeshCentral in a new tab.
3. State clearly that the person at the PC must click Allow and that starting remote support does not authorize an automated repair.
4. Provide an End session action that updates JoltIQ's audit state.
5. Run TypeScript/lint/build checks.

## Task 5: Local integration and Windows acceptance handoff

**Files:** `docs/runbooks/meshcentral-remote-access.md`, `docs/evidence/meshcentral/README.md`

1. Start the MeshCentral profile locally and prove its health endpoint/UI responds.
2. Document first-admin creation, a restricted technician account, device-group desktop-only consent settings, Windows MeshAgent enrollment, node-id binding, and emergency disablement.
3. Record the exact physical-Windows acceptance sequence: local user sees consent, deny blocks access, allow opens desktop, privacy bar remains visible, disconnect ends control, JoltIQ audit shows start/end, and no terminal/file access is available.
4. Run the complete API suite plus web build and record honest evidence; physical Windows acceptance remains open until actually observed.

## Definition of done

- The optional MeshCentral service starts with forced interactive desktop consent.
- JoltIQ can bind a device, start and end an audited remote-support session, and launch the correct MeshCentral node.
- Tenant isolation and staff authorization are covered by tests.
- No provider secret is exposed through JoltIQ.
- The UI distinguishes technician remote support from automatic approved repair execution.
- Launch readiness is not claimed until the physical Windows acceptance sequence passes and evidence is saved.
