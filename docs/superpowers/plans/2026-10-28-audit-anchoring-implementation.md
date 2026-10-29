# External Audit Anchoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Periodically publish and verify privacy-minimized proofs of JoltIQ's existing per-organization audit-chain heads in an independently controlled immutable store.

**Architecture:** Keep the current database hash chain as the source of truth. Add anchor publication records and a small sink protocol with file and S3-compatible implementations. A durable periodic worker verifies the local chain before publishing, writes an idempotent anchor object, reads it back and records health. The admin API/UI report local-chain and external-anchor integrity separately.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL RLS, existing durable jobs, boto3, filesystem fsync, Next.js/TypeScript, pytest, S3-compatible test service.

**Spec:** `docs/superpowers/specs/2026-10-28-selective-integration-port-design.md`

## Global Constraints

- Do not change the current audit event hash algorithm or rewrite old events.
- An anchor contains only schema version, opaque encoded organization/chain ID, event ID/sequence, chain head hash and timestamp—never ticket text, email, user, device or IP content.
- External anchoring is disabled by default and failures do not stop product operation.
- Production credentials and bucket are deployment-owned, not tenant-supplied.
- Per-organization object prefixes prevent enumeration/collision.
- File sink is development/offline evidence only; it is not independently immutable.
- Azure Blob is explicitly later work, not silently emulated here.

## Review Focus

- No anchor is published when the local chain fails verification; covered in Task 2.
- Duplicate/retried workers create one logical anchor and detect conflicting existing content; covered in Tasks 2 and 3.
- S3 object-lock/versioning prerequisites are tested and visible; covered in Task 3.
- Sink outages preserve local audit writes and generate actionable health without leaking credentials; covered in Tasks 3 and 4.
- Cross-tenant keys and admin views cannot reveal another organization's event/head; covered in Tasks 1, 2 and 4.
- Disabled-default operation produces zero S3/filesystem anchor writes; covered in Task 5.

---

### Task 1: Add durable anchor publication records

**Files:**
- Create: `apps/api/alembic/versions/0011_audit_anchors.py`
- Modify: `apps/api/joltiq/models/platform.py`
- Modify: `apps/api/joltiq/models/__init__.py`
- Create: `apps/api/tests/test_audit_anchor_models.py`

**Interfaces:**
- Produces: `AuditAnchor` with `org_id`, `audit_event_id`, `sequence`, `head_hash`, `sink_kind`, `object_key`, `payload_hash`, `status`, `attempts`, `published_at`, `verified_at`, `last_error_code`.
- Constraints: unique `(org_id, sink_kind, audit_event_id, head_hash)` and unique `(sink_kind, object_key)`; tenant RLS; errors are bounded codes, not raw exception/credential text.

- [ ] **Step 1: Write failing migration/model tests**

Migrate existing chained audit data from the prior migration head. Assert event rows/hashes are byte-for-byte unchanged, anchor table starts empty, RLS blocks cross-org rows, uniqueness is enforced and old chain verification still passes.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_models.py`

Expected: fail because migration/model does not exist.

- [ ] **Step 3: Implement the additive schema**

Use an enum/check constraint for pending/published/verified/failed/conflict. Add indexes for org health, pending retry and latest verified anchor. Never add external-storage fields to existing audit events.

- [ ] **Step 4: Run migration and current audit tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_models.py tests/test_tenancy_rls.py tests/test_security.py`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/alembic/versions/0011_audit_anchors.py apps/api/joltiq/models/platform.py apps/api/joltiq/models/__init__.py apps/api/tests/test_audit_anchor_models.py
git commit -m "feat(audit): add external anchor records"
```

### Task 2: Define canonical anchor payloads and sink protocol

**Files:**
- Create: `apps/api/joltiq/services/audit_anchor.py`
- Create: `apps/api/tests/test_audit_anchor_service.py`

**Interfaces:**
- `AuditAnchorPayload` fields: `version`, `chain_id`, `event_id`, `sequence`, `head_hash`, `anchored_at`.
- `build_anchor_payload(org_id: UUID, event: AuditEvent, now: datetime, chain_key: bytes) -> tuple[bytes, str, str]` returns canonical JSON bytes, SHA-256 payload hash and deterministic object key.
- `AnchorSink.put_if_absent(key: str, payload: bytes, payload_hash: str) -> PutResult`
- `AnchorSink.get(key: str) -> bytes`
- `AnchorSink.check_prerequisites() -> SinkHealth`
- `publish_latest_anchor(db, org_id, sink, now) -> AuditAnchor`
- `verify_external_anchor(db, anchor, sink) -> VerificationResult`

- [ ] **Step 1: Write failing canonicalization/privacy tests**

Use golden byte fixtures for stable JSON and deterministic keys. Assert randomized opaque chain ID, per-org prefix isolation and absence of known user email, ticket text, device ID and audit detail fields. Assert any existing event-chain failure prevents `put_if_absent`.

- [ ] **Step 2: Write failing idempotency/verification tests with an in-memory sink**

Cover empty chain, first publish, identical replay, conflicting pre-existing object, lost response after successful put, read-back mismatch, database head advanced after anchor and cross-org anchor access.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_service.py`

