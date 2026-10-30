import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from orderflow.core.config import get_settings
from orderflow.domain.errors import DomainError

password_hasher = PasswordHash.recommended()


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID
    role: str
    session_id: uuid.UUID


def password_hash(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hasher.verify(password, encoded)


def create_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        result = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "sid", "jti", "iss", "aud"]},
        )
        uuid.UUID(result["sub"])
        uuid.UUID(result["sid"])
        return result
    except (jwt.PyJWTError, ValueError, TypeError) as error:
        raise DomainError("invalid_token", "Authentication required", 401) from error


def request_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def sign_webhook(body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    timestamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(
        get_settings().webhook_secret.get_secret_value().encode(),
        str(timestamp).encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return str(timestamp), signature


def verify_webhook(body: bytes, timestamp: str, signature: str) -> None:
    settings = get_settings()
    try:
        fresh = abs(time.time() - int(timestamp)) <= settings.webhook_tolerance_seconds
    except ValueError:
        fresh = False
    expected = hmac.new(
        settings.webhook_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not fresh or not signature.isascii() or not hmac.compare_digest(expected, signature):
        raise DomainError("invalid_signature", "Webhook authentication failed", 401)
