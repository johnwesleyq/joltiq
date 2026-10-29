# Organization OIDC and Microsoft Entra Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each JoltIQ organization connect its own Microsoft Entra tenant for secure, organization-scoped SSO while preserving local authentication, MFA and offline operation.

**Architecture:** Add provider, identity, transient login-state and recovery-grant records beside the existing identity models. The API performs OIDC authorization-code flow with PKCE, validates every token locally, then creates the existing `AuthSession`; a separate TOTP challenge preserves JoltIQ MFA. Admin configuration and public login discovery are additive and disabled by default.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL RLS, httpx, PyJWT, cryptography, Next.js/TypeScript, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-selective-integration-port-design.md`

## Global Constraints

- Keep password login and all current session/MFA behavior working unless an organization explicitly enables and enforces SSO.
- Never choose a tenant from an unverified token claim, email suffix alone or callback query parameter.
- Use the deployment's configured `api_public_url` for callbacks; do not accept tenant-supplied callback origins.
- Encrypt client secrets and PKCE verifiers at rest; store state, challenge and recovery tokens only as hashes.
- Accept only configured asymmetric algorithms and deployment-approved issuer hosts.
- Do not expose whether an email, organization or provider exists during discovery.
- All new network access is disabled by default.

## Review Focus

- Cross-tenant domain, provider, state and subject collisions fail closed; covered in Tasks 1, 3 and 4.
- Expired/replayed state, nonce mismatch, PKCE mismatch, bad signature, unknown `kid`, issuer/audience/`azp` mismatch and clock-skew failures create no session; covered in Task 2.
- Existing verified-email linking is explicit and safe; customer-role JIT requires an existing customer mapping; covered in Task 4.
- Organization SSO enforcement cannot lock out every owner because recovery is short-lived, single-use, MFA-bound and CLI-issued; covered in Task 5.
- Existing MFA still gates privileged routes after SSO; covered in Tasks 4 and 5.
- A clean installation with SSO unset makes no discovery/JWKS/token calls; covered in Task 6.

---

### Task 1: Add organization-scoped identity storage

**Files:**
- Create: `apps/api/alembic/versions/0009_oidc_sso.py`
- Modify: `apps/api/joltiq/models/identity.py`
- Modify: `apps/api/joltiq/models/__init__.py`
- Create: `apps/api/tests/test_oidc_models.py`

**Interfaces:**
- Produces: `OidcProvider`, `OidcProviderDomain`, `UserIdentity`, `OidcLoginState`, `SsoMfaChallenge`, `SsoRecoveryGrant` SQLAlchemy models.
- Produces: `AuthSession.auth_method: str` and `AuthSession.oidc_provider_id: UUID | None`.
- Constraints: unique enabled email domain globally; unique `(provider_id, subject)`; unique `(org_id, user_id, provider_id)`; all transient records expire and are single-use.

- [ ] **Step 1: Write failing migration/model tests**

Test migration from `0008_remote_access` with existing users, sessions, devices and integrations. Assert rows survive, new provider tables are empty, `auth_method == "password"` for old sessions, RLS protects every tenant table, and a second enabled provider cannot claim a domain already enabled elsewhere.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_models.py`

Expected: fail because the migration and models do not exist.

- [ ] **Step 3: Implement the forward-only schema**

Add provider fields for organization, display name, issuer, client ID, encrypted client secret, scopes, role mapping, JIT/enforcement flags, enabled/tested timestamps and configuration fingerprint. Store each normalized approved domain in `OidcProviderDomain` with a database-level global uniqueness constraint. Add hashed state/challenge/recovery tokens, encrypted verifier, nonce, fixed redirect path, expiry/use timestamps and audit-safe metadata. Do not overload `User.oidc_subject`; retain it for compatibility but stop writing it.

- [ ] **Step 4: Add tenant policies and indexes**

Extend the migration's PostgreSQL RLS policy list and add indexes for enabled-domain lookup, state hash, challenge hash, recovery hash, and provider subject lookup. Use database constraints rather than application-only uniqueness.

- [ ] **Step 5: Run focused and migration tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_models.py tests/test_tenancy_rls.py`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/alembic/versions/0009_oidc_sso.py apps/api/joltiq/models/identity.py apps/api/joltiq/models/__init__.py apps/api/tests/test_oidc_models.py
git commit -m "feat(auth): add organization OIDC identity storage"
```

### Task 2: Implement the hardened OIDC protocol client