Expected: fail because the service/protocol does not exist.

- [ ] **Step 4: Implement canonical payload and sink-independent workflow**

Call existing `audit.verify_chain` before selecting the latest event. Use a deployment secret/HMAC only to derive a stable opaque chain ID—never as a substitute for the event hash. Insert/lock the `AuditAnchor`, put idempotently, read back, compare exact bytes/hash, then mark verified.

- [ ] **Step 5: Run focused tests and static checks**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_service.py && .venv/bin/ruff check joltiq/services/audit_anchor.py tests/test_audit_anchor_service.py && .venv/bin/mypy joltiq/services/audit_anchor.py`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/audit_anchor.py apps/api/tests/test_audit_anchor_service.py
git commit -m "feat(audit): define canonical anchor workflow"
```

### Task 3: Implement file and immutable S3-compatible sinks

**Files:**
- Modify: `apps/api/joltiq/services/audit_anchor.py`
- Modify: `apps/api/joltiq/config.py`
- Modify: `apps/api/requirements.lock`
- Modify: `.env.example`
- Create: `apps/api/tests/test_audit_anchor_sinks.py`
- Create: `apps/api/tests/integration/test_audit_anchor_s3.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- `FileAnchorSink(path: Path)` creates parent/file mode `0700/0600`, refuses symlinks/traversal, appends one canonical JSONL record under an exclusive lock, flushes and calls `fsync`.
- `S3AnchorSink(client, bucket: str, prefix: str, retention_days: int)` uses conditional create or exact existing-object verification and server-side encryption.
- Settings: `audit_anchor_sink`, `audit_anchor_interval_seconds`, `audit_anchor_chain_key`, `audit_anchor_file_root`, `audit_anchor_s3_endpoint`, `...bucket`, `...region`, `...access_key`, `...secret_key`, `...retention_days`.

- [ ] **Step 1: Write failing file-sink safety tests**

Cover mode, append locking, fsync call, duplicate equality, conflicting duplicate key, symlink/traversal refusal, truncated final-record recovery and concurrent writers.

- [ ] **Step 2: Write failing S3 contract tests**

Against a disposable local S3-compatible service, cover bucket missing, versioning off, object-lock/retention unavailable, first put, duplicate, conflict, SSE headers, per-org prefix, read-back, timeout, access denied and credential redaction.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_sinks.py`

Expected: fail because sink implementations do not exist.

- [ ] **Step 4: Implement file sink with honest classification**

Use an exclusive file lock, strict permissions, canonical one-record append and fsync. Scan existing records to make `put_if_absent` idempotent; quarantine/refuse malformed non-final records and safely ignore only a demonstrably truncated final record. Return health label `local-integrity-only`, never `immutable` or `independent`.

- [ ] **Step 5: Implement S3 sink and prerequisite probe**

Pin boto3/botocore in `requirements.lock`. On startup/test, verify bucket versioning and object-lock/retention configuration before calling the sink production-ready. Do not create/alter a customer bucket automatically. Use deployment credentials and redact all SDK errors.

- [ ] **Step 6: Run sink tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_sinks.py`

Run with test S3 service: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/integration/test_audit_anchor_s3.py`

Expected: pass; integration test may be explicitly skipped only when the labelled test profile is not running.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/services/audit_anchor.py apps/api/joltiq/config.py apps/api/requirements.lock .env.example apps/api/tests/test_audit_anchor_sinks.py apps/api/tests/integration/test_audit_anchor_s3.py docker-compose.yml
git commit -m "feat(audit): add file and immutable S3 anchor sinks"
```

### Task 4: Schedule anchoring and expose safe health/operations

**Files:**
- Modify: `apps/api/joltiq/worker.py`
- Modify: `apps/api/joltiq/services/audit_anchor.py`
- Modify: `apps/api/joltiq/routers/admin.py`
- Modify: `apps/api/joltiq/cli.py`
- Create: `apps/api/tests/test_audit_anchor_jobs.py`
- Create: `apps/api/tests/test_audit_anchor_api.py`

**Interfaces:**
- Durable handlers: `audit.anchor.publish` and `audit.anchor.verify`.
- Scheduler enqueues at most one due publication per organization/sink/head.
- `GET /admin/audit/anchors` returns current-org latest publication/verification and redacted sink health.
- `POST /admin/audit/anchors/publish` requires org admin + MFA and enqueues, not performs, a publish.
- CLI: `joltiq audit-anchor publish --org ORG_ID`, `verify --org ORG_ID`, `check-sink`.

- [ ] **Step 1: Write failing scheduler/job tests**

Cover disabled setting, interval due/not-due, two workers racing, local-chain corruption, sink outage, retry/backoff, process crash after put, duplicate delivery, new audit event after anchor and one organization failing while another succeeds.

- [ ] **Step 2: Write failing API/CLI permission and redaction tests**

Cover org isolation, admin+MFA gate, auditor read-only access, manual enqueue, disabled state, file/S3 health labels and absence of credentials/object contents/raw SDK messages.

- [ ] **Step 3: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_jobs.py tests/test_audit_anchor_api.py`

