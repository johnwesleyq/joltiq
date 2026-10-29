"""Organization security administration and public OIDC discovery."""
from __future__ import annotations

import base64
import json
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import Conflict, Forbidden, Unauthorized
from ..db import get_raw_session, set_tenant
from ..deps import Ctx, perm
from ..models import OidcLoginState, OidcProvider, OidcProviderDomain, SsoMfaChallenge, utcnow
from ..security.crypto import (
    canonical_json,
    decrypt_secret,
    encrypt_secret,
    hmac_sign,
    hmac_verify,
    new_token,
    sha256_hex,
)
from ..security.ratelimit import hit
from ..services import audit, oidc

router = APIRouter(tags=["security"])
PROOF_TTL_SECONDS = 300
LOOKUP_MESSAGE = "If your organization uses work sign-in, continue with the available option."
OIDC_STATE_COOKIE = "jq_oidc_state"
SSO_MFA_COOKIE = "jq_sso_mfa"


class OidcConfigIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="entra", pattern="^(entra|generic)$")
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str | None = Field(default=None, max_length=4000)
    scopes: str = Field(default="openid email profile", max_length=500)
    domains: list[str] = Field(min_length=1, max_length=20)
    default_role: str = Field(default="technician", pattern="^(customer|technician|auditor)$")
    role_mapping: dict = Field(default_factory=dict)
    jit_provisioning: bool = False
    enforce_sso: bool = False
    enabled: bool = False
    test_proof: str | None = None

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        normalized = []
        for raw in domains:
            value = raw.strip().lower().rstrip(".")
            if not value or "@" in value or "/" in value or len(value) > 253 or "." not in value:
                raise ValueError("invalid email domain")
            normalized.append(value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate email domain")
        return sorted(normalized)


class LookupIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class SsoMfaIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


def _require_mfa(ctx: Ctx) -> None:
    if not ctx.p.user.mfa_enabled or not ctx.p.session.mfa_passed:
        raise Forbidden("Enable and verify MFA before changing organization sign-in.", code="mfa_required")


def _provider(ctx: Ctx) -> OidcProvider | None:
    return ctx.db.execute(select(OidcProvider).where(OidcProvider.org_id == ctx.org_id)).scalar_one_or_none()


def _secret_for(body: OidcConfigIn, provider: OidcProvider | None) -> str | None:
    if body.client_secret:
        return body.client_secret
    return decrypt_secret(provider.client_secret_enc) if provider and provider.client_secret_enc else None


def _fingerprint(body: OidcConfigIn, secret: str | None) -> str:
    value = body.model_dump(exclude={"client_secret", "test_proof"})
    value["secret_hash"] = sha256_hex(secret or "")
    return sha256_hex(canonical_json(value))


def _proof(fingerprint: str) -> str:
    payload = base64.urlsafe_b64encode(
        canonical_json({"fingerprint": fingerprint, "exp": int(utcnow().timestamp()) + PROOF_TTL_SECONDS}).encode()
    ).rstrip(b"=").decode()
    return payload + "." + hmac_sign(get_settings().secret_key, payload)


def _verify_proof(proof: str | None, fingerprint: str) -> bool:
    if not proof or "." not in proof:
        return False
    payload, signature = proof.rsplit(".", 1)
    if not hmac_verify(get_settings().secret_key, payload, signature):
        return False
    try:
        padded = payload + "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        return value["fingerprint"] == fingerprint and int(value["exp"]) >= int(utcnow().timestamp())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def _serialize(provider: OidcProvider, domains: list[str]) -> dict:
    return {
        "id": str(provider.id),
        "display_name": provider.display_name,
        "kind": provider.kind,
        "issuer": provider.issuer,
        "client_id": provider.client_id,
        "client_secret_set": bool(provider.client_secret_enc),
        "scopes": provider.scopes,
        "domains": domains,
        "default_role": provider.default_role,
        "role_mapping": provider.role_mapping,
        "jit_provisioning": provider.jit_provisioning,
        "enforce_sso": provider.enforce_sso,
        "enabled": provider.enabled,
        "tested_at": provider.tested_at.isoformat() if provider.tested_at else None,
        "callback_url": get_settings().api_public_url.rstrip("/") + "/api/v1/auth/sso/callback",
    }


@router.get("/admin/security/oidc")
def get_oidc(ctx: Ctx = Depends(perm("settings.manage"))):
    provider = _provider(ctx)
    if not provider:
        return None
    domains = ctx.db.execute(
        select(OidcProviderDomain.domain).where(OidcProviderDomain.provider_id == provider.id).order_by(OidcProviderDomain.domain)
    ).scalars().all()
    return _serialize(provider, list(domains))


@router.post("/admin/security/oidc/test")
def test_oidc(body: OidcConfigIn, ctx: Ctx = Depends(perm("settings.manage"))):
    _require_mfa(ctx)
    secret = _secret_for(body, _provider(ctx))
    settings = get_settings()
    with httpx.Client(timeout=settings.oidc_http_timeout_seconds, follow_redirects=False, trust_env=False) as client:
        metadata = oidc.discover_provider(client, body.issuer, set(settings.oidc_allowed_issuer_hosts))
    fingerprint = _fingerprint(body, secret)
    audit.record(
        ctx.db,
        org_id=ctx.org_id,
        actor_kind="user",
        actor_id=ctx.p.user_id,
        action="oidc.tested",
        resource_type="oidc_provider",
        data={"issuer": body.issuer, "fingerprint": fingerprint[:12]},
    )
    return {
        "ok": True,
        "test_proof": _proof(fingerprint),
        "authorization_endpoint": metadata.authorization_endpoint,
        "token_endpoint": metadata.token_endpoint,
        "pkce_s256": True,
    }


@router.put("/admin/security/oidc")
def save_oidc(body: OidcConfigIn, ctx: Ctx = Depends(perm("settings.manage"))):
    _require_mfa(ctx)
    provider = _provider(ctx)
    secret = _secret_for(body, provider)
    fingerprint = _fingerprint(body, secret)
    if not _verify_proof(body.test_proof, fingerprint):
        raise Conflict("Test this exact configuration before saving.", code="oidc_test_required")
    if provider is None:
        provider = OidcProvider(org_id=ctx.org_id, display_name=body.display_name, issuer=body.issuer, client_id=body.client_id)
        ctx.db.add(provider)
        ctx.db.flush()
    provider.display_name = body.display_name
    provider.kind = body.kind
    provider.issuer = body.issuer.rstrip("/")
    provider.client_id = body.client_id
    if body.client_secret:
        provider.client_secret_enc = encrypt_secret(body.client_secret)
    provider.scopes = body.scopes
    provider.default_role = body.default_role
    provider.role_mapping = body.role_mapping
    provider.jit_provisioning = body.jit_provisioning
    provider.enforce_sso = body.enforce_sso
    provider.enabled = body.enabled
    provider.tested_at = utcnow()
    provider.tested_fingerprint = fingerprint
    ctx.db.execute(delete(OidcProviderDomain).where(OidcProviderDomain.provider_id == provider.id))
    ctx.db.flush()
    try:
        with ctx.db.begin_nested():
            ctx.db.add_all(
                [OidcProviderDomain(org_id=ctx.org_id, provider_id=provider.id, domain=domain) for domain in body.domains]
            )
            ctx.db.flush()
    except IntegrityError as exc:
        raise Conflict("An enabled sign-in provider already uses that domain.", code="oidc_domain_conflict") from exc
    audit.record(
        ctx.db,
        org_id=ctx.org_id,
        actor_kind="user",
        actor_id=ctx.p.user_id,
        action="oidc.saved",
        resource_type="oidc_provider",
        resource_id=provider.id,
        data={"enabled": provider.enabled, "enforce_sso": provider.enforce_sso, "domains": body.domains},
    )
    return _serialize(provider, body.domains)


@router.post("/admin/security/oidc/disable")
def disable_oidc(ctx: Ctx = Depends(perm("settings.manage"))):
    _require_mfa(ctx)
    provider = _provider(ctx)
    if not provider:
        return {"disabled": True}
    provider.enabled = False
    provider.enforce_sso = False
    audit.record(
        ctx.db,
        org_id=ctx.org_id,
        actor_kind="user",
        actor_id=ctx.p.user_id,
        action="oidc.disabled",
        resource_type="oidc_provider",
        resource_id=provider.id,
    )
    return {"disabled": True}


@router.post("/auth/sso/lookup")
def sso_lookup(body: LookupIn, request: Request, db: Session = Depends(get_raw_session)):
    email = body.email.strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    ip = request.client.host if request.client else "unknown"
    hit(db, f"sso-lookup-ip:{ip}", get_settings().rate_limit_login_per_minute * 3, 60)
    hit(db, f"sso-lookup-email:{sha256_hex(email)}", get_settings().rate_limit_login_per_minute, 60)
    set_tenant(db, None, platform=True)
    row = db.execute(
        select(OidcProviderDomain, OidcProvider)
        .join(OidcProvider, OidcProvider.id == OidcProviderDomain.provider_id)
        .where(OidcProviderDomain.domain == domain, OidcProvider.enabled.is_(True))
    ).first()
    if not row:
        return {"available": False, "continuation": None, "message": LOOKUP_MESSAGE}
    _domain, provider = row
    raw = new_token(32)
    db.add(
        OidcLoginState(
            org_id=provider.org_id,
            provider_id=provider.id,
            state_hash=sha256_hex(raw),
            nonce=new_token(24),
            code_verifier_enc=encrypt_secret(new_token(48)),
            return_to="/portal",
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    return {"available": True, "continuation": raw, "message": LOOKUP_MESSAGE}


def _redirect_uri() -> str:
    return get_settings().api_public_url.rstrip("/") + "/api/v1/auth/sso/callback"


def _clear_flow_cookie(response: Response, name: str) -> None:
    settings = get_settings()
    response.delete_cookie(name, domain=settings.cookie_domain, path="/")


def _failure(code: str = "sign_in_failed") -> RedirectResponse:
    response = RedirectResponse(f"/login?sso_error={code}", status_code=302)
    _clear_flow_cookie(response, OIDC_STATE_COOKIE)
    return response


@router.get("/auth/sso/start")
def sso_start(continuation: str = Query(min_length=32, max_length=200), db: Session = Depends(get_raw_session)):
    set_tenant(db, None, platform=True)
    login = db.execute(
        select(OidcLoginState).where(OidcLoginState.state_hash == sha256_hex(continuation)).with_for_update()
    ).scalar_one_or_none()
    now = utcnow()
    if not login or login.used_at or login.expires_at <= now:
        return _failure()
    provider = db.get(OidcProvider, login.provider_id)
    if not provider or not provider.enabled:
        return _failure()
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.oidc_http_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            metadata = oidc.discover_provider(client, provider.issuer, set(settings.oidc_allowed_issuer_hosts))
        raw_state = new_token(32)
        login.state_hash = sha256_hex(raw_state)
        url = oidc.authorization_url(
            metadata,
            provider.client_id,
            _redirect_uri(),
            provider.scopes,
            raw_state,
            login.nonce,
            decrypt_secret(login.code_verifier_enc),
        )
    except oidc.OidcProtocolError:
        return _failure("provider_unavailable")
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        raw_state,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    return response


@router.get("/auth/sso/callback")
def sso_callback(
    request: Request,
    state: str = Query(min_length=20, max_length=200),
    code: str = Query(min_length=1, max_length=4000),
    db: Session = Depends(get_raw_session),
):
    if request.cookies.get(OIDC_STATE_COOKIE) != state:
        return _failure()
    set_tenant(db, None, platform=True)
    login = db.execute(
        select(OidcLoginState).where(OidcLoginState.state_hash == sha256_hex(state)).with_for_update()
    ).scalar_one_or_none()
    now = utcnow()
    if not login or login.used_at or login.expires_at <= now:
        return _failure()
    provider = db.get(OidcProvider, login.provider_id)
    if not provider or not provider.enabled:
        return _failure()
    login.used_at = now
    db.commit()  # consume before contacting the IdP; a callback is never replayable
    set_tenant(db, None, platform=True)
    settings = get_settings()
    try:
        with httpx.Client(timeout=settings.oidc_http_timeout_seconds, follow_redirects=False, trust_env=False) as client:
            metadata = oidc.discover_provider(client, provider.issuer, set(settings.oidc_allowed_issuer_hosts))
            token = oidc.exchange_code(
                client,
                metadata,
                provider.client_id,
                decrypt_secret(provider.client_secret_enc) if provider.client_secret_enc else None,
                _redirect_uri(),
                code,
                decrypt_secret(login.code_verifier_enc),
            )
            identity, _ = oidc.fetch_and_validate_id_token(
                client, provider, metadata, token["id_token"], login.nonce, now
            )
        resolution = oidc.resolve_identity(db, provider, identity, now=now)
    except oidc.OidcProtocolError:
        return _failure()
    audit.record(
        db,
        org_id=provider.org_id,
        actor_kind="user",
        actor_id=resolution.user.id,
        action="auth.oidc",
        resource_type="user",
        resource_id=resolution.user.id,
        data={"provider_id": str(provider.id), "linked_via": resolution.linked_via},
    )
    if resolution.user.mfa_enabled:
        raw_challenge = new_token(32)
        db.add(
            SsoMfaChallenge(
                org_id=provider.org_id,
                user_id=resolution.user.id,
                provider_id=provider.id,
                token_hash=sha256_hex(raw_challenge),
                expires_at=now + timedelta(minutes=5),
            )
        )
        response = RedirectResponse("/login?sso_mfa=1", status_code=302)
        response.set_cookie(
            SSO_MFA_COOKIE,
            raw_challenge,
            max_age=300,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            domain=settings.cookie_domain,
            path="/",
        )
    else:
        response = RedirectResponse(login.return_to, status_code=302)
        from ..services import auth as auth_service

        auth_service.create_session(
            db,
            resolution.user,
            response,
            org_id=provider.org_id,
            ip=request.client.host if request.client else None,
            ua=request.headers.get("user-agent"),
            mfa_passed=True,
            auth_method="oidc",
            oidc_provider_id=provider.id,
        )
    _clear_flow_cookie(response, OIDC_STATE_COOKIE)
    return response


@router.post("/auth/sso/mfa")
def sso_mfa(
    body: SsoMfaIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_raw_session),
):
    raw = request.cookies.get(SSO_MFA_COOKIE) or ""
    set_tenant(db, None, platform=True)
    challenge = db.execute(
        select(SsoMfaChallenge).where(SsoMfaChallenge.token_hash == sha256_hex(raw)).with_for_update()
    ).scalar_one_or_none()
    now = utcnow()
    if not challenge or challenge.used_at or challenge.expires_at <= now or challenge.attempts >= 5:
        raise Unauthorized("The sign-in challenge expired.", code="sso_mfa_expired")
    from ..models import User
    from ..services import auth as auth_service

    user = db.get(User, challenge.user_id)
    challenge.attempts += 1
    if not user or user.disabled or not auth_service.verify_totp(user, body.code):
        raise Unauthorized("The verification code is invalid.", code="invalid_mfa")
    challenge.used_at = now
    _, session = auth_service.create_session(
        db,
        user,
        response,
        org_id=challenge.org_id,
        ip=request.client.host if request.client else None,
        ua=request.headers.get("user-agent"),
        mfa_passed=True,
        auth_method="oidc",
        oidc_provider_id=challenge.provider_id,
    )
    _clear_flow_cookie(response, SSO_MFA_COOKIE)
    from .auth import me_payload

    return me_payload(db, user, session)
