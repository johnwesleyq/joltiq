import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_windows_release_readiness.py"
SPEC = importlib.util.spec_from_file_location("windows_readiness_validator", VALIDATOR)
assert SPEC and SPEC.loader
VALIDATOR_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR_MODULE)


def valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-22T00:00:00Z",
        "source_commit": "a" * 40,
        "public_launch_ready": False,
        "gates": {
            name: {
                "status": "blocked",
                "evidence": [],
                "blockers": ["Required evidence is not available."],
            }
            for name in (
                "authenticode_signing",
                "installer_lifecycle",
                "windows_acceptance",
                "independent_security_review",
            )
        },
    }


class ReleaseReadinessValidatorTests(unittest.TestCase):
    def run_validator(self, manifest: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "readiness.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR), str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_accepts_blocked_manifest_when_each_gate_names_a_blocker(self) -> None:
        result = self.run_validator(valid_manifest())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("public_launch_ready=false", result.stdout)

    def test_rejects_launch_ready_when_any_gate_is_not_passed(self) -> None:
        manifest = valid_manifest()
        manifest["public_launch_ready"] = True
        result = self.run_validator(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("all four gates", result.stderr)

    def test_rejects_passed_gate_without_evidence(self) -> None:
        manifest = valid_manifest()
        manifest["gates"]["authenticode_signing"] = {
            "status": "passed",
            "evidence": [],
            "blockers": [],
        }
        result = self.run_validator(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must reference evidence", result.stderr)

    def test_rejects_blocked_gate_without_a_blocker(self) -> None:
        manifest = valid_manifest()
        manifest["gates"]["installer_lifecycle"]["blockers"] = []
        result = self.run_validator(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must name at least one blocker", result.stderr)

    def test_rejects_arbitrary_strings_as_passed_gate_evidence(self) -> None:
        manifest = valid_manifest()
        manifest["public_launch_ready"] = True
        for gate in manifest["gates"].values():
            gate.update(status="passed", evidence=["does-not-exist"], blockers=[])
        result = self.run_validator(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("passed evidence must be typed attestations", result.stderr)

    def test_rejects_non_iso_generation_time(self) -> None:
        manifest = valid_manifest()
        manifest["generated_at"] = "not-a-date"
        result = self.run_validator(manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generated_at must be an ISO-8601 UTC timestamp", result.stderr)

    def test_rejects_generic_attestation_for_authenticode_gate(self) -> None:
        manifest = valid_manifest()
        manifest["gates"]["authenticode_signing"] = {
            "status": "passed",
            "evidence": [],
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_hash = "b" * 64
            payload = {
                "passed": True,
                "source_commit": manifest["source_commit"],
                "artifact_sha256": artifact_hash,
                "signed_roles": None,
            }
            evidence_path = root / "signing.json"
            evidence_path.write_text(json.dumps(payload), encoding="utf-8")
            import hashlib

            manifest["gates"]["authenticode_signing"]["evidence"] = [{
                "kind": "authenticode",
                "path": "signing.json",
                "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                "source_commit": manifest["source_commit"],
                "artifact_sha256": artifact_hash,
            }]
            errors = VALIDATOR_MODULE.validate(manifest, root)
        self.assertTrue(any("signature_status" in error for error in errors), errors)

    def test_rejects_mixed_artifact_hashes_across_passed_gates(self) -> None:
        manifest = valid_manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = []
            for index, artifact_hash in enumerate(("b" * 64, "c" * 64)):
                payload = {
                    "passed": True,
                    "source_commit": manifest["source_commit"],
                    "artifact_sha256": artifact_hash,
                    "signature_status": "Valid",
                    "timestamped": True,
                    "signed_roles": ["tray", "broker", "msi"],
                }
                evidence_path = root / f"signing-{index}.json"
                evidence_path.write_text(json.dumps(payload), encoding="utf-8")
                import hashlib

                evidence.append({
                    "kind": "authenticode",
                    "path": evidence_path.name,
                    "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                    "source_commit": manifest["source_commit"],
                    "artifact_sha256": artifact_hash,
                })
            manifest["gates"]["authenticode_signing"] = {
                "status": "passed",
                "evidence": evidence,
                "blockers": [],
            }
            errors = VALIDATOR_MODULE.validate(manifest, root)
        self.assertTrue(any("mixed artifact hashes" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
