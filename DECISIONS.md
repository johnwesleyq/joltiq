# Decisions

| ID | Decision | Why | Revisit when |
|---|---|---|---|
| D-001 | Product name is config (`JOLTIQ_PRODUCT_NAME`, per-tenant `branding.product_name`). Renamed from "Resolviq" to **JoltIQ** at the owner's request. | White-label requirement. | — |
| D-002 | Modular monolith + isolated browser worker; one API image also runs the job worker. | Fewer moving parts; browser isolation is the boundary that matters. | Independent scaling needs. |
| D-003 | Tenant isolation is enforced by PostgreSQL RLS **and** app-layer scoping. The app role cannot bypass RLS. The audit table is INSERT/SELECT only for the app. | Defence in depth against a missed filter. | — |
| D-004 | **Durable workflow state lives in PostgreSQL** (state machine, transitions, `paused_for`, SKIP LOCKED job queue with leases, retries and dead letters) instead of Temporal. | Temporal images and the SDK couldn't be pulled or tested in the build sandbox. Postgres durability is tested here (worker crash → lease reclaim). Temporal is defined in Compose (profile) but the adapter is **not implemented**. | Before multi-region or very long-running workflows. |
| D-005 | Deterministic **local test model** and **local search fixture** for dev/CI when no credentials exist. Both are labelled `live=false` in the data and the UI, and refused in production. The fixture planner only runs human-published procedures. | Lets the full workflow be tested end to end without pretending to be AI. | — |
| D-006 | Local embedder = signed feature hashing (lexical/fuzzy), labelled non-semantic. Semantic embeddings come from OpenAI when the tenant approves it. | No local model weights available offline; no data egress by default. | Add a local ONNX sentence model. |
| D-007 | External web fixes are **never** auto-executed. They escalate unless a human authors a published, executable procedure. Forum/web text is screened for injection and commands across the full page. | Prompt-injection and safety requirement. | — |
| D-008 | Approvals bind to `sha256(action, target, params, plan_hash)` and are HMAC-signed. Any step change supersedes them, and they are consumed once. | Approval-bypass resistance. | — |
| D-009 | Windows companion: user-context tray app plus a separate LocalSystem broker that exposes only allowlisted service restarts over a pipe ACL'd to interactive users. | No UAC bypass or broad privilege in the main process. | — |
| D-010 | Server → device envelopes carry the exact signed bytes (`payload`, base64). | Avoids JSON canonicalization drift between Python and .NET. | — |
| D-011 | Real ticketing adapter = **Freshdesk v2** (Basic `api_key:X`; `/api/v2/agents/me`, `/tickets`, `/tickets/{id}/notes`, `PUT /tickets/{id}`). Other PSAs are listed as extension points and return HTTP 501. | Small, well-documented API. | — |
| D-012 | Hypothetical prices ($299 / $799 / $1,999) are placeholders and labelled unvalidated. Stripe live mode needs an explicit flag. | The brief marked these as unvalidated. | Pricing research. |
| D-013 | Real-time updates use SSE (`/api/v1/events/stream`) with Redis pub/sub across API processes, delivered only after commit. The live browser viewer polls masked JPEG frames (~1/s). | Simple and proxy-friendly. Frame polling avoids streaming unmasked content. | Use WebRTC if latency matters. |
| D-014 | Next.js proxies `/api` same-origin (rewrite compiled at build). The session cookie is first-party Lax/HttpOnly, so no CORS credentials are needed. | Simpler CSRF story. | — |
