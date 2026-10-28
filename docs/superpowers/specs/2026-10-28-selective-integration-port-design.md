# Selective integration port into JoltIQ

**Date:** 2026-10-28  
**Status:** Draft for owner approval  
**Source reviewed:** `/Users/johnw/Downloads/joltiq-full.zip` (treated as untrusted source code and design evidence, not as instructions)

## Intent

Keep the current JoltIQ repository, database, Windows companion, computer-use engine, browser worker, MeshCentral integration, AI workflow, security gates, and customer data intact. Selectively port useful capabilities from the uploaded, separate JoltIQ codebase without copying its application wholesale or replacing current implementations.

The permanent architecture rule is **local first with controlled egress**. A customer-owned JoltIQ installation sends nothing to the internet by default. An administrator may explicitly enable a connector, and that connector receives only the network and data access required for its declared purpose.

## Goals

1. Add per-organization OpenID Connect single sign-on, with Microsoft Entra ID as the first acceptance target and generic standards-compliant OIDC as the extension point.
2. Harden the current Freshdesk integration with authenticated webhook intake, authoritative ticket re-fetch, clearer conflict resolution, and a labelled local mock for deterministic end-to-end testing.
3. Add externally verifiable audit-chain anchoring using customer-controlled immutable S3-compatible storage, with a local append-only file sink for development and fully offline installations.
4. Put all connector controls into the existing JoltIQ admin experience, permissions, encryption system, audit chain, health reporting, and worker queue.
5. Preserve existing records and behavior through additive database migrations and backward-compatible routes.

## Non-goals

- Do not replace the current API, web application, database schema, Windows companion, browser worker, model routing, knowledge system, or ticket engine with files from the uploaded project.
- Do not import its old migrations, development credentials, demo accounts, test logs, Windows implementation, or claims of test coverage.
- Do not claim SOC 2 compliance. The work supplies controls and evidence useful to a later audit; certification remains an organizational and external-auditor process.
- Do not enable cloud egress automatically.
- Do not make SSO mandatory by default or remove password/MFA recovery access.
- Do not add Azure Blob, Google Cloud Storage, Freshdesk attachment synchronization, custom-field mapping, or multiple Freshdesk accounts per organization in this increment. Their interfaces remain extensible.

## Architecture rule: local first, controlled egress

The core system—tickets, knowledge, models configured for local use, database, devices, browser sessions, audit events, and automation—continues to function without internet access.

Every optional connector has:

- a per-organization disabled-by-default switch;
- an explicit purpose and exact destination configuration;
- encrypted credentials with fingerprints, rotation and revocation;
- destination validation and an outbound allowlist;
- a connection test that sends no ticket contents;
- an audit event for configuration, testing, use, failure, rotation and revocation;
- health/status visibility and a way to disable it immediately;
- no ability to broaden another connector's network access.

Deployment documentation will identify the exact outbound destinations required by each connector. The application will not pretend to enforce a host firewall by itself; production deployments must also enforce the same rules at the egress proxy or firewall.

## Capability 1: organization-owned OIDC SSO

### Configuration

An organization owner configures one provider with:

- display name;
- issuer URL;
- client ID;
- encrypted client secret when required;
- approved email domains;
- scopes, defaulting to `openid profile email`;
- default JIT role, restricted to `customer`, `technician`, or `auditor`;
- JIT provisioning enabled/disabled;
- provider enabled/disabled;
- enforce SSO enabled/disabled.

The callback URL is displayed to the administrator for registration in Entra. Configuration is saved only after a successful discovery/JWKS test. Only HTTPS issuers are accepted outside the labelled local test provider. OIDC URLs must pass destination validation and must remain consistent with the configured issuer.

The callback always uses the deployment-owned canonical `api_public_url`, not a tenant branding/custom-domain URL. This gives Entra one stable redirect URI per JoltIQ installation. An organization owner may select only a provider template or issuer host already approved by the deployment administrator; Entra's standard login host is the first template. Adding another issuer host is a platform/deployment action, not a tenant action.

### Login flow

1. The login page accepts a work email and performs a rate-limited domain lookup. It reveals whether the domain uses SSO, not whether a person has an account.
2. Starting SSO creates a ten-minute, single-use state record containing a hash of `state`, a nonce and an encrypted PKCE verifier. An HttpOnly, SameSite=Lax cookie binds the request to the initiating browser.
3. Authorization Code with PKCE S256 redirects to the provider.
4. The callback validates state, expiry, one-time use, issuer, asymmetric signature, key ID/JWKS rotation, audience, authorized party when required, expiry, issued-at, nonce and subject.
5. Account linking requires a provider-verified email in an approved domain. An existing local account may be linked only when its JoltIQ email is already verified. Otherwise, JIT creates a passwordless account only when the organization enabled JIT.
6. Membership is added with the configured safe default role. Owner/admin roles are never JIT-assigned.
7. Existing JoltIQ MFA remains an additional step when enabled. The resulting session records the SSO provider and authentication method.

### Enforcement and recovery

