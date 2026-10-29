# V9 Ask Chat Integration Plan

**Goal:** Selectively integrate the v9 live-model Ask experience into the current JoltIQ branch without replacing newer security, Windows consent, structured diagnostic, or remote-support behavior.

**Constraints:** Keep existing OIDC/security migrations and the tested Windows read-only/device fixes. Never log, return, or commit model credentials. Plain Ask is chat-only; Guide/Fix remain explicit permission-gated computer-use actions.

## Task 1: Backend live Ask service

- Add failing API tests for live chat replies, no-model setup messaging, status, streaming, conversation history, and durable `troubleshoot` compatibility.
- Add the Ask chat service, minimal configuration/provider support, and selectively merge the agent/device routes.
- Run focused API tests and existing device/computer-use regressions.

## Task 2: Web Ask experience

- Add/adjust tests where available for streaming and AI status behavior.
- Add SSE support, visible connected/setup status, and OpenAI connection UI while preserving current portal/device actions.
- Run web typecheck and production build.

## Task 3: Windows companion experience

- Add/adjust tests for immediate Ask replies, setup notices, and approval-status behavior.
- Merge the v9 protocol/UI changes while retaining terminal-state clearing and local-consent safeguards.
- Run native tests if a compatible runner is available; otherwise record the physical-Windows verification requirement honestly.

## Task 4: Regression and deployment preparation

- Run the complete API suite and web build; scan changes for credentials and regressions.
- Perform a whole-branch review against this plan.
- Commit the integration branch. Do not merge, push, or restart the main stack without the required external-side-effect checkpoint.

## Review focus

- No API key appears in responses, logs, diffs, or fixtures beyond obvious fake test values.
- Ordinary Ask never creates approvals or computer-use work.
- Guide/Fix still requires local PC consent.
- Existing structured read-only diagnostics and terminal status clearing remain intact.
- Missing/unavailable AI is explicit and cannot be misrepresented as a real answer.