Expected: fail because handlers/routes/commands do not exist.

- [ ] **Step 4: Implement worker scheduling and handlers**

Use current `@handler` and enqueue facilities. Keep anchor jobs low priority, bounded and independent of ticket jobs. Retry transient sink failures; mark prerequisite/configuration/conflict failures terminal and visible. Never retry local chain corruption as a network error.

- [ ] **Step 5: Implement API and CLI**

Reuse current audit permissions and chain verification. Manual publish returns a job ID immediately. CLI output includes event/sequence/hash prefixes and classification, not customer content.

- [ ] **Step 6: Run focused tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_audit_anchor_jobs.py tests/test_audit_anchor_api.py tests/test_security.py`

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/worker.py apps/api/joltiq/services/audit_anchor.py apps/api/joltiq/routers/admin.py apps/api/joltiq/cli.py apps/api/tests/test_audit_anchor_jobs.py apps/api/tests/test_audit_anchor_api.py
git commit -m "feat(audit): schedule and operate external anchors"
```

### Task 5: Add external-anchor health UI

**Files:**
- Modify: `apps/web/src/app/(app)/admin/audit/page.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/e2e/audit-anchor.spec.ts`

**Interfaces:**
- Existing audit page adds separate cards for `Local chain integrity` and `External anchor`.
- Displays sink classification, last anchored sequence/time, last verified time, lag, prerequisite/error code and manual publish action.
- File sink explicitly reads `Local integrity copy — not independently immutable`.

- [ ] **Step 1: Write failing UI tests**

Cover disabled, healthy S3, unhealthy prerequisites, file classification, lag, chain-corrupt blocking, manual publish pending/success/failure and no credential/object-content exposure.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/web && npx playwright test e2e/audit-anchor.spec.ts`

Expected: fail because the UI is absent.

- [ ] **Step 3: Implement the minimal additive UI**

Preserve current chain-integrity display and terminology. Do not combine local-chain and external-store status into a single green badge.

- [ ] **Step 4: Run web checks**

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add 'apps/web/src/app/(app)/admin/audit/page.tsx' apps/web/src/lib/api.ts apps/web/e2e/audit-anchor.spec.ts
git commit -m "feat(web): show external audit anchor health"
```

### Task 6: Audit-anchor acceptance and recovery documentation

**Files:**
- Modify: `docs/guides/admin.md`
- Create: `docs/evidence/audit-anchor-acceptance.md`
- Modify: `README.md`

**Interfaces:**
- Produces: setup, verification, restore/disaster-check and evidence runbook with accurate assurance labels.

- [ ] **Step 1: Run complete API and static suites**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq`

Expected: pass except explicit live-service skips.

- [ ] **Step 2: Run web verification**

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: pass.

- [ ] **Step 3: Run acceptance matrix**

Record disabled-default, file, local S3-compatible, duplicate, conflict, outage, local corruption, external tamper, cross-tenant and recovery-readback cases. Confirm disabled configuration creates zero anchor writes.

- [ ] **Step 4: Run production-shaped immutable-store acceptance only when supplied**

Use a deployment-owned bucket with versioning, object lock and retention enabled; save redacted bucket configuration, object versions/retention and verification results. If unavailable, label `BLOCKED — immutable external store not supplied`; local S3/file tests are not production immutability proof.

- [ ] **Step 5: Document operations and classifications**

Include credential rotation, bucket prerequisite checklist, restore verification, lag alerts, local-chain corruption response, privacy schema and explicit `Azure Blob: not implemented` note.

- [ ] **Step 6: Commit**

```bash
git add docs/guides/admin.md docs/evidence/audit-anchor-acceptance.md README.md
git commit -m "docs: record external audit anchor acceptance"
```