**Files:**
- Create: `apps/api/joltiq/services/oidc.py`
- Create: `apps/api/tests/test_oidc_protocol.py`
- Modify: `apps/api/joltiq/config.py`
- Modify: `apps/api/requirements.lock`
- Modify: `.env.example`

**Interfaces:**
- `OidcMetadata = TypedDict(...)` containing exact authorization, token, issuer and JWKS endpoints.
- `discover_provider(http: httpx.Client, issuer: str, allowed_hosts: set[str]) -> OidcMetadata`
- `build_authorization_request(provider: OidcProvider, login: OidcLoginState) -> str`
- `exchange_code(http: httpx.Client, provider: OidcProvider, login: OidcLoginState, code: str) -> dict[str, Any]`
- `validate_id_token(provider: OidcProvider, metadata: OidcMetadata, jwks: dict, token: str, nonce: str, now: datetime) -> ValidatedIdentity`
- Consumes: `settings.api_public_url`, `settings.oidc_allowed_issuer_hosts`, existing envelope encryption helpers.

- [ ] **Step 1: Write protocol failure tests with an injected `httpx.MockTransport`**

Cover HTTPS-only discovery, host allowlist, redirect rejection, metadata issuer equality, authorization-code + PKCE S256 parameters, client-secret POST, timeouts, non-JSON responses, token endpoint errors, asymmetric algorithm allowlist, unknown/rotated `kid`, signature, issuer, audience, `azp`, nonce, `exp`, `iat`, missing subject and unverified email.

- [ ] **Step 2: Confirm the tests fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_protocol.py`

Expected: fail because `services.oidc` does not exist.

- [ ] **Step 3: Implement discovery, PKCE, exchange and validation**

Use `httpx` with fixed connect/read timeouts and no ambient proxy/redirect behavior unless explicitly configured. Use PyJWT/JWK validation with `RS256` and `ES256` as the initial allowlist. Refresh JWKS once on unknown `kid`; never fall back to decoding without verification. Redact tokens and secrets from exceptions/logs.

- [ ] **Step 4: Pin every runtime dependency**

Prefer existing PyJWT/cryptography/httpx. If any new library is required, pin it in `requirements.lock`, rebuild the API image and update the dependency advisory evidence rather than relying on an unpinned Dockerfile install.

- [ ] **Step 5: Run tests and static checks**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_protocol.py && .venv/bin/ruff check joltiq/services/oidc.py tests/test_oidc_protocol.py && .venv/bin/mypy joltiq/services/oidc.py`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/services/oidc.py apps/api/tests/test_oidc_protocol.py apps/api/joltiq/config.py apps/api/requirements.lock .env.example
git commit -m "feat(auth): add hardened OIDC protocol client"
```

### Task 3: Add provider administration and safe domain discovery

**Files:**
- Create: `apps/api/joltiq/routers/security.py`
- Modify: `apps/api/joltiq/routers/__init__.py`
- Create: `apps/api/tests/test_oidc_admin.py`

**Interfaces:**
- `GET /admin/security/oidc` returns redacted provider configuration and fingerprint.
- `POST /admin/security/oidc/test` validates metadata/credentials without saving or enabling.
- `PUT /admin/security/oidc` saves only a successfully tested fingerprint and audits the change.
- `POST /admin/security/oidc/disable` disables login without deleting linked identities.
- `POST /auth/sso/lookup` accepts an email and always returns the same neutral response shape and comparable work factor; when eligible it includes a short-lived opaque continuation, never provider/org identifiers.

- [ ] **Step 1: Write failing permission, secrecy and tenant-isolation tests**

Cover org-admin + MFA requirement, non-admin rejection, secret write-only behavior, fingerprint invalidation on edits, blocked issuer host, domain collision, enable-without-test rejection, audit events, disabled lookup, unknown email, cross-org continuation replay and uniform public messaging.

- [ ] **Step 2: Confirm the tests fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_admin.py`

Expected: route-not-found failures.

- [ ] **Step 3: Implement redacted admin DTOs and provider test/save flow**

Reuse the current `Principal`/MFA dependencies and credential encryption. Store only encrypted secrets. Re-test discovery on enable, compare the exact fingerprint, and audit create/update/test/enable/disable/domain-transfer events without secrets.

- [ ] **Step 4: Implement opaque discovery continuations**

Hash the continuation at rest, bind it to the provider/domain and expiry, and return neutral results for all ineligible cases. Rate-limit by IP plus normalized email hash using the existing rate-limit facility.

