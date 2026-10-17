from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

_ph = PasswordHasher()  # argon2id with library defaults (OWASP-compliant parameters)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        # Constant-ish time: still do work to avoid user-enumeration by timing.
        _ph.hash("timing-equalizer")
        return False
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _fernet() -> Fernet:
    s = get_settings()
    key = s.encryption_key
    if not key:
        # Dev only: deterministic derivation from secret_key. Production requires explicit key.
        key = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + s.secret_key).encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:  # pragma: no cover - indicates key rotation problem
        raise ValueError("credential could not be decrypted (wrong encryption key?)") from e


def fingerprint(secret: str) -> str:
    return sha256_hex(secret)[:12]


def hmac_sign(key: str, message: str) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def hmac_verify(key: str, message: str, signature: str) -> bool:
    return hmac.compare_digest(hmac_sign(key, message), signature or "")
