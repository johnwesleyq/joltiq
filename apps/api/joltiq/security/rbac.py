"""Role-based access control. Permissions are checked server-side on every endpoint."""
from __future__ import annotations

from ..models.enums import Role

R = Role
STAFF = {R.ORG_OWNER, R.ADMIN, R.TECHNICIAN}
ADMINS = {R.ORG_OWNER, R.ADMIN}

PERMISSIONS: dict[str, set[Role]] = {
    "tickets.read_all": STAFF | {R.AUDITOR},
    "tickets.read_own": {R.CUSTOMER},
    "tickets.create": STAFF | {R.CUSTOMER},
    "tickets.update": STAFF,
    "tickets.assign": STAFF,
    "tickets.comment": STAFF | {R.CUSTOMER},
    "tickets.internal_note": STAFF,
    "knowledge.read": STAFF | {R.AUDITOR, R.CUSTOMER},  # customers only see customer-visible docs
    "knowledge.write": ADMINS | {R.TECHNICIAN},
    "knowledge.review": ADMINS | {R.TECHNICIAN},
    "knowledge.publish": ADMINS,
    "agents.run": STAFF | {R.CUSTOMER},
    "agents.read_all": STAFF | {R.AUDITOR},
    "research.run": STAFF | {R.CUSTOMER},
    "approvals.decide": STAFF | {R.CUSTOMER},  # further narrowed per approval (approver_roles, requester)
    "sessions.start": STAFF | {R.CUSTOMER},
    "sessions.takeover": STAFF,
    "sessions.read_all": STAFF | {R.AUDITOR},
    "devices.enroll": STAFF | {R.CUSTOMER},
    "devices.manage": ADMINS | {R.TECHNICIAN},
    "users.manage": ADMINS,
    "customers.manage": STAFF,
    "settings.manage": ADMINS,
    "policies.manage": ADMINS,
    "models.manage": ADMINS,
    "integrations.manage": ADMINS,
    "billing.manage": {R.ORG_OWNER, R.ADMIN},
    "branding.manage": ADMINS,
    "analytics.read": ADMINS | {R.AUDITOR, R.TECHNICIAN},
    "audit.read": ADMINS | {R.AUDITOR},
    "notifications.self": set(Role),
}

ROLE_RANK = {R.CUSTOMER: 0, R.AUDITOR: 1, R.TECHNICIAN: 2, R.ADMIN: 3, R.ORG_OWNER: 4, R.PLATFORM_OWNER: 5}


def has_permission(role: str, perm: str) -> bool:
    try:
        r = Role(role)
    except ValueError:
        return False
    if r == R.PLATFORM_OWNER:
        return True
    return r in PERMISSIONS.get(perm, set())


def can_grant_role(granter: str, target: str) -> bool:
    """Admins can't create owners; nobody grants platform_owner via org APIs."""
    if target == R.PLATFORM_OWNER:
        return False
    if target == R.ORG_OWNER:
        return granter == R.ORG_OWNER
    return ROLE_RANK.get(Role(granter), 0) >= ROLE_RANK[R.ADMIN]