- [ ] **Step 5: Run focused auth/security tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_admin.py tests/test_auth.py tests/test_security.py`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/joltiq/routers/security.py apps/api/joltiq/routers/__init__.py apps/api/tests/test_oidc_admin.py
git commit -m "feat(auth): add organization OIDC administration"
```

### Task 4: Complete SSO callback, identity linking and JIT rules

**Files:**
- Modify: `apps/api/joltiq/routers/security.py`
- Modify: `apps/api/joltiq/services/oidc.py`
- Modify: `apps/api/joltiq/services/auth.py`
- Create: `apps/api/tests/test_oidc_login.py`

**Interfaces:**
- `GET /auth/sso/start?continuation=...` creates hashed state, nonce and encrypted PKCE verifier, sets a SameSite=Lax HttpOnly state cookie, then redirects.
- `GET /auth/sso/callback?code=...&state=...` validates/consumes state and either creates the existing `AuthSession` or creates an `SsoMfaChallenge`.
- `POST /auth/sso/mfa` consumes `{code}` plus the HttpOnly challenge cookie and calls existing `verify_totp` before `create_session`.
- `resolve_identity(db, provider, claims) -> IdentityResolution` links by provider subject first, then an already-verified same-org email, then allowed JIT rules.
- Existing `create_session(...)` gains optional keyword-only `auth_method="password"`, `oidc_provider_id=None` and `ttl_seconds=None` without breaking callers.

- [ ] **Step 1: Write failing end-to-end API tests**

Cover happy path, single-use state, concurrent tabs, expired state, cookie/state mismatch, callback error response, verified-email link, unverified-email refusal, duplicate subject, user disabled, safe technician/auditor JIT, org-owner/admin JIT refusal, customer JIT with/without explicit existing `Customer` mapping, return-path allowlist and TOTP-required SSO.

- [ ] **Step 2: Confirm the tests fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_login.py`

Expected: fail because the flow is incomplete.

- [ ] **Step 3: Implement single-use state and exact callback binding**

Derive the callback solely from `api_public_url`; lock and mark state used in the same transaction that resolves identity. Clear cookies on every terminal success/failure. Redirect failures to a generic login error code, never raw provider details.

- [ ] **Step 4: Implement identity resolution and safe JIT**

Require `email_verified`; never change roles from claims after initial linking. Limit JIT roles to the configured safe set and require an existing customer record for customer role. Audit linking/JIT/refusal without copying full tokens.

- [ ] **Step 5: Preserve JoltIQ MFA**

If the resolved user has MFA enabled, create a five-minute one-time challenge rather than an authenticated session. Only successful existing TOTP verification calls `create_session(..., mfa_passed=True, auth_method="oidc", oidc_provider_id=...)`.

- [ ] **Step 6: Run focused and existing auth tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_oidc_login.py tests/test_auth.py tests/test_security.py`

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/routers/security.py apps/api/joltiq/services/oidc.py apps/api/joltiq/services/auth.py apps/api/tests/test_oidc_login.py
git commit -m "feat(auth): complete organization SSO login"
```

### Task 5: Add enforcement and audited break-glass recovery

**Files:**
- Modify: `apps/api/joltiq/routers/auth.py`
- Modify: `apps/api/joltiq/services/auth.py`
- Modify: `apps/api/joltiq/cli.py`
- Create: `apps/api/tests/test_sso_enforcement.py`

**Interfaces:**
- Password login checks the selected/default organization's enabled provider and rejects local login when `enforce_sso` is true; organization switching also rejects password/recovery sessions entering an enforced organization.
- `joltiq sso-recovery issue --org ORG_ID --email EMAIL --ttl-minutes 15` validates an existing verified org owner with MFA and prints a single-use recovery URL/token once.
- `POST /auth/sso/recovery` consumes recovery token + TOTP and creates an `auth_method="recovery"` session.
- Existing authenticated sessions remain valid when the identity provider is temporarily unavailable.

- [ ] **Step 1: Write failing enforcement and recovery tests**

Cover unenforced compatibility, enforced refusal, disabled provider fallback, password-session switch into an enforced org, OIDC-session switch, provider outage with an existing OIDC session, wrong-org token, non-owner, unverified owner, owner without MFA, expired/replayed grant, wrong TOTP, successful grant, audit trail and token redaction.

- [ ] **Step 2: Confirm failure**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_sso_enforcement.py`

Expected: fail because enforcement/recovery does not exist.

