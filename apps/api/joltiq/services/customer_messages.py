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

PARTIAL_APPLICATION = (
    "The add-in was disabled, but Outlook could not be safely restarted. JoltIQ did not force it closed. "
    "The change has not been undone."
)
ROLLBACK_OFFER = "Restore the add-in setting that was present before this session?"
ROLLBACK_CHANGE_REMAINS = (
    "The original add-in setting has not been restored. The change remains in place, and a support person will "
    "restore it manually."
)
ROLLBACK_RESTORED = (
    "The original add-in setting was restored and verified, and Outlook was safely restarted. The original PDF "
    "problem is still open for a support person to review."
)
ROLLBACK_RESTORED_PENDING_RESTART = (
    "The original add-in setting was restored and verified. Outlook could not be safely restarted, so the restored "
    "setting will take effect the next time Outlook starts."
)
