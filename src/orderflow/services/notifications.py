from sqlalchemy import select, tuple_

from orderflow.core.pagination import decode_cursor, encode_cursor
from orderflow.core.security import Actor
from orderflow.db.session import session_factory
from orderflow.models.entities import Notification


def list_notifications(actor: Actor, limit: int, cursor: str | None) -> dict:
    with session_factory()() as session:
        query = select(Notification).where(Notification.user_id == actor.id)
        if cursor:
            query = query.where(
                tuple_(Notification.created_at, Notification.id) < decode_cursor(cursor)
            )
        rows = session.scalars(
            query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "id": str(row.id),
                    "event_id": str(row.event_id),
                    "channel": row.channel,
                    "message": row.message,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows[:limit]
            ],
            "next_cursor": encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id)
            if len(rows) > limit
            else None,
        }