- [ ] **Step 3: Implement enforcement without changing password verification semantics**

Authenticate credentials first using constant-work behavior, resolve the existing default org, then return the same generic SSO-required response for enforced users. Apply the same auth-method gate in the existing switch-organization route so a password session cannot bypass enforcement. Do not auto-disable enforcement during provider failure.

- [ ] **Step 4: Implement CLI-only recovery issuance**

Require database/host access, verify owner membership, verified email and enabled MFA, store only the grant hash, default to 15 minutes, cap TTL at 60 minutes, and append audit/security events. Never add an admin-web endpoint for issuance.

- [ ] **Step 5: Implement recovery consumption**

Lock and consume the grant atomically with TOTP verification, create a short-lived existing session, label its auth method, notify the organization through current notification channels and require normal SSO for the next login.

- [ ] **Step 6: Run tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q tests/test_sso_enforcement.py tests/test_auth.py tests/test_security.py`

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/joltiq/routers/auth.py apps/api/joltiq/services/auth.py apps/api/joltiq/cli.py apps/api/tests/test_sso_enforcement.py
git commit -m "feat(auth): enforce SSO with audited recovery"
```

### Task 6: Add login and organization-security UI

**Files:**
- Modify: `apps/web/src/app/(auth)/login/page.tsx`
- Create: `apps/web/src/app/(app)/admin/security/page.tsx`
- Modify: `apps/web/src/app/(app)/admin/layout.tsx`
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/e2e/oidc.spec.ts`

**Interfaces:**
- Login page adds `Continue with work email`, generic error states and an SSO MFA step while retaining local email/password.
- Admin Security page edits/tests/enables/disables provider configuration and shows the canonical callback URL.
- UI never receives saved client-secret plaintext or raw OIDC tokens.

- [ ] **Step 1: Write failing UI tests**

Cover local-login compatibility, neutral lookup response, redirect start, generic callback failure, SSO MFA, secret placeholder, test-before-enable, domain conflict, enforcement warning and disable confirmation.

- [ ] **Step 2: Run tests and confirm failure**

Run: `cd apps/web && npx playwright test e2e/oidc.spec.ts`

Expected: fail because pages/controls do not exist. If this repository has no unit UI runner, add these cases to its Playwright suite and document the exact command.

- [ ] **Step 3: Implement the minimal UI**

Use current design components and `api.ts`. Do not add tenant IDs to browser storage. Display one clear note that enabling enforcement can block local login and that break-glass requires server CLI access.

- [ ] **Step 4: Run UI and build checks**

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add 'apps/web/src/app/(auth)/login/page.tsx' 'apps/web/src/app/(app)/admin/security/page.tsx' 'apps/web/src/app/(app)/admin/layout.tsx' apps/web/src/lib/api.ts apps/web/e2e/oidc.spec.ts
git commit -m "feat(web): add organization SSO experience"
```

### Task 7: OIDC acceptance, documentation and honest evidence

**Files:**
- Modify: `README.md`
- Modify: `docs/guides/admin.md`
- Create: `docs/evidence/oidc-entra-acceptance.md`

**Interfaces:**
- Produces: deployment runbook, firewall destinations, configuration matrix, rollback/disable procedure and evidence table.

- [ ] **Step 1: Run the complete API suite**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q`

Expected: all tests pass except explicit live-service skips.

- [ ] **Step 2: Run API and web static/build checks**

Run: `cd apps/api && .venv/bin/ruff check joltiq tests && .venv/bin/mypy joltiq`

Run: `cd apps/web && npm run typecheck && npm run build`

Expected: exit 0.

- [ ] **Step 3: Run local protocol acceptance**

Run an ephemeral standards-compliant OIDC fixture through login, MFA, JIT, enforcement, key rotation, outage and recovery cases. Verify provider-disabled operation creates zero OIDC network calls.

- [ ] **Step 4: Run live Entra acceptance only with customer-owned test credentials**

Verify a real Entra tenant with two test users, MFA, key rotation simulation and revocation. If credentials are absent, mark this `BLOCKED — live tenant not supplied`; do not call the local fixture proof of Entra acceptance.

- [ ] **Step 5: Document operations and results**

Include domain-transfer procedure, CLI recovery, secret rotation, canonical callback, logs that must stay redacted and exact remaining blockers.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/guides/admin.md docs/evidence/oidc-entra-acceptance.md
git commit -m "docs: record OIDC and Entra acceptance"
```
