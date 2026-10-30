import uuid
from datetime import timedelta
from functools import lru_cache

import redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from orderflow.core.config import get_settings
from orderflow.core.security import (
    Actor,
    create_token,
    decode_token,
    password_hash,
    verify_password,
)
from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import AuthSession, User


def profile(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email_normalized,
        "role": user.role,
        "is_active": user.is_active,
    }


@lru_cache
def _dummy_password() -> str:
    return password_hash("this-account-does-not-exist")


def register(email: str, password: str) -> dict:
    encoded = password_hash(password)
    try:
        with session_factory().begin() as session:
            user = User(email_normalized=email.casefold(), password_hash=encoded)
            session.add(user)
            session.flush()
            result = profile(user)
        return result
    except IntegrityError as error:
        raise DomainError("email_exists", "An account with this email already exists") from error


def login(email: str, password: str) -> dict:
    with session_factory().begin() as session:
        user = session.scalar(select(User).where(User.email_normalized == email.casefold()))
        valid = verify_password(password, user.password_hash if user else _dummy_password())
        if not user or not valid or not user.is_active:
            raise DomainError("invalid_credentials", "Invalid email or password", 401)
        auth_session = AuthSession(
            user_id=user.id,
            expires_at=database_now(session)
            + timedelta(seconds=get_settings().access_token_ttl_seconds),
        )
        session.add(auth_session)
        session.flush()
        token = create_token(user.id, auth_session.id)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": get_settings().access_token_ttl_seconds,
    }


def authenticate(token: str) -> Actor:
    claims = decode_token(token)
    with session_factory()() as session:
        auth_session = session.get(AuthSession, uuid.UUID(claims["sid"]))
        user = session.get(User, uuid.UUID(claims["sub"]))
        if (
            not auth_session
            or not user
            or auth_session.user_id != user.id
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= database_now(session)
            or not user.is_active
        ):
            raise DomainError("invalid_token", "Authentication required", 401)
        return Actor(user.id, user.role, auth_session.id)


def logout(actor: Actor) -> None:
    with session_factory().begin() as session:
        auth_session = session.get(AuthSession, actor.session_id)
        if auth_session and auth_session.revoked_at is None:
            auth_session.revoked_at = database_now(session)


def rate_limit(peer: str, action: str) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    from hashlib import sha256

    key = "orderflow:rate:" + sha256(f"{action}:{peer}".encode()).hexdigest()
    client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        count = client.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
            1,
            key,
        )
    except redis.RedisError as error:
        raise DomainError(
            "auth_unavailable", "Authentication temporarily unavailable", 503
        ) from error
    finally:
        client.close()
    if count > 30:
        raise DomainError("rate_limited", "Too many authentication requests", 429)
