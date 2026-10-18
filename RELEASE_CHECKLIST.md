# Release checklist (production)

- [ ] All CI jobs green (api, web, windows-agent). Docker images built and scanned.
- [ ] `pip-audit` / `npm audit` clean, or findings triaged in SECURITY.md.
- [ ] External security review / penetration test complete. Release-blocking findings fixed.
- [ ] Secrets generated (`python -m joltiq.cli gen-keys`) and stored in the secrets manager. No `dev-only` values. `JOLTIQ_ENV=production`, `JOLTIQ_COOKIE_SECURE=true`, `JOLTIQ_ENABLE_LOCAL_FIXTURE_PROVIDERS=false`.
- [ ] `joltiq_app` DB password set (`cli set-app-password`). The app connects as `joltiq_app`, migrations run as owner.
- [ ] Managed PostgreSQL with PITR backups. A restore drill has been performed and timed.
- [ ] Browser workers on an isolated node pool with egress restricted to public internet only (NetworkPolicy + egress proxy).
- [ ] TLS on all public endpoints. HSTS on. Custom domains provisioned.
- [ ] At least one AI provider live-tested from Admin → AI models. Research provider key set and tested.
- [ ] Stripe live mode explicitly authorized. Prices validated. Webhook endpoint configured and signature verified.
- [ ] SMTP provider configured with SPF/DKIM/DMARC.
- [ ] Monitoring: Prometheus scrape, alerts routed, OTLP traces flowing, log retention set.
- [ ] Windows companion: MSI built and **signed** with an authorized certificate. Acceptance script passed on Windows 11 (results attached). Compatibility matrix updated. Only claim the OS versions that were tested.
- [ ] Rollback plan: previous image tags pinned. Migrations reviewed for backward compatibility.
- [ ] Live acceptance: full release scenario (ticket → chat → knowledge → research → consent → plan → approval → execution → VERIFIED → ticket updated → draft article) on the production-like environment.
