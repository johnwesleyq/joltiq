# Guided Self-Service IT Assistant

**Goal:** When no approved automated fix exists, JoltIQ answers like an experienced IT technician with grounded, cited
step-by-step guidance the person performs themselves, instead of escalating immediately. Covers any IT topic
(Windows, macOS, Linux, M365, networking, cloud/AWS, security, programming).

## Behavior
1. Planning finds no safe automated plan -> `_give_guidance` (task `guidance`, planning-tier model) posts an answer +
   up to 8 ordered steps with citations, sets ticket AWAITING_CUSTOMER, pauses for the customer.
2. Customer replies -> `_handle_guidance_feedback` (task `guidance_feedback`):
   - "fixed" -> ticket resolved **by the customer** (actor_kind=user), `resolution_verified=false`; run RESOLVED
     with `method=customer_confirmed_guidance`. If org requires verification, ticket returns to OPEN for sign-off.
   - not fixed -> one more round of new steps (max 2 rounds), then escalate with what was tried.
   - "technician"/"human" -> immediate escalation.
3. JoltIQ never executes guidance steps. AI still cannot resolve tickets on its own say-so.

## Also changed
- Research allowlist widened to cloud, networking, OS and developer documentation domains (`config.py`).
- Portal shows "Sources" links under AI answers.
- Local fixture handlers `_TroubleshootingGuide` / `_GuidanceFeedback` for keyless dev/CI.

## Tests
- Updated `test_unfamiliar_issue_uses_research_and_never_executes_forum_commands` (now guidance, then escalation on request).
- New `test_general_it_question_gets_guidance_and_customer_confirms_fix`, `test_guidance_that_does_not_work_retries_then_escalates`.

## Remaining for a live demo
- Add an OpenAI or Anthropic key in Admin -> Models and approve it for tenant data.
- Add `JOLTIQ_BRAVE_SEARCH_API_KEY` or `JOLTIQ_TAVILY_API_KEY` for live web research.
