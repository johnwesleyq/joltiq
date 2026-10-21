"""Server-owned customer wording for security-sensitive workflow pauses and failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .procedures import ProcedureDefinition

DIAGNOSTIC_PORTAL_CONSENT = (
    "JoltIQ found an approved fix for the Outlook PDF crash and is requesting permission to diagnose and work on "
    "this PC. You can stop the session at any time."
)
DIAGNOSTIC_DEVICE_CONSENT = (
    "Please accept the JoltIQ prompt on the PC. The read-only check will confirm the Windows user, classic Outlook, "
    "the named add-in, company policy, and matching crash evidence."
)
DIAGNOSTIC_MISMATCH = (
    "JoltIQ could not confirm that the approved PDF-preview add-in caused this crash. Nothing was changed. "
    "A support person will review it."
)
DIAGNOSTIC_UNSUPPORTED = (
    "This Outlook or Windows setup is not supported by this approved fix. Nothing was changed. "
    "A support person will review it."
)

ACTIVE_BANNER = "JoltIQ support is active on this PC — STOP ends control immediately."
CONSENT_EXPIRED = "Permission for this session expired. JoltIQ stopped and made no further changes."
DEVICE_REVOKED = "This computer is no longer authorized for JoltIQ support. The session was stopped."
CONNECTION_LOST = (
    "JoltIQ lost contact with this computer. It will not repeat or count an unfinished action. "
    "A support person will review the recorded state."
)
EMERGENCY_STOP = "The session was stopped. JoltIQ will report any change that had already completed and will not start another action."


def session_status_message(state: str, reason: str | None, emergency_stopped: bool) -> str | None:
    """Translate a server session state into an exact customer safety explanation."""
    normalized = (reason or "").lower()
    if emergency_stopped or "emergency stop" in normalized or "stopped by user" in normalized:
        return EMERGENCY_STOP
    if "device revoked" in normalized:
        return DEVICE_REVOKED
    if "connection lost" in normalized or "offline" in normalized:
        return CONNECTION_LOST
    if state == "expired" or "consent expired" in normalized:
        return CONSENT_EXPIRED
    return None


def diagnostic_failure_message(reasons: list[str]) -> str:
    mismatch_reasons = {"wrong_module", "matching_crash_not_found"}
    return DIAGNOSTIC_MISMATCH if reasons and set(reasons).issubset(mismatch_reasons) else DIAGNOSTIC_UNSUPPORTED

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
SYMPTOM_TEST = "Please open the same PDF attachment now. What happened?"
FIX_APPLIED_TEST_DEFERRED = (
    "The approved fix was applied and checked, but the original problem has not been confirmed resolved. "
    "The ticket will remain open."
)
SYMPTOM_REMINDER = (
    "The approved fix is still applied. Please try the same PDF attachment when you can. If we do not hear back, "
    "a support person will review the open ticket."
)
SYMPTOM_TIMEOUT = (
    "The fix remains applied, but JoltIQ could not confirm the original problem was resolved. A support person will "
    "review the ticket."
)
SYMPTOM_RESOLVED = "The PDF opened without a new Outlook crash. JoltIQ verified the fix and resolved the ticket."
SYMPTOM_PERSISTS = (
    "Outlook still appears to crash when opening the PDF. JoltIQ will not try another change automatically. "
    "A support person will review it."
)
SYMPTOM_UNKNOWN = (
    "The PDF test was reported, but JoltIQ could not verify the Windows crash log reliably. A support person will "
    "review the open ticket."
)


def outlook_session_details(procedure: ProcedureDefinition, procedure_hash: str) -> dict:
    """Return the complete display contract signed into the Windows session-begin envelope."""
    name = procedure.display.addin_name
    progid = procedure.display.addin_progid
    return {
        "workflow": procedure.kind,
        "procedure_hash": procedure_hash,
        "addin_name": name,
        "addin_progid": progid,
        "read_only": True,
        "portal_message": DIAGNOSTIC_PORTAL_CONSENT,
        "device_wait_message": DIAGNOSTIC_DEVICE_CONSENT,
        "native_title": "JoltIQ — permission requested",
        "native_message": (
            "JoltIQ wants to check Outlook crash records and apply the approved fix on this computer. It will "
            f"disable {name} and safely restart Outlook. It will not force Outlook closed. Allow this session?"
        ),
        "step_messages": {
            "office.addin.set_load_behavior": (
                f"Disable {name} ({progid}) for your Windows account. JoltIQ will save the current setting so it "
                "can be restored."
            ),
            "app.restart": (
                "Safely restart classic Outlook to apply the change. If Outlook does not close normally, JoltIQ "
                "will stop and ask for help."
            ),
        },
        "active_message": ACTIVE_BANNER,
    }
