"""Static safety contract for the development-only headless companion fixture."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HEADLESS = ROOT / "apps/windows-agent/src/JoltIQ.Agent.Headless"


def test_headless_uses_current_core_and_simulated_os_without_protocol_copy():
    project = (HEADLESS / "JoltIQ.Agent.Headless.csproj").read_text()
    program = (HEADLESS / "Program.cs").read_text()
    assert "JoltIQ.Agent.Core.csproj" in project
    assert (HEADLESS / "SimulatedOs.cs").is_file()
    assert "PersistentCommandJournal" in program and "AgentRuntime" in program
    assert not list(HEADLESS.glob("*Protocol*.cs"))


def test_headless_refuses_production_and_has_no_arbitrary_command_endpoint():
    program = (HEADLESS / "Program.cs").read_text()
    assert 'JOLTIQ_ENV' in program and 'production' in program
    assert 'HEADLESS-SIM-01' in program and 'SIMULATED Windows state' in program
    assert 'MapPost("/execute"' not in program and 'MapPost("/command"' not in program
    assert 'X-Control-Key' in program


def test_headless_is_absent_by_default_and_profile_gated():
    default = subprocess.run(
        ["docker", "compose", "config", "--services"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    dev = subprocess.run(
        ["docker", "compose", "--profile", "dev-mock", "config", "--services"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert "companion-headless" not in default
    assert "companion-headless" in dev
    dockerfile = (ROOT / "apps/windows-agent/Dockerfile.headless").read_text()
    assert 'org.joltiq.dev-only="true"' in dockerfile


def test_freshdesk_mock_is_fixed_labelled_and_profile_gated():
    app = (ROOT / "services/mock-freshdesk/mock_freshdesk/app.py").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "MOCK Freshdesk" in app and "DEV/TEST ONLY" in app
    assert "mock-fd-key-0001" in app
    assert "mock-freshdesk:" in compose and 'profiles: ["dev-mock"]' in compose
