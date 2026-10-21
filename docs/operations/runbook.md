# Operations runbook

## Start / stop
- Compose: `docker compose up -d --build`, `docker compose logs -f api worker browser-worker`, `docker compose down`.
- Kubernetes: `kubectl apply -f infrastructure/deployment/k8s/joltiq.yaml` (replace `REPLACE_TAG`; create `joltiq-secrets` first).

## Migrations
`alembic upgrade head` (as the DB owner), then `python -m joltiq.cli set-app-password`. Migrations are forward-only in production. Test them on a restored snapshot first.

## Health
- `GET /healthz` (liveness) and `GET /readyz` (DB + migration revision, Redis) on the API.
- `GET /healthz` on the browser worker (session count, Chromium connected).
- Platform → Tenants & health: worker heartbeats, queue depth, dead-lettered jobs (retry button), browser worker, security events.
- `/metrics` for Prometheus; alert rules in `infrastructure/monitoring/alerts.yml`.

## Common incidents
| Symptom | Check | Action |
|---|---|---|
| Runs stuck "working…" | Platform health → workers | Start or scale `worker`. Expired run locks self-heal within 5 min. |
| Browser sessions fail to start | browser-worker health, `shm_size` | Scale the browser worker. Check node memory and `/dev/shm`. |
| Many `dead` jobs | Platform → Failed jobs | Fix the cause (credentials/SMTP), then Retry. |
| Device offline | Devices page "last seen" | Machine asleep, offline or app closed. The companion reconnects with backoff. |
| Suspected tampering | `GET /api/v1/audit/verify` per tenant, or `cli verify-audit ORG` | Preserve the DB snapshot and start incident response. |

## Windows Outlook PDF internal acceptance

This workflow is not approved for customer use until the evidence index says Windows VM acceptance and signing have passed.

1. Use authorized, disposable 64-bit Windows 10/11 VMs. Reset to a named clean snapshot before every required run.
2. Build on Windows with `pwsh apps/windows-agent/scripts/build.ps1 -Version <version>`. Treat an unsigned package as internal-only.
3. Run installer lifecycle verification, then the 56-case / 59-run acceptance catalog using `apps/windows-agent/scripts/test-on-windows.ps1` and `docs/operations/windows-outlook-pdf-acceptance.md`.
4. Keep evidence outside the repository under `%ProgramData%\JoltIQ\AcceptanceEvidence`. Finalize every run and require aggregate `windows-acceptance.json` to report `passed: true`.
5. If any action is stopped, disconnected, duplicated, expired, revoked, or partially applied, keep the ticket open/escalated and retain the journal, command, audit, registry, event-log, screen, timing, and final-state evidence. Never rerun a mutation to make the evidence pass.
6. A restored registry value is not active until Outlook restarts. If safe restart cannot complete, report `RESTORED_PENDING_RESTART`; never force-close Outlook through a save prompt.
7. A quiet or unreadable event log is `UNKNOWN`, not proof. `FIX_APPLIED_VERIFIED` and `RESOLVED_VERIFIED` are separate outcomes.

See `windows-outlook-pdf-evidence-index.md` for the current gate status and artifact hashes.

## Backups and restore
Managed PITR for PostgreSQL, plus daily logical dumps (`pg_dump -Fc`) to versioned, encrypted object storage. S3 bucket versioning on. Restore drill: restore into a new instance, run `alembic current`, run `/readyz`, then sample-verify audit chains.

## Rotating secrets
- Integration and model keys: Admin UI (rotate). Old ciphertext is revoked.
- `JOLTIQ_ENCRYPTION_KEY` rotation needs a re-encrypt job (not implemented yet). Plan a maintenance window.
- `JOLTIQ_AGENT_SIGNING_KEY_PEM` rotation requires companion re-enrollment (key is pinned at enrollment).

## Data retention / deletion
The daily `retention.apply` job deletes session events, verification evidence and notifications per tenant settings. Tenant deletion: suspend in Platform, export audit, then delete the organization row as DB owner (cascades), and delete the `s3://bucket/<org_id>/` prefix.
