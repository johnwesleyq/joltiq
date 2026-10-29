"""Hardened OpenID Connect authorization-code + PKCE protocol helpers."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

ALLOWED_ALGORITHMS = frozenset({"RS256", "ES256"})
LEEWAY_SECONDS = 60


class OidcProtocolError(ValueError):
    """Safe, token-free protocol failure."""


@dataclass(frozen=True)
class OidcMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    code_challenge_methods_supported: list[str]


@dataclass(frozen=True)
class ValidatedIdentity:
    issuer: str
    subject: str
    email: str
    email_verified: bool
    claims: dict[str, Any]


@dataclass(frozen=True)
class IdentityResolution:
    user: Any
    linked_via: str
    created: bool


def _validated_https_url(value: str, allowed_hosts: set[str], *, label: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise OidcProtocolError(f"{label} must be an absolute https URL")
    host = parts.hostname.lower().rstrip(".")
    normalized = {item.lower().rstrip(".") for item in allowed_hosts}
    if host not in normalized:
        raise OidcProtocolError(f"{label} host is not deployment-approved")
    return value.strip()


def _json_response(response: httpx.Response, *, label: str) -> dict[str, Any]:
    if response.is_redirect:
        raise OidcProtocolError(f"{label} returned a redirect")
    if response.status_code != 200:
        raise OidcProtocolError(f"{label} returned HTTP {response.status_code}")
    try:
        value = response.json()
    except ValueError as exc:
        raise OidcProtocolError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise OidcProtocolError(f"{label} returned an invalid document")
    return value


def discover_provider(http: httpx.Client, issuer: str, allowed_hosts: set[str]) -> OidcMetadata:
    issuer = _validated_https_url(issuer, allowed_hosts, label="issuer").rstrip("/")
    try:
        response = http.get(issuer + "/.well-known/openid-configuration")
    except httpx.HTTPError as exc:
        raise OidcProtocolError("identity provider discovery is unavailable") from exc
    document = _json_response(response, label="identity provider discovery")
    if document.get("issuer") != issuer:
        raise OidcProtocolError("identity provider discovery issuer does not match")
    try:
        authorization_endpoint = _validated_https_url(
            str(document["authorization_endpoint"]), allowed_hosts, label="authorization endpoint"
        )
        token_endpoint = _validated_https_url(
            str(document["token_endpoint"]), allowed_hosts, label="token endpoint"
        )
        jwks_uri = _validated_https_url(str(document["jwks_uri"]), allowed_hosts, label="JWKS endpoint")
    except KeyError as exc:
        raise OidcProtocolError(f"identity provider discovery is missing {exc.args[0]}") from exc
    methods = document.get("code_challenge_methods_supported") or []
    if not isinstance(methods, list) or "S256" not in methods:
        raise OidcProtocolError("identity provider must support PKCE S256")
    return OidcMetadata(
        issuer=issuer,
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
        jwks_uri=jwks_uri,
        code_challenge_methods_supported=[str(item) for item in methods],
    )


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorization_url(
    metadata: OidcMetadata,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    nonce: str,
    code_verifier: str,
) -> str:
    if "S256" not in metadata.code_challenge_methods_supported:
        raise OidcProtocolError("identity provider must support PKCE S256")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in metadata.authorization_endpoint else "?"
    return metadata.authorization_endpoint + separator + query


def exchange_code(
    http: httpx.Client,
    metadata: OidcMetadata,
    client_id: str,
    client_secret: str | None,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }
    if client_secret:
        form["client_secret"] = client_secret
    try:
        response = http.post(metadata.token_endpoint, data=form)
    except httpx.HTTPError as exc:
        raise OidcProtocolError("identity provider token exchange is unavailable") from exc
    token = _json_response(response, label="identity provider token exchange")
    if not isinstance(token.get("id_token"), str) or not token["id_token"]:
        raise OidcProtocolError("identity provider returned no ID token")
    return token


def fetch_jwks(http: httpx.Client, metadata: OidcMetadata) -> dict[str, Any]:
    try:
        response = http.get(metadata.jwks_uri)
    except httpx.HTTPError as exc:
        raise OidcProtocolError("identity provider signing keys are unavailable") from exc
    keys = _json_response(response, label="identity provider signing keys")
    if not isinstance(keys.get("keys"), list):
        raise OidcProtocolError("identity provider signing keys are invalid")
    return keys


def _signing_key(jwks: dict[str, Any], kid: str) -> Any:
    candidates = [item for item in jwks.get("keys", []) if isinstance(item, dict) and item.get("kid") == kid]
    if len(candidates) != 1:
        raise OidcProtocolError("identity provider signing key was not found")
    key_data = candidates[0]
    key_algorithm = key_data.get("alg")
    if key_algorithm is not None and key_algorithm not in ALLOWED_ALGORITHMS:
        raise OidcProtocolError("identity provider signing key algorithm is not allowed")
    try:
        return jwt.PyJWK.from_dict(key_data).key
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise OidcProtocolError("identity provider signing key is invalid") from exc


def validate_id_token(
    provider: Any,
    metadata: OidcMetadata,
    jwks: dict[str, Any],
    token: str,
    nonce: str,
    now: datetime,
) -> ValidatedIdentity:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OidcProtocolError("ID token header is invalid") from exc
    algorithm = header.get("alg")
    if algorithm not in ALLOWED_ALGORITHMS:
        raise OidcProtocolError("ID token algorithm is not allowed")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise OidcProtocolError("ID token has no signing key identifier")
    key = _signing_key(jwks, kid)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=provider.client_id,
            issuer=metadata.issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "nonce"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcProtocolError("ID token signature or claims are invalid") from exc
    if claims.get("nonce") != nonce:
        raise OidcProtocolError("ID token nonce does not match")
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != provider.client_id:
        raise OidcProtocolError("ID token authorized party does not match")
    subject = claims.get("sub")
    email = claims.get("email") or claims.get("preferred_username")
    if not isinstance(subject, str) or not subject:
        raise OidcProtocolError("ID token subject is missing")
    if not isinstance(email, str) or not email or claims.get("email_verified") is not True:
        raise OidcProtocolError("ID token email is not verified")
    issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=now.tzinfo)
    if issued_at > now + timedelta(seconds=LEEWAY_SECONDS):
        raise OidcProtocolError("ID token issued-at time is in the future")
    return ValidatedIdentity(
        issuer=str(claims["iss"]),
        subject=subject,
        email=email.strip().lower(),
        email_verified=True,
        claims=claims,
    )


def fetch_and_validate_id_token(
    http: httpx.Client,
    provider: Any,
    metadata: OidcMetadata,
    token: str,
    nonce: str,
    now: datetime,
    cached_jwks: dict[str, Any] | None = None,
) -> tuple[ValidatedIdentity, dict[str, Any]]:
    """Validate with cached keys and refresh exactly once for normal key rotation."""

    jwks = cached_jwks or fetch_jwks(http, metadata)
    try:
        return validate_id_token(provider, metadata, jwks, token, nonce, now), jwks
    except OidcProtocolError as exc:
        if "signing key was not found" not in str(exc):
            raise
    refreshed = fetch_jwks(http, metadata)
    return validate_id_token(provider, metadata, refreshed, token, nonce, now), refreshed


def resolve_identity(
    db: Session, provider: Any, identity: ValidatedIdentity, *, now: datetime
) -> IdentityResolution:
    """Resolve a verified external identity without trusting role claims from the token."""

    from ..models import Customer, Membership, User, UserIdentity

    linked = db.execute(
        select(UserIdentity).where(
            UserIdentity.provider_id == provider.id,
            UserIdentity.subject == identity.subject,
        )
    ).scalar_one_or_none()
    if linked:
        user = db.get(User, linked.user_id)
        if not user or user.disabled:
            raise OidcProtocolError("external identity is not available")
        linked.last_login_at = now
        return IdentityResolution(user=user, linked_via=linked.linked_via, created=False)

    user = db.execute(select(User).where(User.email == identity.email)).scalar_one_or_none()
    if user and not user.email_verified_at:
        raise OidcProtocolError("existing account email is not verified")
    membership = None
    if user:
        membership = db.execute(
            select(Membership).where(Membership.user_id == user.id, Membership.org_id == provider.org_id)
        ).scalar_one_or_none()
    linked_via = "verified-email"
    created = False
    if user is None or membership is None:
        if not provider.jit_provisioning:
            raise OidcProtocolError("external identity is not assigned to this organization")
        if provider.default_role not in {"customer", "technician", "auditor"}:
            raise OidcProtocolError("external identity role is not allowed for JIT")
        customer_id = None
        if provider.default_role == "customer":
            email_domain = identity.email.rsplit("@", 1)[-1]
            customer = db.execute(
                select(Customer).where(Customer.org_id == provider.org_id, Customer.domain == email_domain)
            ).scalar_one_or_none()
            if customer is None:
                raise OidcProtocolError("customer identity has no existing customer mapping")
            customer_id = customer.id
        if user is None:
            user = User(
                email=identity.email,
                name=str(identity.claims.get("name") or identity.email.split("@", 1)[0])[:200],
                password_hash=None,
                email_verified_at=now,
            )
            db.add(user)
            db.flush()
            created = True
        if membership is None:
            db.add(
                Membership(
                    user_id=user.id,
                    org_id=provider.org_id,
                    role=provider.default_role,
                    customer_id=customer_id,
                )
            )
        linked_via = "jit"
    db.add(
        UserIdentity(
            org_id=provider.org_id,
            user_id=user.id,
            provider_id=provider.id,
            issuer=identity.issuer,
            subject=identity.subject,
            email_at_link=identity.email,
            linked_via=linked_via,
            last_login_at=now,
        )
    )
    db.flush()
    return IdentityResolution(user=user, linked_via=linked_via, created=created)
