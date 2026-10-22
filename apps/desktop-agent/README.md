# JoltIQ desktop companion (cross-platform) — protocol + local policy

This Python package is a **non-production protocol/reference client** for macOS and Windows:

* `protocol.py`: device key (ECDSA P-256, user-only file permissions), enrollment, signed requests (timestamp, nonce, counter), and signed-envelope verification against the server key pinned at enrollment.
* `policy.py`: the **local** allowlist the companion applies to every command, even though commands are signed. It refuses unknown actions, refuses to type anything that looks like a secret, refuses lock/log-out/shutdown key combos, and only opens http(s) URLs.

## Platform status

The production Windows screen/input layer is implemented in `apps/windows-agent` with native desktop capture, UI Automation target checks, bounded `SendInput`, Guide overlay, durable screenshot evidence and STOP handling. It is packaged only after its capability manifest passes the Windows build gate.

This Python package does not implement or replace that native layer. Do not ship it as a Windows or macOS control driver. The future macOS driver must implement the same protocol and acceptance properties using native macOS permissions/APIs; it is not provided here.

Any additional platform adapter must:
* require both support-session consent and the exact signed per-run `computer_use.begin` Guide/Fix grant;
* show a persistent "JoltIQ is controlling this computer" indicator and a stop control (`POST /agent/v1/sessions/{id}/stop`);
* run every command through `policy.check()` first;
* durably journal an executed action and protected screenshot before uploading the screenshot, then upload the result without repeating input after restart;
* surface approvals, questions and "your turn to type" pauses from `GET /agent/v1/computer-use/active` and answer via `POST /agent/v1/computer-use/{id}/respond`.

Command ids and parameters are defined in `apps/api/joltiq/services/actions/registry.py`. Mutating input is Fix-only and bound to the last accepted frame generation and display-layout hash. Guide uses only `guide.highlight` / `guide.clear` and must have no input path.
