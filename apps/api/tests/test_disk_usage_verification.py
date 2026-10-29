from __future__ import annotations


def test_disk_usage_uses_its_action_bound_verifier_not_model_config_readback():
    from joltiq.services.actions import registry

    assert hasattr(registry, "postconditions_for_step"), "planner needs an action-bound postcondition selector"
    proposed = [{
        "type": "device_config_equals",
        "params": {"path": "disk.free_space"},
        "description": "model-chosen incompatible verifier",
    }]

    assert registry.postconditions_for_step("disk.usage", {}, proposed) == [{
        "type": "device_disk_usage_available",
        "params": {},
        "description": "default check for disk.usage",
    }]


def test_disk_usage_verifier_accepts_a_separate_valid_drive_readback():
    from joltiq.models.enums import VerificationStatus as V
    from joltiq.services.verification import evaluate_device_result

    status, observed = evaluate_device_result(
        "device_disk_usage_available",
        {},
        {"ok": True, "drives": [{"name": "C:\\", "free_gb": 370, "total_gb": 475}]},
    )

    assert status == V.VERIFIED
    assert "C:\\" in observed and "370" in observed and "475" in observed
