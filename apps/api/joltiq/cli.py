"""Operational CLI.

  python -m joltiq.cli set-app-password             # sets the joltiq_app DB role password from JOLTIQ_APP_DB_PASSWORD
  python -m joltiq.cli create-platform-owner EMAIL  # grants platform-owner to an existing user (requires DB owner creds)
  python -m joltiq.cli export-openapi PATH          # writes the OpenAPI document
  python -m joltiq.cli verify-audit ORG_ID          # verifies an organization's audit hash chain
  python -m joltiq.cli gen-keys                     # prints fresh secret/encryption/approval/agent signing keys for .env
"""
from __future__ import annotations

import json
import os
import sys

from sqlalchemy import create_engine, text


def _owner_engine():
    from .config import get_settings

    return create_engine(get_settings().migration_database_url)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "set-app-password":
        pw = os.environ.get("JOLTIQ_APP_DB_PASSWORD")
        if not pw:
            print("JOLTIQ_APP_DB_PASSWORD not set; leaving default (development only)")
            return 0
        with _owner_engine().begin() as c:
            c.execute(text("select set_config('jq.pw', :p, true)"), {"p": pw})
            c.execute(text("DO $$ BEGIN EXECUTE format('ALTER ROLE joltiq_app PASSWORD %L', current_setting('jq.pw')); END $$"))
        print("joltiq_app password updated")
    elif cmd == "create-platform-owner":
        with _owner_engine().begin() as c:
            n = c.execute(text("update users set is_platform_owner = true where email = :e"), {"e": argv[1].lower()}).rowcount
        print("granted" if n else "no such user")
        return 0 if n else 1
    elif cmd == "export-openapi":
        from .main import create_app

        with open(argv[1], "w") as f:
            json.dump(create_app().openapi(), f, indent=2)
        print(f"wrote {argv[1]}")
    elif cmd == "verify-audit":
        import uuid

        from .db import session_scope
        from .services.audit import verify_chain

        with session_scope(uuid.UUID(argv[1])) as db:
            print(json.dumps(verify_chain(db, uuid.UUID(argv[1]))))
    elif cmd == "gen-keys":
        import secrets

        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        k = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        print(f"JOLTIQ_SECRET_KEY={secrets.token_urlsafe(48)}\nJOLTIQ_ENCRYPTION_KEY={Fernet.generate_key().decode()}\n"
              f"JOLTIQ_APPROVAL_SIGNING_KEY={secrets.token_urlsafe(48)}\nJOLTIQ_BROWSER_WORKER_TOKEN={secrets.token_urlsafe(32)}\n"
              f"JOLTIQ_APP_DB_PASSWORD={secrets.token_urlsafe(24)}\nJOLTIQ_AGENT_SIGNING_KEY_PEM=\"{k.strip()}\"")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
