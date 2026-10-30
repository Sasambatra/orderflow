import uuid
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from orderflow.core.security import Actor
from orderflow.domain.errors import DomainError
from orderflow.services.auth import authenticate

bearer = HTTPBearer(auto_error=False)


def current_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise DomainError("invalid_token", "Authentication required", 401)
    return authenticate(credentials.credentials)


def admin_actor(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
    if actor.role != "admin":
        raise DomainError("forbidden", "Administrator access required", 403)
    return actor


def idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", max_length=36)],
) -> uuid.UUID:
    try:
        key = uuid.UUID(value)
        if key.version != 4:
            raise ValueError("UUIDv4 required")
        return key
    except ValueError as error:
        raise DomainError(
            "invalid_idempotency_key", "Idempotency-Key must be a UUIDv4", 422
        ) from error


CurrentActor = Annotated[Actor, Depends(current_actor)]
AdminActor = Annotated[Actor, Depends(admin_actor)]
IdempotencyKey = Annotated[uuid.UUID, Depends(idempotency_key)]
