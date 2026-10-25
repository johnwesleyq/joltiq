# Direct Fix with Customer-Owned Local Model — Design

## Goal

A person can describe a problem in the Windows tray, choose **Guide me** or **Fix it for me**, approve the exact goal locally, and have JoltIQ operate the enrolled PC through its existing observe → decide → act → verify loop. Model inference and screenshots remain on the customer's network.

The acceptance case is: “I can't find Adobe Sign in Outlook.” JoltIQ must inspect the real screen, distinguish the Microsoft 365 Acrobat Sign web add-in from the already-enabled Acrobat COM add-in, ask before installing or enabling anything, and only report success after the control is visible in Outlook.

## Decisions

- Add an Ollama provider for computer-use turns. Its base URL is deployment configuration, never tenant-supplied input. The default model is `gemma3:4b`, which accepts screenshots and structured output.
- Ollama is a customer-controlled local provider and remains eligible when external computer-use providers are disabled. A tenant admin must still explicitly enable it and approve use of tenant data.
- The model emits exactly one action per turn using a JSON schema. Existing server and companion policy validation remains authoritative.
- A direct tray request may start Guide/Fix only for its own linked ticket, enrolled device, and active owner. The signed device endpoint creates the desktop support session and computer-use run atomically.
- Starting from the tray is customer initiation of the goal, but the PC still presents the existing native local-consent dialog before any screenshot or input action.
- Guide and Fix are immutable per run. The tray displays the active run state and any customer question or approval request; it does not silently convert an escalated procedure run into a successful ticket.
- Installing/enabling the Acrobat Sign add-in is a consequential action. The existing `request_approval` control tool must pause immediately before that action.
- A successful computer-use run adds evidence to the ticket but does not automatically claim the underlying ticket is resolved unless the model has visibly verified the requested outcome.

## Failure behavior

- If Ollama is unavailable, the model output is invalid, the model loops, consent is declined, the device disconnects, or the step limit is reached, the run fails closed and the ticket remains escalated/open for a person.
- A repeated start request is idempotent while an active computer-use run exists.
- Cross-device, cross-ticket, revoked-device, expired-consent, and wrong-owner attempts are refused.
- Screen text is untrusted data. Secrets must be entered by the customer through secure-input handoff.

## Acceptance evidence

1. Unit tests for Ollama structured-output normalization and malformed/refused output.
2. API tests for signed direct Guide/Fix start, exact ticket/device binding, idempotency, and consent-before-capture.
3. Windows native tests for Guide/Fix controls and protocol parsing.
4. A physical Windows run showing native consent, screen observation, an approval pause before any add-in installation/change, and visible Outlook verification (or an honest failed/escalated outcome).

