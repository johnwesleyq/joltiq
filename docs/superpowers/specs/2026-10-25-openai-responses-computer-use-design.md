# OpenAI Responses Computer Use Design

## Intent

Add an explicitly enabled cloud intelligence option for JoltIQ's existing Windows computer-control runtime. OpenAI chooses the next small UI action from the current screenshot and task history; JoltIQ remains responsible for customer consent, action policy, signed device commands, stop controls, execution, audit, and final verification.

## Goals

- Use the OpenAI Responses API for real screenshot-aware computer decisions.
- Reuse JoltIQ's existing single-action `observe -> decide -> act -> verify` loop and normalized action vocabulary.
- Keep sensitive or consequential actions behind JoltIQ's existing `request_approval` and `request_secure_input` controls.
- Send requests with `store: false` and never place API keys or screenshot bytes in application logs.
- Require both a deployment opt-in and the tenant's existing `enabled` plus `approved_for_tenant_data` provider settings.
- Fail closed when OpenAI is unavailable or returns malformed/unsupported actions. Never silently substitute the scripted fixture.
- Record the actual provider, model, token usage, latency, cost, and outcome in existing audit/billing records.

## Non-goals

- This does not make an offline deployment cloud-free; screenshots and task context are sent to OpenAI when cloud mode is enabled.
- This does not bypass Windows consent, RDP restrictions, local kill switches, or customer approval prompts.
- This does not let the model execute PowerShell, arbitrary code, or direct API calls on the customer computer.
- This does not prove customer readiness by itself; a physical Windows acceptance run is still required.

## Design

JoltIQ will call `POST /v1/responses` directly through its existing HTTP adapter layer. The adapter will send the system safety prompt, a compact textual history, and the most recent hydrated screenshot. It will expose one strict function tool per supported JoltIQ computer/control action and set `parallel_tool_calls: false`, ensuring each model turn selects at most one next action. This matches JoltIQ's existing action-by-action approval and frame-binding architecture more safely than accepting a batch of unreviewed UI actions.

The default OpenAI computer-use model is `gpt-6.1-sol`, following the current official OpenAI computer-use guide, but remains tenant-configurable. Each request uses `store: false`. OpenAI output is normalized into the existing `Turn` and `ToolUse` structures, so the rest of the run engine remains provider-independent.

External computer use remains off unless `JOLTIQ_ALLOW_EXTERNAL_COMPUTER_USE_MODELS=true`. Tenant administrators must also enable OpenAI, store a valid encrypted API key, approve the provider for tenant data, and route `computer_use` to OpenAI. Production startup permits the deployment flag; the tenant gates still decide whether any data is sent.

## Acceptance

- Contract tests prove the Responses request shape, screenshot conversion, `store: false`, tool restrictions, action normalization, usage parsing, and error classification.
- Routing tests prove OpenAI is unavailable without deployment opt-in or tenant data approval and selected when both gates are present.
- Security tests prove fixtures remain unavailable in production and external computer use is an explicit deployment choice.
- The full API suite passes.
- A live test is reported separately and is successful only when the physical Windows PC completes the task and the final installed-software state is independently verified.
