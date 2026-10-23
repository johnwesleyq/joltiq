from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest


def outlook_body() -> dict:
    return {
        "procedure_schema": 2,
        "kind": "windows_outlook_pdf_crash",
        "display": {"addin_name": "PDF Preview", "addin_progid": "PdfPreview.Connect"},
        "diagnostics": [{
            "action": "outlook.pdf_crash.inspect",
            "params": {"addin_progid": "PdfPreview.Connect", "faulting_module": "PdfPreview.dll", "lookback_hours": 24},
            "expect": {"supported": True, "outlook.kind": "classic", "addin.installed": True, "crash.matched": True},
        }],
        "procedure": [
            {
                "action": "office.addin.set_load_behavior",
                "params": {"app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False},
                "rationale": "Disable the published faulty add-in",
                "postconditions": [{"type": "device_config_equals", "params": {
                    "app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False,
                }}],
            },
            {
                "action": "app.restart", "params": {"process": "outlook.exe"},
                "rationale": "Safely restart classic Outlook",
                "postconditions": [{"type": "device_process_responding", "params": {"process": "outlook.exe"}}],
            },
        ],
        "rollback": {
            "action": "office.addin.restore_load_behavior", "source_step": 0,
            "restart": {"action": "app.restart", "params": {"process": "outlook.exe"}},
        },
        "messages": {"diagnostic_consent": "Check Outlook crash evidence", "remediation": "Apply the approved Outlook fix"},
    }


def test_canonical_hash_is_order_independent_but_contract_sensitive():
    """Catches hashing presentation order or omitting an executable/diagnostic field from the fingerprint."""
    from joltiq.services.procedures import procedure_hash, validate_executable_body

    body = outlook_body()
    reordered = {key: body[key] for key in reversed(body)}
    expected = procedure_hash(validate_executable_body(body))
    assert procedure_hash(validate_executable_body(reordered)) == expected
    with_presentation = {**body, "title": "Customer-facing title", "symptoms": ["Presentation only"]}
    assert procedure_hash(validate_executable_body(with_presentation)) == expected

    mutations = [
        ("procedure", 0, "rationale", "Different rationale"),
        ("diagnostics", 0, "expect", {"supported": True, "outlook.kind": "classic", "addin.installed": True, "crash.matched": False}),
    ]
    for section, index, field, value in mutations:
        changed = copy.deepcopy(body)
        changed[section][index][field] = value
        assert procedure_hash(validate_executable_body(changed)) != expected

    changed_setting = copy.deepcopy(body)
    changed_setting["procedure"][0]["params"]["enabled"] = True
    changed_setting["procedure"][0]["postconditions"][0]["params"]["enabled"] = True
    assert procedure_hash(validate_executable_body(changed_setting)) != expected

    changed_progid = copy.deepcopy(body)
    changed_progid["display"]["addin_progid"] = "Other.Connect"
    changed_progid["diagnostics"][0]["params"]["addin_progid"] = "Other.Connect"
    changed_progid["procedure"][0]["params"]["addin_progid"] = "Other.Connect"
    changed_progid["procedure"][0]["postconditions"][0]["params"]["addin_progid"] = "Other.Connect"
    assert procedure_hash(validate_executable_body(changed_progid)) != expected

    reordered_steps = copy.deepcopy(body)
    reordered_steps["procedure"].reverse()
    reordered_steps["rollback"]["source_step"] = 1
    assert procedure_hash(validate_executable_body(reordered_steps)) != expected


@pytest.mark.parametrize("mutation", [
    "missing_rollback", "untyped_diagnostic", "computer_use", "unsupported_action",
    "missing_progid", "missing_module", "incomplete_readback_target",
])
def test_invalid_desktop_procedure_is_rejected(mutation):
    """Catches publication of a desktop procedure that can bypass its typed diagnosis/rollback boundary."""
    from joltiq.services.procedures import validate_executable_body

    body = outlook_body()
    if mutation == "missing_rollback":
        body.pop("rollback")
    elif mutation == "untyped_diagnostic":
        body["diagnostics"] = ["look in Event Viewer"]
    elif mutation == "computer_use":
        body["procedure"][0]["action"] = "input.click"
        body["procedure"][0]["params"] = {"x": 10, "y": 10}
    elif mutation == "unsupported_action":
        body["procedure"][0]["action"] = "shell.exec"
    elif mutation == "missing_progid":
        body["diagnostics"][0]["params"].pop("addin_progid")
    elif mutation == "missing_module":
        body["diagnostics"][0]["params"].pop("faulting_module")
    elif mutation == "incomplete_readback_target":
        body["procedure"][0]["postconditions"][0]["params"] = {"enabled": False}
    with pytest.raises(ValueError):
        validate_executable_body(body)


def test_legacy_browser_publication_is_fingerprinted_without_opening_a_desktop_side_door():
    """Catches breaking existing browser procedures or accepting a legacy desktop action without schema v2."""
    from joltiq.services.procedures import publication_hash

    browser = {
        "procedure": [{
            "action": "browser.navigate", "params": {"url": "https://example.test/settings"},
            "rationale": "Open settings", "postconditions": [],
        }]
    }
    assert len(publication_hash(browser)) == 64
    browser["procedure"][0] = {
        "action": "app.restart", "params": {"process": "outlook.exe"}, "rationale": "restart", "postconditions": []
    }
    with pytest.raises(ValueError):
        publication_hash(browser)


def test_published_procedure_is_revalidated_at_load_and_tampering_is_audited(monkeypatch):
    """Catches trusting a stored executable flag after the published body has changed."""
    from joltiq.models import KnowledgeArticle, KnowledgeDocument
    from joltiq.services.agent import _procedures_for
    from joltiq.services.procedures import publication_hash

    document_id = uuid.uuid4()
    article_id = uuid.uuid4()
    body = outlook_body()
    article = SimpleNamespace(
        id=article_id, org_id=uuid.uuid4(), title="Outlook PDF fix", status="published",
        executable=True, version=3, procedure_hash=publication_hash(body), body=body,
    )
    document = SimpleNamespace(meta={"article_id": str(article_id)})

    class FakeDb:
        def get(self, model, key):
            if model is KnowledgeDocument and key == document_id:
                return document
            if model is KnowledgeArticle and key == article_id:
                return article
            return None

    security_events = []
    monkeypatch.setattr("joltiq.services.agent.audit.security_event", lambda *a, **kw: security_events.append((a, kw)))
    hits = [{"document_id": str(document_id), "eid": "E1"}]
    loaded = _procedures_for(FakeDb(), hits)
    assert loaded[0]["procedure"] == {"article_id": str(article_id), "version": 3, "hash": article.procedure_hash}

    article.body = copy.deepcopy(body)
    article.body["procedure"][0]["params"]["enabled"] = True
    assert _procedures_for(FakeDb(), hits) == []
    assert security_events[-1][0][1] == "executable_procedure_integrity_failed"
