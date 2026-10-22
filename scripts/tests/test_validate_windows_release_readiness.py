import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_windows_release_readiness.py"


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


if __name__ == "__main__":
    unittest.main()
