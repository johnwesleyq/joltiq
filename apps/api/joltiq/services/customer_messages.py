"""Server-owned customer wording for security-sensitive workflow pauses and failures."""

DIAGNOSTIC_PORTAL_CONSENT = (
    "JoltIQ found an approved fix that may match this Outlook problem. Before proposing any change, it needs your "
    "permission to check this PC for the named add-in and matching crash evidence. This check is read-only."
)
DIAGNOSTIC_DEVICE_CONSENT = (
    "Please accept the JoltIQ prompt on the PC. The read-only check will confirm the Windows user, classic Outlook, "
    "the named add-in, company policy, and matching crash evidence."
)
DIAGNOSTIC_UNSUPPORTED = (
    "The read-only check could not prove that this approved fix applies safely to this PC, so JoltIQ made no changes "
    "and handed the request to a technician."
)