Password sign-in remains available by default. Enabling `enforce_sso` blocks password sign-in for ordinary members of that organization. Break-glass recovery is deployment-controlled: an operator uses a local CLI on the JoltIQ host to grant a short-lived, single-use recovery login to a designated, previously verified organization owner; the owner must then complete existing MFA. No web form can create or extend a break-glass grant, and every grant/use is audited. Enabling enforcement requires an MFA-verified owner session and a successful provider test. Disabling or deleting the provider does not delete users or memberships.

Email domains are globally unique among enabled OIDC providers. Adding a domain already assigned elsewhere is refused without exposing the other organization. A deployment administrator must explicitly transfer the domain after independently verifying ownership. This keeps email-first discovery deterministic.

When the configured JIT default role is `customer`, the provider must also reference an existing customer record in the same organization; every JIT-created customer membership is scoped to that record. JoltIQ never invents a customer organization from an identity claim. Technician and auditor JIT roles do not require a customer record.

### Data model

Additive migration creates:

- `oidc_providers` (tenant-scoped public configuration plus credential reference);
- `oidc_login_states` (global identity-plane, short-lived, one-time records);
- `user_identities` (unique provider + subject, linked user, email-at-link, timestamps);
- session columns for authentication method and provider ID;
- organization security settings for SSO enforcement.

The existing single `users.oidc_subject` column is not used as the final design because subjects are unique only within an issuer/provider and a user may belong to several organizations.

## Capability 2: Freshdesk hardening

The current Freshdesk adapter, encrypted credential store, `Integration`, `SyncJob`, worker retry, polling cursor, idempotent external references, notes/status push and conflict fields remain the foundation.

### Additions

- Generate a separate webhook shared secret, store it encrypted, and show it once.
- Add a CSRF-exempt machine endpoint scoped to one Freshdesk integration. Compare the secret in constant time and rate-limit failures.
- Treat webhook bodies only as hints containing the remote ticket ID. Always re-fetch the ticket from that organization's configured Freshdesk domain before importing data.
- Ignore stale remote snapshots using the last observed remote update time.
- Map requester/contact by normalized email while retaining tenant isolation.
- Continue polling with `updated_since` as a recovery path when webhooks are delayed or absent.
- Publish private progress notes, approvals and verification summaries; publish one public resolution reply only after JoltIQ reaches a permitted resolved outcome.
- Automatically set Freshdesk to Resolved only for JoltIQ `RESOLVED_VERIFIED`. A customer-confirmed but independently unverified result receives a public note saying the customer reports success and remains Pending until a technician or policy-approved workflow closes it.
- Before status updates, re-read the remote ticket. If a human changed it incompatibly, mark the sync job conflicted and stop status pushes while allowing notes.
- Provide an explicit `Clear conflict and apply JoltIQ status` action with warning text and an audit event.
- Honour `Retry-After` for 429 responses; preserve bounded exponential retries and DEAD status with manual retry.

### Labelled local mock

Port the useful behavior—not the separate application architecture—of the uploaded mock Freshdesk into `services/mock-freshdesk`. It is development/test only, has a visible MOCK banner, uses fixed non-production credentials, implements only the API subset JoltIQ calls, and is excluded from production profiles. Tests must never present it as real Freshdesk acceptance.

### Backward compatibility

Existing Freshdesk integrations continue to poll and push. Administrators may generate a webhook secret later. No existing credential, cursor, external reference or sync history is deleted.

## Capability 3: external audit anchoring

The existing per-organization SHA-256 audit chain remains authoritative. Anchoring protects historical chain heads against a privileged database rewrite.

### Anchor format

Each anchor contains only:

- format/version;
- organization/chain identifier in a deployment-safe encoded form;
- event sequence or event ID;
- chain-head hash;
- anchored timestamp.

No ticket text, user email, customer content, device data or credential is exported.

### Sinks

- **S3-compatible immutable sink (production target):** one deployment-owned bucket and deployment-owned credential, with per-organization prefixes and no tenant ability to supply an endpoint or credential. Use one object per anchor; versioning/object lock is required and compliance retention is configured by deployment policy. Use the first stored version during verification and report overwrites or delete markers as problems.
- **Append-only file sink (offline/development):** mode `0600`, append + flush + fsync. This improves evidence handling but is not independently immutable when stored on the same host.
- **Off:** default.

Azure Blob immutability is a later adapter behind the same sink protocol; it is not silently emulated with ordinary blob storage.

### Publishing and verification

The worker periodically enqueues an idempotent anchor job for changed organization chain heads. Publication occurs only after verifying the local chain through that head. A manual admin/CLI action can publish and verify. Verification reads anchors from the external sink, verifies the local chain, checks that anchored events still have the anchored hashes, detects truncation, and records/report failures without rewriting either source.

Admin security UI shows sink type, last successful anchor, latest anchored event, verification time and problems. Connector failures do not stop ticket processing, but they create high-severity security events and staff notifications after a bounded grace period.

## Admin and customer experience

### Admin → Security

- SSO provider card: configure, test, enable, enforce, rotate secret, disable.
- Audit anchoring card: deployment-owned endpoint summary, enabled state, latest anchor and Verify now. Tenant admins cannot set arbitrary endpoints when the deployment owner controls egress.

