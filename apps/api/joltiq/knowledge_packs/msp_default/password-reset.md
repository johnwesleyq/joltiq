# [DEFAULT] Password reset (process only)
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Rule
JoltIQ and technicians **never** ask for, read out, type, store or guess a user's password or MFA code. This document is only the process.

## Process
1. Verify identity with the MSP's approved method (call-back to the number on file, manager approval, or the ticket portal) — not by "knowing details" alone.
2. Microsoft 365 / Entra: send the user to self-service reset (https://aka.ms/sspr) or an admin resets it in the admin center and the user sets a new one at first sign-in.
3. Local Windows account: an admin uses Computer Management on the PC; **account.list_local** / **account.whoami** only show which account is involved (read-only).
4. After reset: user signs in themselves, re-enrols MFA if needed, updates the password on their phone mail app.
5. Record in the ticket: who verified, how, when. Never the password.

## Red flags (stop and escalate to security)
Request comes from a new phone/email, urgency pressure, a "manager" asking for someone else's password, or a request to disable MFA.
