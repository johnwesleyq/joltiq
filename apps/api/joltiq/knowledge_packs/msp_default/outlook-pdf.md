# [DEFAULT] Outlook problems and PDFs that won't open
> DEMO/DEFAULT content shipped with JoltIQ (msp-default pack v1). Generic Windows/MSP guidance — replace or extend it with your own company procedures.

## Outlook won't open / keeps asking for a password / not receiving
1. Check Microsoft 365 service health first (admin portal) — if Exchange is degraded, wait.
2. JoltIQ: **process.list** — is OUTLOOK.EXE "not responding"? Close it and reopen.
3. Start in safe mode (outlook.exe /safe) to rule out add-ins; disable the faulty add-in.
4. Password prompts: the customer signs in themselves; never ask them for the password. If it persists, the technician repairs the profile (Control Panel → Mail → new profile).
5. Large mailbox / OST over 50 GB → archive (technician).

## PDF won't open / opens in the wrong app
1. JoltIQ: **app.list** "Acrobat" — is a PDF reader installed?
2. If missing: **app.install** Adobe Acrobat Reader from get.adobe.com / ardownload2.adobe.com only (signature checked).
3. Set the default: Settings → Apps → Default apps → .pdf (customer clicks; JoltIQ can guide on screen).
4. Attachment blocked by Outlook → it is a security policy; do not bypass.
