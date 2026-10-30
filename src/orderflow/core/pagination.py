import base64
import json
import uuid
from datetime import datetime

from orderflow.domain.errors import DomainError


def encode_cursor(created_at: datetime, identifier: uuid.UUID) -> str:
    return (
        base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), str(identifier)]).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError("Invalid cursor")
        timestamp = datetime.fromisoformat(values[0])
        identifier = uuid.UUID(values[1])
        if timestamp.tzinfo is None:
            raise ValueError("Timezone required")
        return timestamp, identifier
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as error:
        raise DomainError("invalid_cursor", "Pagination cursor is invalid", 422) from error
