# Selective Integration Port Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add organization-owned OIDC/Entra SSO, Freshdesk webhook hardening, and external audit anchoring without replacing any existing JoltIQ subsystem.

**Architecture:** Execute three independent, additive plans in order. Each ships disabled by default, uses current models/services as its foundation, and receives a full regression gate before the next begins.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, PostgreSQL RLS, Redis-backed job queue, Next.js/TypeScript, PyJWT/cryptography, httpx, boto3, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-28-selective-integration-port-design.md`

## Global Constraints

- Current JoltIQ is canonical; the uploaded ZIP is untrusted reference material only.
- Do not delete or replace the current Windows companion, computer use, MeshCentral, browser worker, ticketing, knowledge, model routing or existing integration records.
- Cloud connectors are disabled by default; fully offline operation remains supported.
- All schema changes are forward-only migrations after `0008_remote_access`.
- Do not claim real Entra, Freshdesk, immutable S3 or SOC 2 acceptance without external evidence.
- Every task follows red-green TDD and ends in a focused commit.

## Review Focus

- An upgrade from the current production-shaped schema with existing users, devices and Freshdesk rows preserves every record and default behavior; covered by each plan's migration task.
- An organization with all new connectors disabled makes no new network calls; covered by each plan's disabled-default tests.
- Cross-tenant identifiers, webhook IDs, OIDC state and anchor prefixes cannot expose or mutate another organization; covered by each plan's security task.
- Connector outages do not stop core ticket/device/AI processing; covered by Freshdesk and audit worker failure tests and OIDC existing-session tests.
- Existing Windows/browser/direct-device regression suites remain green after all three plans; covered by the final release gate.

---

### Task 1: Implement OIDC/Entra plan

**Plan:** `docs/superpowers/plans/2026-10-28-oidc-entra-implementation.md`

- [ ] Complete every task and focused commit in the OIDC plan.
- [ ] Run its full acceptance gate and record live Entra as blocked until credentials exist.

### Task 2: Implement Freshdesk hardening plan

**Plan:** `docs/superpowers/plans/2026-10-28-freshdesk-hardening-implementation.md`

- [ ] Rebase the plan against Task 1's migration head without changing its interfaces.
- [ ] Complete every task and focused commit in the Freshdesk plan.
- [ ] Run its full acceptance gate and record real Freshdesk as blocked until a sandbox exists.

### Task 3: Implement audit anchoring plan

**Plan:** `docs/superpowers/plans/2026-10-28-audit-anchoring-implementation.md`

- [ ] Rebase the plan against Task 2's migration head without changing its interfaces.
- [ ] Complete every task and focused commit in the audit plan.
- [ ] Run its full acceptance gate; label file/local-S3 results honestly.

### Task 4: Whole-branch release gate

**Files:**
- Modify: `PROJECT_STATUS.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/guides/admin.md`

**Interfaces:**
- Consumes: all public routes, models, worker handlers and UI from the three plans.
- Produces: one evidence-backed status update with no unsupported production claim.

- [ ] **Step 1: Run complete API verification**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q`

Expected: all collected tests pass except tests explicitly marked/skipped for unavailable live credentials.

- [ ] **Step 2: Run API static checks**

Run: `cd apps/api && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq`

Expected: exit 0 for both.

- [ ] **Step 3: Run web verification**

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: exit 0 and all routes generated.

- [ ] **Step 4: Run browser and UI smoke verification**

Run the repository's documented browser-worker tests and `tests/e2e/web/ui_smoke.py` against the rebuilt local stack.

Expected: exit 0; login, admin security, integrations and audit pages have no 5xx or browser errors.

- [ ] **Step 5: Run Windows non-regression contracts**

Run the current Core self-test, Windows build-contract tests and capability-manifest validation documented under `apps/windows-agent`.

Expected: all existing contracts pass; do not substitute this for the separate disposable-VM release matrix.

- [ ] **Step 6: Verify disabled-default egress**

Start a clean stack with OIDC, Freshdesk webhook and audit anchor settings unset; capture outbound-request fixture counters while running registration, ticket, direct-device-help and audit pages.

Expected: zero calls to OIDC discovery, Freshdesk and audit sinks.

- [ ] **Step 7: Update status and operating documentation**

Document exact commands/results, configuration variables, required firewall destinations, disabled defaults, mock/live labels and remaining external blockers.

- [ ] **Step 8: Commit**

```bash
git add PROJECT_STATUS.md README.md .env.example docs/guides/admin.md
git commit -m "docs: record selective integration acceptance"
```

