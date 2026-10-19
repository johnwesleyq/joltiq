# JoltIQ desktop companion (cross-platform) — protocol + local policy

This package holds the device side of the JoltIQ protocol for macOS and Windows:

* `protocol.py`: device key (ECDSA P-256, user-only file permissions), enrollment, signed requests (timestamp, nonce, counter), and signed-envelope verification against the server key pinned at enrollment.
* `policy.py`: the **local** allowlist the companion applies to every command, even though commands are signed. It refuses unknown actions, refuses to type anything that looks like a secret, refuses lock/log-out/shutdown key combos, and only opens http(s) URLs.

## Not included: the screen/input layer
The component that captures the screen and performs clicks/keystrokes is **not in this repository**. Supply it in one of two ways:
1. Integrate an established remote-support SDK that already handles OS permissions, consent prompts and input relay. JoltIQ sends it the commands below.
2. Have your team implement a small adapter against the OS APIs.

Either way, the adapter must:
* only act after the person at the computer accepts the session (`session.begin` → `POST /agent/v1/sessions/{id}/local-consent`);
* show a persistent "JoltIQ is controlling this computer" indicator and a stop control (`POST /agent/v1/sessions/{id}/stop`);
* run every command through `policy.check()` first;
* upload the post-action screenshot (`POST /agent/v1/commands/{id}/screenshot`, raw JPEG/PNG) and then the result (`POST /agent/v1/commands/{id}/result` with `width`/`height` of the screenshot);
* surface approvals, questions and "your turn to type" pauses from `GET /agent/v1/computer-use/active` and answer via `POST /agent/v1/computer-use/{id}/respond`.

Command ids and parameters: `screen.capture`, `input.click`, `input.drag`, `input.move`, `input.mouse`, `input.type`, `input.key`, `input.scroll`, `input.wait`, `input.cursor`, `url.open` (see `apps/api/joltiq/services/actions/registry.py`). Coordinates are in the pixel space of the last screenshot the device sent.
