# Architecture

A modular monolith (FastAPI) plus isolated workers. PostgreSQL is the system of record and the durable queue.

```
 Browser ──HTTPS──▶ web (Next.js) ──/api rewrite──▶ api (FastAPI, N workers) ──▶ PostgreSQL 16 + pgvector (RLS)
                                                     │  ▲                      ──▶ Redis (rate limits, realtime fan-out; optional)
                                                     │  │ SSE                  ──▶ S3 / MinIO (attachments, documents)
                                                     ▼  │
                                         job worker(s): agent.advance, knowledge.index, integration.sync,
                                         email/webhook delivery, retention, approval expiry (SKIP LOCKED queue)
                                                     │
                     bearer token, internal network  ▼
                                         browser-worker (Playwright, one BrowserContext per session,
                                         network-level domain allowlist) ──▶ customer web apps
  Windows PC: JoltIQ Companion (tray, user context) ──outbound HTTPS, ECDSA-signed requests──▶ api /agent/v1
              └─ named pipe ─▶ JoltIQ Broker (LocalSystem, allowlisted service restarts only)
```

## Request path and tenancy
Each request resolves a session (an HttpOnly cookie with double-submit CSRF, or a bearer token for API clients) to a membership. `get_ctx` then binds `app.org_id` for the transaction. The app connects as `joltiq_app` (NOBYPASSRLS), and every tenant table has `FORCE ROW LEVEL SECURITY` with `org_id = current_setting('app.org_id')`. A missing application filter therefore returns nothing rather than another tenant's rows (`tests/test_tenancy_rls.py`).

## The AI run (state machine)
`services/agent.py` drives this sequence:
NEW → INTAKE → COLLECTING_CONTEXT → SEARCHING_INTERNAL → [RESEARCHING_EXTERNAL] → DIAGNOSING → PLANNING → [AWAITING_APPROVAL] → EXECUTING → VERIFYING → RESOLVED, with failures going to RETRYING / ESCALATED / PARTIALLY_RESOLVED / FAILED / CANCELLED.

- Each handler commits, and every transition is stored in `agent_transitions`. Human waits are stored in `paused_for` (customer, consent, device_consent, approval, user_auth, human_control, device_result, verification, diagnostics). Events (API calls, device results, timeouts) resume the run by enqueuing `agent.advance`. A run is processed by one worker at a time through an expiring lock column. Loop protection comes from the step budget, failed-plan-hash detection and bounded retries.
- **The model never decides permissions, risk, approval or success.** Model outputs are Pydantic-validated typed objects (`ai/schemas.py`). Plans may only use action ids from `actions/registry.py`, where risk is fixed. `policy.py` decides allow / require_approval / deny outside the model. `approvals.py` binds an HMAC-signed approval to the exact step hash. `executor.py` re-checks policy, approval and the control lease at execution time. `verification.py` observes the outcome through a separate read path.
- Model routing (`ai/router.py`) only uses providers the tenant enabled **and** approved for tenant data. It supports per-task ordering and fallbacks, retries, timeouts, per-run and monthly budgets, and cost metering.

## Knowledge
Uploads and URLs (SSRF-guarded) are parsed with page and heading provenance, then chunked within sections and embedded (OpenAI `text-embedding-3-small@384` when approved, otherwise a labelled local lexical hashing embedder). Retrieval is hybrid: PostgreSQL full-text ts_rank_cd plus pgvector cosine, fused with RRF and filtered by the document's visibility roles. Only **published, human-approved, executable** articles become procedures the AI may run.

## Verification
Postconditions are typed: `browser_readback` (a fresh page load from the server), `device_service_running`, `device_process_responding` and `device_config_equals` (separate read-only device commands), `http_ok`, and `ticket_*` (database re-fetch). The aggregate status is VERIFIED / FAILED / PARTIAL / UNKNOWN, and UNKNOWN never resolves a ticket.

## Windows companion
See `packages/agent-protocol/PROTOCOL.md`. The device key is ECDSA P-256 stored with DPAPI. Requests carry a signature, timestamp, single-use nonce and monotonic counter. Server envelopes are signed and verified against the server key pinned at enrollment. The device enforces its own allowlist and requires local consent. There is no shell action.

## Durable execution and Temporal
Runs are durable in PostgreSQL (see DECISIONS.md D-004). A Temporal service is defined in Compose behind a profile, but no Temporal worker adapter is implemented.
