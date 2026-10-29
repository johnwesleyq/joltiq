"""OIDC protocol contracts ported from the uploaded JoltIQ implementation."""
from __future__ import annotations

import importlib
import importlib.util
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


def _oidc():
    assert importlib.util.find_spec("joltiq.services.oidc") is not None, "OIDC protocol service is missing"
    return importlib.import_module("joltiq.services.oidc")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _metadata(issuer: str = "https://login.microsoftonline.com/tenant/v2.0") -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token",
        "jwks_uri": issuer + "/keys",
        "code_challenge_methods_supported": ["S256"],
    }


def test_discovery_requires_approved_https_issuer_and_rejects_redirects():
    oidc = _oidc()
    with _client(lambda request: httpx.Response(200, json=_metadata(), request=request)) as http:
        with pytest.raises(oidc.OidcProtocolError, match="https"):
            oidc.discover_provider(http, "http://login.microsoftonline.com/tenant/v2.0", {"login.microsoftonline.com"})
        with pytest.raises(oidc.OidcProtocolError, match="approved"):
            oidc.discover_provider(http, "https://evil.example/tenant", {"login.microsoftonline.com"})

    def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.example/config"}, request=request)

    with _client(redirect) as http, pytest.raises(oidc.OidcProtocolError, match="redirect"):
        oidc.discover_provider(http, "https://login.microsoftonline.com/tenant/v2.0", {"login.microsoftonline.com"})


def test_discovery_requires_exact_issuer_endpoints_and_pkce():
    oidc = _oidc()
    issuer = "https://login.microsoftonline.com/tenant/v2.0"
    with _client(lambda request: httpx.Response(200, json=_metadata(issuer), request=request)) as http:
        metadata = oidc.discover_provider(http, issuer, {"login.microsoftonline.com"})
    assert metadata.issuer == issuer

    wrong = _metadata("https://login.microsoftonline.com/other/v2.0")
    with _client(lambda request: httpx.Response(200, json=wrong, request=request)) as http:
        with pytest.raises(oidc.OidcProtocolError, match="issuer"):
            oidc.discover_provider(http, issuer, {"login.microsoftonline.com"})

    no_pkce = _metadata(issuer)
    no_pkce["code_challenge_methods_supported"] = ["plain"]
    with _client(lambda request: httpx.Response(200, json=no_pkce, request=request)) as http:
        with pytest.raises(oidc.OidcProtocolError, match="S256"):
            oidc.discover_provider(http, issuer, {"login.microsoftonline.com"})


def test_authorization_url_uses_state_nonce_and_pkce_s256():
    oidc = _oidc()
    metadata = oidc.OidcMetadata(**_metadata())
    verifier = "v" * 64
    url = oidc.authorization_url(
        metadata,
        client_id="client-id",
        redirect_uri="https://api.example.test/api/v1/auth/sso/callback",
        scopes="openid email profile",
        state="state-value",
        nonce="nonce-value",
        code_verifier=verifier,
    )
    query = parse_qs(urlsplit(url).query)
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-value"]
    assert query["nonce"] == ["nonce-value"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [oidc.pkce_challenge(verifier)]
    assert "code_verifier" not in query


def test_code_exchange_posts_verifier_and_redacts_provider_error():
    oidc = _oidc()
    metadata = oidc.OidcMetadata(**_metadata())
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: values[0] for key, values in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"id_token": "signed-token"}, request=request)

    with _client(handler) as http:
        token = oidc.exchange_code(
            http,
            metadata,
            client_id="client-id",
            client_secret="top-secret",
            redirect_uri="https://api.example.test/api/v1/auth/sso/callback",
            code="authorization-code",
            code_verifier="v" * 64,
        )
    assert token["id_token"] == "signed-token"
    assert seen["grant_type"] == "authorization_code"
    assert seen["code_verifier"] == "v" * 64
    assert seen["client_secret"] == "top-secret"

    with _client(lambda request: httpx.Response(400, json={"error": "invalid_grant", "error_description": "leaked-code"}, request=request)) as http:
        with pytest.raises(oidc.OidcProtocolError) as exc:
            oidc.exchange_code(http, metadata, "client-id", "top-secret", "https://api.example.test/cb", "leaked-code", "v" * 64)
    assert "leaked-code" not in str(exc.value)
    assert "top-secret" not in str(exc.value)


def _key_material(kid: str = "kid-1"):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public_jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return private, {"keys": [public_jwk]}


def _token(private, *, kid="kid-1", **overrides):
    now = datetime.now(UTC)
    claims = {
        "iss": "https://login.microsoftonline.com/tenant/v2.0",
        "aud": "client-id",
        "sub": "subject-1",
        "nonce": "nonce-value",
        "email": "person@example.com",
        "email_verified": True,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


def test_id_token_validation_accepts_valid_token_and_rejects_claim_failures():
    oidc = _oidc()
    private, jwks = _key_material()
    metadata = oidc.OidcMetadata(**_metadata())
    provider = SimpleNamespace(client_id="client-id", issuer=metadata.issuer)
    now = datetime.now(UTC)

    identity = oidc.validate_id_token(provider, metadata, jwks, _token(private), "nonce-value", now)
    assert identity.subject == "subject-1"
    assert identity.email == "person@example.com"
    assert identity.email_verified is True

    bad_tokens = [
        _token(private, nonce="wrong"),
        _token(private, aud="another-client"),
        _token(private, iss="https://evil.example"),
        _token(private, exp=int((now - timedelta(minutes=5)).timestamp())),
        _token(private, sub=""),
        _token(private, email_verified=False),
        _token(private, aud=["client-id", "other"], azp="other"),
    ]
    for token in bad_tokens:
        with pytest.raises(oidc.OidcProtocolError):
            oidc.validate_id_token(provider, metadata, jwks, token, "nonce-value", now)


def test_unknown_kid_is_rejected_without_unverified_fallback():
    oidc = _oidc()
    private, _ = _key_material("new-kid")
    _, old_jwks = _key_material("old-kid")
    metadata = oidc.OidcMetadata(**_metadata())
    provider = SimpleNamespace(client_id="client-id", issuer=metadata.issuer)
    with pytest.raises(oidc.OidcProtocolError, match="signing key"):
        oidc.validate_id_token(provider, metadata, old_jwks, _token(private, kid="new-kid"), "nonce-value", datetime.now(UTC))
