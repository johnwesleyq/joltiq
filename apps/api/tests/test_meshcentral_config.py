import json
from pathlib import Path


def test_unsigned_internal_meshcentral_agents_do_not_self_update():
    root = Path(__file__).resolve().parents[3]
    config = json.loads((root / "infrastructure/meshcentral/config.json").read_text())

    assert config["settings"]["noAgentUpdate"] == 1


def test_embedded_workspace_can_send_meshcentral_session_cookie():
    root = Path(__file__).resolve().parents[3]
    config = json.loads((root / "infrastructure/meshcentral/config.json").read_text())

    assert config["settings"]["allowFraming"] is True
    assert config["settings"]["sessionSameSite"] == "none"


def test_windows_consent_uses_reliable_native_message_box():
    root = Path(__file__).resolve().parents[3]
    config = json.loads((root / "infrastructure/meshcentral/config.json").read_text())

    assert config["domains"][""]["consentMessages"]["oldStyle"] is True


def test_direct_meshcentral_sessions_receive_consent_message_options():
    root = Path(__file__).resolve().parents[3]
    dockerfile = (root / "infrastructure/meshcentral/Dockerfile").read_text()
    patcher = root / "infrastructure/meshcentral/patch-direct-consent.js"

    assert patcher.name in dockerfile
    source = patcher.read_text()
    assert "applyConsentMessageOptions(command)" in source
    assert "command.soptions.oldStyle = true" in source
    assert "command.soptions.consentMsgDesktop" in source
