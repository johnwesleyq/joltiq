#!/usr/bin/env python3
"""Fail-closed validation for the Windows public-release readiness manifest."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_GATES = {
    "authenticode_signing",
    "installer_lifecycle",
    "windows_acceptance",
    "independent_security_review",
}
ALLOWED_STATUSES = {"passed", "blocked"}
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def validate(manifest: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if not isinstance(manifest.get("generated_at"), str) or not manifest["generated_at"]:
        errors.append("generated_at must be a non-empty string")
    if not COMMIT_RE.fullmatch(str(manifest.get("source_commit", ""))):
        errors.append("source_commit must be a 40-character lowercase Git SHA")
    if not isinstance(manifest.get("public_launch_ready"), bool):
        errors.append("public_launch_ready must be a boolean")

    gates = manifest.get("gates")
    if not isinstance(gates, dict):
        return errors + ["gates must be an object"]
    if set(gates) != REQUIRED_GATES:
        errors.append("gates must contain exactly the four required release gates")

    for name in sorted(REQUIRED_GATES & set(gates)):
        gate = gates[name]
        if not isinstance(gate, dict):
            errors.append(f"{name} must be an object")
            continue
        status = gate.get("status")
        evidence = gate.get("evidence")
        blockers = gate.get("blockers")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{name}.status must be passed or blocked")
        if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
            errors.append(f"{name}.evidence must be a list of non-empty strings")
        if not isinstance(blockers, list) or not all(isinstance(item, str) and item for item in blockers):
            errors.append(f"{name}.blockers must be a list of non-empty strings")
        if status == "passed" and not evidence:
            errors.append(f"{name} passed gate must reference evidence")
        if status == "passed" and blockers:
            errors.append(f"{name} passed gate cannot retain blockers")
        if status == "blocked" and not blockers:
            errors.append(f"{name} blocked gate must name at least one blocker")

    all_passed = set(gates) == REQUIRED_GATES and all(
        isinstance(gates[name], dict) and gates[name].get("status") == "passed"
        for name in REQUIRED_GATES
    )
    if manifest.get("public_launch_ready") is True and not all_passed:
        errors.append("public_launch_ready requires all four gates to be passed")
    if manifest.get("public_launch_ready") is False and all_passed:
        errors.append("public_launch_ready must be true when all four gates are passed")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_windows_release_readiness.py MANIFEST.json", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid readiness manifest: {exc}", file=sys.stderr)
        return 1
    errors = validate(manifest)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    ready = str(manifest["public_launch_ready"]).lower()
    print(f"valid Windows release-readiness manifest; public_launch_ready={ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