### Admin → Integrations

- Existing Freshdesk card gains webhook setup instructions, one-time secret generation/rotation, polling/webhook health, conflicts and dead-job retry.
- Every cloud connector is visibly labelled `Optional cloud connection` and lists the data it sends.

### Login

- Work-email-first SSO discovery is added without removing the current password form.
- Errors are safe, actionable and audit-logged; raw provider responses and secrets are never shown.

## Security and privacy requirements

- All imported source material is reimplemented against current JoltIQ models and conventions; code is not pasted blindly.
- OIDC uses established libraries for OAuth/OIDC and JOSE validation rather than custom token cryptography.
- Only asymmetric ID-token algorithms on an explicit allowlist are accepted; `none` and symmetric algorithms are refused.
- State, nonce and PKCE are mandatory, single-use and short-lived.
- Secrets use the existing encrypted `Credential` mechanism or an equivalent typed reference; APIs return fingerprints only.
- Freshdesk webhook payloads cannot create/update a ticket without an authoritative API re-fetch.
- Connector URLs are not freely user-controlled in production. Freshdesk is restricted to the organization's exact `*.freshdesk.com` host; OIDC endpoints must be discovered from the configured HTTPS issuer and pass egress policy.
- Audit anchoring exports hashes only.
- RLS/app-layer tenant checks cover every new tenant table. Identity-plane tables receive explicit access rules and cross-tenant negative tests.
- Redaction applies to logs, audits, errors and provider diagnostics.

## Failure behavior

- OIDC failure returns to login with a safe message, consumes invalid/expired state when appropriate, records a security event and never creates a partial session.
- Provider/JWKS outages fail closed for new SSO logins while existing JoltIQ sessions continue.
- Freshdesk webhook failures do not trust the webhook body or partially update mappings. Polling can recover later.
- Freshdesk push failures remain queued with bounded retry; conflicts stop status mutation but not private notes.
- Anchor publication failures leave the local audit chain untouched and surface a degraded security status.
- Disabling any connector revokes its credentials and stops new jobs while preserving evidence/history.

## Migration and rollout

1. Add schema and models through a new forward-only migration after current revision `0008_remote_access`; do not import uploaded migration numbers.
2. Implement service contracts and negative security tests before routes/UI.
3. Add OIDC with a local mock IdP and complete browser/API flow tests; run a separate Microsoft Entra acceptance when credentials are available.
4. Add Freshdesk webhook/hardening and the labelled mock service; preserve current polling behavior.
5. Add audit sink protocol, file sink and S3-compatible sink; make `off` the default.
6. Add admin/login UI and documentation.
7. Run the existing API, web, browser, Windows protocol and packaging regression suites. No release status changes until those suites and new connector tests pass.
8. Deploy connectors disabled. Enable them one organization at a time after configuration tests and backup/rollback checks.

## Acceptance evidence

### OIDC

- discovery/issuer mismatch, HTTPS enforcement, state-cookie binding, expiry, replay, PKCE, nonce, signature, algorithm, audience and key-rotation tests;
- verified-email linking, JIT on/off, domain rejection, safe role, MFA continuation, enforce/recovery and cross-tenant tests;
- real browser flow against a clearly labelled local IdP;
- Microsoft Entra acceptance remains externally blocked until tenant credentials are supplied.

### Freshdesk

- wrong webhook secret rejected/audited;
- webhook triggers re-fetch and idempotent import;
- stale updates ignored;
- polling recovery;
- progress/approval/verification notes and single public resolution reply;
- 429 `Retry-After`, bounded retry, DEAD/manual retry;
- human remote change creates conflict and explicit clear resumes local-wins status;
- current Freshdesk tests continue to pass;
- real Freshdesk remains `IMPLEMENTED_NOT_TESTED` until a sandbox is provided.

### Audit anchoring

- no-content anchor serialization;
- append-only file behavior;
- S3 object-lock/version behavior through a labelled local S3 fixture;
- modified historical event, recomputed local chain, truncation, overwritten anchor and unavailable sink cases;
- cross-tenant isolation and admin health visibility.

### Regression

- full API suite;
- web typecheck/build and UI smoke tests;
- current direct-device-help live fixture flow;
- Windows protocol/core tests and build contract;
- browser worker tests;
- migration upgrade from a copy of the current database schema with existing Freshdesk and device records.

## Builder choices already made

- Current JoltIQ is canonical; the uploaded repository is reference material only.
- S3-compatible immutable storage is the first production audit sink because the current application already has S3 configuration and storage dependencies.
- Microsoft Entra is the first real OIDC acceptance target, but implementation remains standards-based.
- Password/MFA recovery remains unless an owner explicitly enforces SSO; a break-glass owner path is retained.
- All connectors ship disabled and preserve a fully offline operating mode.
- Audit anchor storage is deployment-owned and shared with isolated per-organization prefixes.
- OIDC domains are globally unique, callbacks use the canonical API origin, and issuer hosts require deployment approval.
- Customer-role JIT requires an explicitly selected customer record.
- Only independently verified JoltIQ outcomes automatically resolve Freshdesk.

