# Security

Report vulnerabilities to security@joltiq.io. JoltIQ holds **no** SOC 2, ISO 27001 or HIPAA certification. Do not claim otherwise.

## Invariants (enforced in code, covered by tests)
1. **Every browser or desktop operation needs authenticated, scoped consent** from the person whose system it is (`sessions.give_consent`, `consent_holder_ok`). Desktop operations also need consent on the device itself. Technicians cannot consent for a customer.
2. **Policy is evaluated outside the model** for every proposed and executed action (`services/policy.py`). Risk comes from the typed registry (`actions/registry.py`), never from model output. Destructive actions are denied by default and cannot be set to auto-allow.
3. **Approvals** are bound to `sha256(action, target, params, plan_hash)`, HMAC-signed, single-use and expiring, and superseded when the step changes (`services/approvals.py`). Model output or web text can never create an approval.
4. **One input-control lease per session**, enforced by a partial unique index. Humans preempt the AI, and the customer preempts everyone. Emergency stop terminates the session and escalates the run.
5. **Secrets stay out of AI context.** Sign-in, MFA and CAPTCHA pause the AI. Humans type into the isolated browser through `protected` input, which is never stored or logged. Secret fields are masked in screenshots, the AI is refused when typing into them, and the Windows companion refuses password fields too.
6. **No stealth or unrestricted execution.** There's no shell action. The companion shows a visible banner and a kill switch, uses no hidden persistence, and its privileged broker exposes only allowlisted service restarts.
7. **Independent verification.** A click is never proof. UNKNOWN never resolves a ticket. The AI cannot resolve a ticket without VERIFIED.
8. **External content is untrusted.** Research queries are built from technical signals and redacted. Fetches are SSRF-guarded and domain-allowlisted. Full pages are screened for instruction-like text and commands, which are stripped from excerpts and flagged. Web fixes are never executed automatically.

## Controls
| Area | Implementation |
|---|---|
| Tenant isolation | PostgreSQL RLS (FORCE) on all tenant tables. The app role is NOBYPASSRLS. App-layer scoping too. Storage keys are prefixed with the org and checked on read. |
| Auth | argon2id, lockout after 8 failures, IP and email rate limits, TOTP MFA, HttpOnly SameSite=Lax session cookie + double-submit CSRF, idle and absolute expiry, all sessions revoked on password reset. |
| Audit | Append-only for the app (UPDATE/DELETE revoked), per-tenant SHA-256 hash chain, verify and export endpoints. |
| Crypto | Integration, model and MFA secrets encrypted with Fernet (`JOLTIQ_ENCRYPTION_KEY`). Device keys are ECDSA P-256 (DPAPI on Windows). Server envelope key from `JOLTIQ_AGENT_SIGNING_KEY_PEM`. Production startup refuses dev keys and test fixtures. |
| Device protocol | Signature over method, path, timestamp (±120 s), single-use nonce, monotonic counter and body hash. Revocation stops all sessions and commands. |
| SSRF | http(s) only, no credentials in URLs, ports 80/443, no private, loopback, link-local, metadata, CGNAT or reserved addresses (link-local blocked in every mode), redirects re-validated, size and time caps. **Gap:** a DNS re-resolution window remains, so production must use an egress proxy. |
| Uploads | Extension + magic bytes, executables refused, macro-enabled Office refused, zip-bomb guard, size limit, served as attachment with nosniff. **No antivirus engine.** |
| Web | CSP, frame-ancestors none, nosniff, referrer policy, same-origin API proxy. |
| Logging | JSON logs with request IDs and redaction of emails, keys, tokens, cards, passwords and IPs. Secret-named fields are fully masked. |
| Dependencies | On 2026-10-20, `npm audit --omit=dev --audit-level=high` was clean. `certifi` was upgraded from the affected `2026.7.22` release to `2026.10.19`; the complete API/browser suite passed and `pip-audit -r requirements.lock` reported no known vulnerabilities. |

## Windows Outlook PDF workflow boundary

- Executable steps are copied from a published, fingerprinted procedure after typed diagnosis. Remembered/chat fixes may select only a published candidate ID and cannot create actions.
- Publishing requires two different MFA-backed admins. Customer portal consent, native-device consent, and exact per-step approval remain separate.
- The MVP Windows companion exposes no screen-control action. The Outlook HKCU write runs only in the matching interactive user's tray process, never as SYSTEM.
- Rollback names the earlier command only. The PC restores its DPAPI-protected local journal entry and refuses a missing, mismatched, or server-supplied prior value.
- A verified setting and responsive Outlook mean `FIX_APPLIED_VERIFIED`; only a customer PDF attempt plus trustworthy post-attempt crash observation can produce `RESOLVED_VERIFIED`.
- The workflow is `IMPLEMENTED_NOT_TESTED` on Windows. No Windows CI run, VM acceptance aggregate, signed MSI, or external review evidence exists. See `docs/operations/windows-outlook-pdf-evidence-index.md`.

## Release-blocking security work still open
- External penetration test and code review.
- OIDC SSO not implemented. MFA enforcement policy per tenant not implemented.
- Egress proxy for fetches and browser workers (NetworkPolicy is provided for Kubernetes).
- Antivirus scanning of uploads and downloads.
- Windows companion runtime review on real hardware, code signing, and update signing.
- Complete all 59 Windows Outlook acceptance runs, including STOP timing and licensed classic Outlook, and retain the aggregate evidence bundle.
