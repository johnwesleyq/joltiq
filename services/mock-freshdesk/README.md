# MOCK Freshdesk (DEV/TEST ONLY)

Not Freshdesk and not affiliated with Freshworks. An in-memory stand-in for the Freshdesk REST API v2
subset used by `joltiq.services.integrations.Freshdesk` (agents/me, tickets list/get/create/update,
notes, reply, conversations, Basic `key:X` auth, 429 + Retry-After, rate-limit headers). Ported from
the JoltIQ reference build. Dev control endpoints live under `/__mock` (reset, faults, state, requests, ui).

* Run: `docker compose --profile dev-mock up -d mock-freshdesk` (host: http://127.0.0.1:8320).
* Point an integration at it: create a Freshdesk integration with API key `mock-fd-key-0001` and
  `config.base_url_override = "http://mock-freshdesk:8500"`. The override is ignored when `JOLTIQ_ENV=production`.
* Tests: `cd services/mock-freshdesk && PYTHONPATH=. pytest -q tests` (the adapter contract test needs the API package installed).

