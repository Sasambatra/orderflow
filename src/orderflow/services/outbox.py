import uuid
from datetime import timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from orderflow.core.logging import log_event
from orderflow.core.pagination import decode_cursor, encode_cursor
from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError, missing_resource
from orderflow.models.entities import Notification, OutboxEvent
from orderflow.services.common import run_transaction


def claim_events(limit: int = 100) -> list[tuple[uuid.UUID, uuid.UUID]]:
    def operation(session):
        now = database_now(session)
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.processed_at.is_(None),
                OutboxEvent.dead_at.is_(None),
                OutboxEvent.next_dispatch_at <= now,
                (OutboxEvent.lease_until.is_(None) | (OutboxEvent.lease_until <= now)),
            )
            .order_by(OutboxEvent.next_dispatch_at, OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        ).all()
        claims = []
        for event in events:
            event.lease_token = uuid.uuid4()
            event.lease_until = now + timedelta(seconds=30)
            event.dispatch_attempts += 1
            claims.append((event.id, event.lease_token))
        return claims

    return run_transaction(operation)


def finish_dispatch(identifier: uuid.UUID, token: uuid.UUID, *, success: bool) -> bool:
    def operation(session):
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == identifier).with_for_update()
        )
        if not event or event.lease_token != token:
            return False
        now = database_now(session)
        if success:
            event.published_at = now
            event.last_error = None
        else:
            event.last_error = "broker_unavailable"
        # Published events are retried until the consumer commits processed_at.
        event.next_dispatch_at = now + timedelta(
            seconds=30 if success else min(60, 2 ** min(event.dispatch_attempts, 6))
        )
        event.lease_token = None
        event.lease_until = None
        return True

    return run_transaction(operation)


def relay_events(limit: int = 100, publisher=None) -> dict:
    if publisher is None:
        from orderflow.workers.tasks import process_event

        def publisher(identifier):
            return process_event.apply_async(args=[str(identifier)], retry=False)

    published = failed = 0
    for identifier, token in claim_events(limit):
        try:
            publisher(identifier)
        except Exception:
            finish_dispatch(identifier, token, success=False)
            failed += 1
        else:
            finish_dispatch(identifier, token, success=True)
            published += 1
    log_event("outbox_relay", published=published, failed=failed)
    return {"published": published, "failed": failed}


def process_event(identifier: uuid.UUID) -> bool:
    def operation(session):
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == identifier).with_for_update()
        )
        if event is None or event.processed_at is not None or event.dead_at is not None:
            return False
        payload = event.payload
        if payload.get("schema_version") != 1 or event.event_type not in {
            "order.awaiting_payment",
            "order.paid",
            "order.cancelled",
            "order.expired",
            "payment.refund_required",
        }:
            raise ValueError("Unsupported event schema or type")
        user_id = uuid.UUID(payload["user_id"])
        text = f"Order {payload['order_id']}: {event.event_type}; {payload['total_minor']} {payload['currency']} minor units."
        session.execute(
            insert(Notification)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                event_id=event.id,
                channel="inbox",
                message=text,
            )
            .on_conflict_do_nothing(constraint="uq_notifications_event_channel")
        )
        event.processed_at = database_now(session)
        event.processing_attempts += 1
        event.last_error = None
        return True

    try:
        return run_transaction(operation)
    except Exception as error:
        error_type = type(error).__name__

        # This transaction is separate from the rolled-back business attempt.
        def failed(session):
            event = session.scalar(
                select(OutboxEvent).where(OutboxEvent.id == identifier).with_for_update()
            )
            if not event or event.processed_at is not None or event.dead_at is not None:
                return
            event.processing_attempts += 1
            event.last_error = error_type
            if event.processing_attempts >= 5:
                event.dead_at = database_now(session)

        run_transaction(failed)
        raise


def retry_dead(identifier: uuid.UUID) -> dict:
    def operation(session):
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == identifier).with_for_update()
        )
        if not event:
            raise missing_resource()
        if event.dead_at is None:
            raise DomainError("event_not_dead", "Only dead events may be retried")
        event.dead_at = None
        event.processing_attempts = 0
        event.last_error = None
        event.lease_token = None
        event.lease_until = None
        event.next_dispatch_at = database_now(session)
        return {"id": str(event.id), "status": "pending"}

    return run_transaction(operation)


def list_dead(limit: int, cursor: str | None) -> dict:
    with session_factory()() as session:
        query = select(OutboxEvent).where(OutboxEvent.dead_at.is_not(None))
        if cursor:
            query = query.where(tuple_(OutboxEvent.dead_at, OutboxEvent.id) < decode_cursor(cursor))
        rows = session.scalars(
            query.order_by(OutboxEvent.dead_at.desc(), OutboxEvent.id.desc()).limit(limit + 1)
        ).all()
        return {
            "items": [
                {
                    "id": str(event.id),
                    "event_type": event.event_type,
                    "last_error": event.last_error,
                    "processing_attempts": event.processing_attempts,
                    "dead_at": event.dead_at.isoformat(),
                }
                for event in rows[:limit]
            ],
            "next_cursor": encode_cursor(rows[limit - 1].dead_at, rows[limit - 1].id)
            if len(rows) > limit
            else None,
        }


def backlog() -> dict:
    with session_factory()() as session:
        pending = session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None), OutboxEvent.dead_at.is_(None))
        )
        dead = session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.dead_at.is_not(None))
        )
        return {"pending": pending, "dead": dead}
