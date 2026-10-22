#!/usr/bin/env python3
"""Fail-closed validation for the Windows public-release readiness manifest."""

from __future__ import annotations

import json
import hashlib
import re
import sys
from datetime import datetime
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_EVIDENCE_KINDS = {
    "authenticode_signing": {"authenticode"},
    "installer_lifecycle": {"installer_lifecycle"},
    "windows_acceptance": {"windows_10_acceptance", "windows_11_acceptance"},
    "independent_security_review": {"independent_security_review"},
}


def _is_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _validate_passed_evidence(
    gate_name: str, evidence: object, source_commit: object, repo_root: Path
) -> list[str]:
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, dict) for item in evidence):
        return [f"{gate_name} passed evidence must be typed attestations"]
    errors: list[str] = []
    kinds: set[str] = set()
    artifact_hashes: set[str] = set()
    for index, item in enumerate(evidence):
        prefix = f"{gate_name}.evidence[{index}]"
        kind = item.get("kind")
        path_value = item.get("path")
        digest = item.get("sha256")
        attested_commit = item.get("source_commit")
        artifact_hash = item.get("artifact_sha256")
        if not isinstance(kind, str) or not kind:
            errors.append(f"{prefix}.kind must be non-empty")
        else:
            kinds.add(kind)
        if attested_commit != source_commit:
            errors.append(f"{prefix}.source_commit must match the readiness source_commit")
        if not SHA256_RE.fullmatch(str(artifact_hash or "")):
            errors.append(f"{prefix}.artifact_sha256 must be a lowercase SHA-256")
        else:
            artifact_hashes.add(artifact_hash)
        if not SHA256_RE.fullmatch(str(digest or "")):
            errors.append(f"{prefix}.sha256 must be a lowercase SHA-256")
        if not isinstance(path_value, str) or not path_value:
            errors.append(f"{prefix}.path must be non-empty")
            continue
        relative = Path(path_value)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"{prefix}.path must stay inside the repository")
            continue
        path = (repo_root / relative).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            errors.append(f"{prefix}.path must stay inside the repository")
            continue
        if not path.is_file():
            errors.append(f"{prefix}.path does not exist")
            continue
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != actual_digest:
            errors.append(f"{prefix}.sha256 does not match the evidence file")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"{prefix}.path must contain a JSON attestation")
            continue
        if payload.get("passed") is not True:
            errors.append(f"{prefix} attestation must say passed=true")
        if payload.get("source_commit") != source_commit:
            errors.append(f"{prefix} attestation source_commit mismatch")
        if payload.get("artifact_sha256") != artifact_hash:
            errors.append(f"{prefix} attestation artifact_sha256 mismatch")
    missing = REQUIRED_EVIDENCE_KINDS[gate_name] - kinds
    if missing:
        errors.append(f"{gate_name} is missing required evidence kinds: {', '.join(sorted(missing))}")
    if len(artifact_hashes) > 1:
        errors.append(f"{gate_name} evidence refers to mixed artifact hashes")
    return errors


def validate(manifest: Any, repo_root: Path | None = None) -> list[str]:
    repo_root = repo_root or Path(__file__).resolve().parents[1]
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest must be a JSON object"]
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if not _is_utc_timestamp(manifest.get("generated_at")):
        errors.append("generated_at must be an ISO-8601 UTC timestamp ending in Z")
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
        if not isinstance(evidence, list):
            errors.append(f"{name}.evidence must be a list")
        if not isinstance(blockers, list) or not all(isinstance(item, str) and item for item in blockers):
            errors.append(f"{name}.blockers must be a list of non-empty strings")
        if status == "passed" and not evidence:
            errors.append(f"{name} passed gate must reference evidence")
        if status == "passed":
            errors.extend(_validate_passed_evidence(name, evidence, manifest.get("source_commit"), repo_root))
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
