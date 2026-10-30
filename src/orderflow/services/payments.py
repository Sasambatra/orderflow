import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from orderflow.core.logging import error_body
from orderflow.core.metrics import WEBHOOKS
from orderflow.core.security import Actor
from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError, missing_resource
from orderflow.models.entities import Order, Payment, PaymentEvent
from orderflow.schemas.contracts import PaymentWebhook
from orderflow.services.common import Result, add_outbox, idempotent, run_transaction
from orderflow.services.orders import finish_reservation, owned_order


def payment_view(payment: Payment) -> dict:
    return {
        "id": str(payment.id),
        "order_id": str(payment.order_id),
        "provider": payment.provider,
        "provider_payment_id": payment.provider_payment_id,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "provider_status": payment.provider_status,
        "application_status": payment.application_status,
    }


def create_payment(actor: Actor, order_id: uuid.UUID, key: uuid.UUID) -> Result:
    def operation(session: Session):
        order = owned_order(session, actor, order_id, lock=True)
        if order.status == "awaiting_payment" and database_now(session) >= order.expires_at:
            finish_reservation(session, order, "expired", actor)
            session.flush()
            return Result(409, error_body("payment_window_expired", "The reservation has expired"))
        if order.status != "awaiting_payment":
            raise DomainError("order_not_payable", "Order is not awaiting payment")
        count = session.scalar(
            select(func.count()).select_from(Payment).where(Payment.order_id == order_id)
        )
        if count >= 3:
            raise DomainError("payment_attempt_limit", "At most three payment attempts are allowed")
        payment = Payment(
            order_id=order_id,
            provider_payment_id="mock_" + uuid.uuid4().hex,
            amount_minor=order.total_minor,
            currency=order.currency,
        )
        session.add(payment)
        session.flush()
        return Result(201, payment_view(payment))

    return idempotent(actor, "POST", f"/api/v1/orders/{order_id}/payments", key, {}, operation)


def list_payments(actor: Actor, order_id: uuid.UUID) -> dict:
    with session_factory()() as session:
        owned_order(session, actor, order_id)
        rows = session.scalars(
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.created_at, Payment.id)
        ).all()
        return {"items": [payment_view(payment) for payment in rows]}


def handle_webhook(event: PaymentWebhook, raw_body: bytes) -> Result:
    fingerprint = hashlib.sha256(raw_body).hexdigest()

    def operation(session: Session):
        event_record_id = uuid.uuid4()
        inserted = session.scalar(
            insert(PaymentEvent)
            .values(
                id=event_record_id,
                provider="mock",
                event_id=event.event_id,
                payload_hash=fingerprint,
                event_type=event.type,
                outcome="processing",
                response_status=0,
                response_body={},
            )
            .on_conflict_do_nothing(constraint="uq_payment_events_provider_event")
            .returning(PaymentEvent.id)
        )
        record = session.scalar(
            select(PaymentEvent).where(
                PaymentEvent.provider == "mock", PaymentEvent.event_id == event.event_id
            )
        )
        if inserted is None:
            if record.payload_hash != fingerprint:
                raise DomainError(
                    "event_payload_mismatch", "Event ID already contains a different payload"
                )
            return Result(record.response_status, record.response_body, replayed=True)
        payment_info = session.execute(
            select(Payment.id, Payment.order_id).where(
                Payment.provider == "mock", Payment.provider_payment_id == event.payment_id
            )
        ).one_or_none()
        if payment_info is None:
            raise missing_resource()
        order = session.scalar(
            select(Order).where(Order.id == payment_info.order_id).with_for_update()
        )
        now = database_now(session)
        record.payment_id = payment_info.id
        if (
            order.id != event.order_id
            or order.total_minor != event.amount_minor
            or order.currency != event.currency
        ):
            result = Result(
                409,
                error_body(
                    "payment_details_mismatch", "Payment order, amount, or currency does not match"
                ),
            )
            record.outcome = "rejected"
        else:
            # Inventory is locked before the payment row, consistently with expiry/cancellation.
            from orderflow.models.entities import Inventory, OrderItem

            ids = list(
                session.scalars(
                    select(OrderItem.product_id)
                    .where(OrderItem.order_id == order.id)
                    .order_by(OrderItem.product_id)
                )
            )
            list(
                session.scalars(
                    select(Inventory)
                    .where(Inventory.product_id.in_(ids))
                    .order_by(Inventory.product_id)
                    .with_for_update()
                )
            )
            payment = session.scalar(
                select(Payment).where(Payment.id == payment_info.id).with_for_update()
            )
            if event.type == "payment.failed":
                if payment.provider_status != "succeeded":
                    payment.provider_status = "failed"
                record.outcome = "ignored" if payment.provider_status == "succeeded" else "failed"
            elif payment.application_status in {"applied", "refund_required", "refunded"}:
                record.outcome = "ignored"
            else:
                payment.provider_status = "succeeded"
                if order.status == "awaiting_payment" and now < order.expires_at:
                    order.paid_payment_id = payment.id
                    payment.application_status = "applied"
                    finish_reservation(session, order, "paid")
                    record.outcome = "applied"
                else:
                    if order.status == "awaiting_payment":
                        finish_reservation(session, order, "expired")
                    payment.application_status = "refund_required"
                    add_outbox(session, order, "payment.refund_required")
                    record.outcome = "refund_required"
            session.flush()
            result = Result(
                200,
                {
                    "received": True,
                    "outcome": record.outcome,
                    "order_status": order.status,
                    "payment": payment_view(payment),
                },
            )
        record.response_status = result.status
        record.response_body = result.body
        session.flush()
        return result

    result = run_transaction(operation)
    WEBHOOKS.labels("replayed" if result.replayed else result.body.get("outcome", "rejected")).inc()
    return result
