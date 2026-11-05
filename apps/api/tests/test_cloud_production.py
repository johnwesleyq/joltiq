from __future__ import annotations

import sys
import types
import uuid

import pytest


def _production_settings(**overrides):
    from joltiq.config import Settings

    values = {
        "env": "production",
        "database_url": "postgresql+psycopg://app:secret@db.example.internal:5432/joltiq",
        "migration_database_url": "postgresql+psycopg://owner:secret@db.example.internal:5432/joltiq",
        "redis_url": "rediss://cache.example.internal:6379/0",
        "secret_key": "s" * 40,
        "encryption_key": "e" * 44,
        "approval_signing_key": "a" * 40,
        "cookie_secure": True,
        "web_origin": "https://app.joltiq.example",
        "api_public_url": "https://api.joltiq.example",
        "browser_worker_token": "b" * 40,
        "enable_local_fixture_providers": False,
        "openai_api_key": "sk-central-test-key",
        "storage_backend": "s3",
        "s3_bucket": "joltiq-production",
    }
    values.update(overrides)
    return Settings(**values)


def test_secure_cloud_production_configuration_is_accepted():
    _production_settings().assert_production_safe()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"web_origin": "http://app.joltiq.example"}, "web_origin"),
        ({"api_public_url": "http://api.joltiq.example"}, "api_public_url"),
        ({"database_url": "postgresql+psycopg://app:x@localhost:5432/joltiq"}, "database_url"),
        ({"redis_url": "redis://127.0.0.1:6379/0"}, "redis_url"),
        ({"browser_worker_token": "short"}, "browser_worker_token"),
        ({"storage_backend": "local"}, "storage_backend"),
        ({"s3_bucket": ""}, "s3_bucket"),
        ({"openai_api_key": None}, "openai_api_key"),
    ],
)
def test_production_rejects_unsafe_cloud_configuration(override, message):
    settings = _production_settings(**override)
    with pytest.raises(RuntimeError, match=message):
        settings.assert_production_safe()


def test_aws_s3_uses_default_credential_chain_without_custom_endpoint(monkeypatch):
    from joltiq.services import storage

    settings = _production_settings(s3_region="us-west-2", s3_endpoint=None, s3_access_key=None, s3_secret_key=None)
    calls: list[tuple[str, dict]] = []
    client = object()

    def make_client(service, **kwargs):
        calls.append((service, kwargs))
        return client

    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=make_client))

    assert storage._s3() is client
    assert calls == [("s3", {"region_name": "us-west-2"})]


def test_s3_put_uses_configured_kms_key(monkeypatch):
    from joltiq.services import storage

    settings = _production_settings(s3_kms_key_id="alias/joltiq")
    calls: list[dict] = []
    client = types.SimpleNamespace(put_object=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(storage, "_s3", lambda: client)

    key, _ = storage.put(uuid.uuid4(), "knowledge", "guide.txt", b"private")

    assert key.endswith("-guide.txt")
    assert calls[0]["ServerSideEncryption"] == "aws:kms"
    assert calls[0]["SSEKMSKeyId"] == "alias/joltiq"
