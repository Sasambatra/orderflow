import random
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from orderflow.core.logging import error_body
from orderflow.core.metrics import DB_ACQUIRE, DB_RETRIES, IDEMPOTENCY_REPLAYS
from orderflow.core.security import Actor, request_digest
from orderflow.db.session import session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import IdempotencyRecord, Order, OrderEvent, OutboxEvent


@dataclass(frozen=True)
class Result:
    status: int
    body: dict
    replayed: bool = False


def run_transaction(operation):
    for attempt in range(3):
        try:
            with session_factory().begin() as session:
                with DB_ACQUIRE.time():
                    session.connection()
                return operation(session)
        except DBAPIError as error:
            if (
                getattr(error.orig, "sqlstate", None) not in {"40P01", "40001", "55P03"}
                or attempt == 2
            ):
                raise
            time.sleep(0.03 * (2**attempt) + random.random() * 0.02)
            DB_RETRIES.inc()
    raise RuntimeError("unreachable")


def idempotent(
    actor: Actor, method: str, path: str, key: uuid.UUID, payload: dict, operation
) -> Result:
    fingerprint = request_digest(payload)

    def transaction(session: Session):
        record_id = uuid.uuid4()
        inserted = session.scalar(
            insert(IdempotencyRecord)
            .values(
                id=record_id,
                actor_id=actor.id,
                method=method,
                path=path,
                key=key,
                request_hash=fingerprint,
                response_status=0,
                response_body={},
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_scope")
            .returning(IdempotencyRecord.id)
        )
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor.id,
                IdempotencyRecord.method == method,
                IdempotencyRecord.path == path,
                IdempotencyRecord.key == key,
            )
        )
        if inserted is None:
            if record.request_hash != fingerprint:
                raise DomainError(
                    "idempotency_key_reused", "Key was already used for a different request"
                )
            if not record.response_status:
                raise DomainError("request_in_progress", "Retry the request", 503)
            return Result(record.response_status, record.response_body, replayed=True)
        try:
            with session.begin_nested():
                result = operation(session)
                session.flush()
        except DomainError as error:
            result = Result(error.status, error_body(error.code, error.message, error.details))
        except IntegrityError as error:
            if (
                getattr(getattr(error.orig, "diag", None), "constraint_name", "")
                != "uq_products_sku"
            ):
                raise
            result = Result(409, error_body("sku_exists", "A product with this SKU already exists"))
        record.response_status = result.status
        record.response_body = result.body
        session.flush()
        return result

    result = run_transaction(transaction)
    if result.replayed:
        IDEMPOTENCY_REPLAYS.inc()
    return result


def add_outbox(session: Session, order: Order, event_type: str) -> OutboxEvent:
    event = OutboxEvent(
        aggregate_id=order.id,
        event_type=event_type,
        payload={
            "schema_version": 1,
            "order_id": str(order.id),
            "user_id": str(order.user_id),
            "status": order.status,
            "total_minor": order.total_minor,
            "currency": order.currency,
        },
    )
    session.add(event)
    return event


def add_history(
    session: Session, order: Order, previous: str | None, reason: str, actor: Actor | None = None
) -> None:
    session.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            from_status=previous,
            to_status=order.status,
            reason=reason,
            actor_type="user" if actor else "system",
            actor_id=actor.id if actor else None,
        )
    )
    add_outbox(session, order, f"order.{order.status}")
