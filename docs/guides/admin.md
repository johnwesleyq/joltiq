# Admin guide
- **People / Customers**: invite staff by role. Invite customer users from their customer account.
- **AI models**: add keys and tick *Approved to receive our ticket data*. Set spending limits. Test each provider.
- **Policies**: rules are evaluated lowest priority number first, and the first match wins. Use *Try a rule* to simulate.
- **Approvals**: each card shows the exact action, risk, expiry and whether an undo path is available. Approve/deny requires a confirmation; a changed plan requires a new approval.
- **Approval alerts**: choose reminder timing and in-app/email/Slack/Teams events. Slack and Teams stay off until their integration is connected, successfully tested, and explicitly enabled.
- **Integrations**: Freshdesk, Slack, Teams, incoming REST API, outgoing webhooks, email to ticket. Rotate and revoke secrets. Retry failed syncs. Anything labelled `MOCK` is development-only.
- **Settings & branding**: research on/off, verification requirement, auto-close, approval expiry, retention, branding, custom domain.
- **Devices**: generate one-time enrollment codes. Revoke lost machines.
- **Billing / Analytics / Audit log**: usage against plan limits, verified-resolution rate and cost per fix, tamper-evident audit with export.
