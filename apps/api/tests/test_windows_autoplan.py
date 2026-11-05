from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest

from joltiq.core.errors import Unavailable
from joltiq.services.ai.schemas import WindowsRepairPlan, WindowsRepairStep


def _module():
    return importlib.import_module("joltiq.services.windows_autoplan")


def _model_plan(*steps: WindowsRepairStep, rationale: str = "Use the smallest safe typed repair.") -> WindowsRepairPlan:
    return WindowsRepairPlan(rationale=rationale, confidence=0.9, steps=list(steps))


def _live_call(plan: WindowsRepairPlan):
    def call(*_args, **_kwargs):
        return plan, SimpleNamespace(provider="openai", model="gpt-5.1", live=True)

    return call


def test_live_model_selects_capability_aware_typed_action(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)
    monkeypatch.setattr(
        autoplan,
        "_call",
        _live_call(
            _model_plan(
                WindowsRepairStep(
                    action="network.proxy_check",
                    params={},
                    rationale="Inspect proxy configuration before changing anything.",
                )
            )
        ),
    )

    planned = autoplan.build(None, uuid.uuid4(), "Why can I not reach internal sites?", {"network.proxy_check"})

    assert [item["action"] for item in planned.items] == ["network.proxy_check"]
    assert planned.planner == "live_ai"
    assert planned.model == "openai/gpt-5.1"
    assert planned.live is True
    assert planned.fallback_reason is None
    assert planned.items[0]["effects"]
    assert planned.items[0]["verify"]
    assert planned.items[0]["rollback"]


def test_unadvertised_action_is_omitted_and_reason_is_recorded(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)
    monkeypatch.setattr(
        autoplan,
        "_call",
        _live_call(
            _model_plan(
                WindowsRepairStep(action="network.proxy_check", params={}, rationale="Inspect proxy settings."),
                WindowsRepairStep(action="disk.health", params={}, rationale="Check disk health too."),
            )
        ),
    )

    planned = autoplan.build(None, uuid.uuid4(), "Diagnose this PC", {"network.proxy_check"})

    assert [item["action"] for item in planned.items] == ["network.proxy_check"]
    assert planned.planner == "live_ai"
    assert "disk.health" in (planned.fallback_reason or "")
    assert "not advertised" in (planned.fallback_reason or "")


def test_live_model_cannot_drop_explicit_typed_actions(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)
    monkeypatch.setattr(
        autoplan,
        "_call",
        _live_call(
            _model_plan(
                WindowsRepairStep(
                    action="network.diagnostics",
                    params={},
                    rationale="Check the network before changing it.",
                )
            )
        ),
    )

    planned = autoplan.build(
        None,
        uuid.uuid4(),
        "Flush my DNS and check my network.",
        {"network.flush_dns", "network.diagnostics"},
    )

    assert [item["action"] for item in planned.items] == ["network.diagnostics", "network.flush_dns"]
    assert planned.planner == "live_ai"


def test_invalid_model_parameters_reject_entire_model_plan(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)
    monkeypatch.setattr(
        autoplan,
        "_call",
        _live_call(
            _model_plan(
                WindowsRepairStep(action="network.proxy_check", params={}, rationale="Inspect proxy settings."),
                WindowsRepairStep(action="network.ping", params={"host": "bad host"}, rationale="Test reachability."),
            )
        ),
    )

    planned = autoplan.build(None, uuid.uuid4(), "Flush my DNS", {"network.flush_dns", "network.proxy_check", "network.ping"})

    assert [item["action"] for item in planned.items] == ["network.flush_dns"]
    assert planned.planner == "deterministic"
    assert planned.live is False
    assert "invalid" in (planned.fallback_reason or "")


def test_model_cannot_invent_high_risk_action_not_in_user_request(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)
    monkeypatch.setattr(
        autoplan,
        "_call",
        _live_call(
            _model_plan(
                WindowsRepairStep(action="printer.clear_queue", params={}, rationale="This might improve performance.")
            )
        ),
    )

    planned = autoplan.build(None, uuid.uuid4(), "My computer feels slow", {"printer.clear_queue"})

    assert planned.items == []
    assert planned.planner == "deterministic"
    assert "high-risk" in (planned.fallback_reason or "")


def test_provider_failure_returns_capability_filtered_deterministic_plan(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)

    def fail(*_args, **_kwargs):
        raise Unavailable("provider timed out", code="ai_unavailable")

    monkeypatch.setattr(autoplan, "_call", fail)

    planned = autoplan.build(None, uuid.uuid4(), "Flush my DNS and check my network", {"network.diagnostics"})

    assert [item["action"] for item in planned.items] == ["network.diagnostics"]
    assert planned.planner == "deterministic"
    assert planned.model is None
    assert planned.live is False
    assert "network.flush_dns" in (planned.fallback_reason or "")
    assert "not advertised" in (planned.fallback_reason or "")
    assert "provider timed out" in (planned.fallback_reason or "")


def test_unexpected_planner_bug_is_not_silently_hidden(monkeypatch):
    autoplan = _module()
    monkeypatch.setattr(autoplan, "_has_live_provider", lambda *_: True)

    def fail(*_args, **_kwargs):
        raise RuntimeError("programming bug")

    monkeypatch.setattr(autoplan, "_call", fail)

    with pytest.raises(RuntimeError, match="programming bug"):
        autoplan.build(None, uuid.uuid4(), "Flush my DNS", {"network.flush_dns"})
