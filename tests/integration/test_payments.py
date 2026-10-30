import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import Inventory, Order, OrderEvent, Payment
from orderflow.schemas.contracts import OrderInput, PaymentWebhook
from orderflow.services import orders, payments


def prepared(actor, product_id):
    order = orders.create_order(
        actor, OrderInput(items=[{"product_id": product_id, "quantity": 1}]), uuid.uuid4()
    ).body
    payment = payments.create_payment(actor, uuid.UUID(order["id"]), uuid.uuid4()).body
    return order, payment


def event_for(order, payment, **overrides):
    body = {
        "event_id": str(uuid.uuid4()),
        "type": "payment.succeeded",
        "payment_id": payment["provider_payment_id"],
        "order_id": order["id"],
        "amount_minor": order["total_minor"],
        "currency": "RUB",
        **overrides,
    }
    raw = json.dumps(body).encode()
    return PaymentWebhook(**body), raw


def test_payment_applies_only_once(actors, product_factory):
    product_id = product_factory(stock=2)
    order, payment = prepared(actors["customer"], product_id)
    event = event_for(order, payment)
    first = payments.handle_webhook(*event)
    assert first.body["outcome"] == "applied"
    assert payments.handle_webhook(*event).replayed
    assert payments.handle_webhook(*event_for(order, payment)).body["outcome"] == "ignored"
    payments.handle_webhook(*event_for(order, payment, type="payment.failed"))
    with session_factory()() as session:
        inventory = session.get(Inventory, product_id)
        assert (inventory.on_hand, inventory.reserved) == (1, 0)
        assert session.get(Payment, uuid.UUID(payment["id"])).provider_status == "succeeded"
        assert session.scalar(select(func.count()).select_from(OrderEvent)) == 2
    assert (
        orders.cancel_order(actors["customer"], uuid.UUID(order["id"]), uuid.uuid4()).status == 409
    )


@pytest.mark.parametrize("terminal", ["cancelled", "expired"])
def test_late_payment_requires_refund(actors, product_factory, terminal):
    product_id = product_factory(stock=1)
    order, payment = prepared(actors["customer"], product_id)
    if terminal == "cancelled":
        orders.cancel_order(actors["customer"], uuid.UUID(order["id"]), uuid.uuid4())
    else:
        with session_factory().begin() as session:
            session.get(Order, uuid.UUID(order["id"])).expires_at = database_now(
                session
            ) - timedelta(seconds=1)
    assert payments.handle_webhook(*event_for(order, payment)).body["outcome"] == "refund_required"
    with session_factory()() as session:
        inventory = session.get(Inventory, product_id)
        assert (inventory.on_hand, inventory.reserved) == (1, 0)
        assert session.get(Order, uuid.UUID(order["id"])).status == terminal


def test_additional_success_and_failed_then_success(actors, product_factory):
    order, first = prepared(actors["customer"], product_factory())
    second = payments.create_payment(actors["customer"], uuid.UUID(order["id"]), uuid.uuid4()).body
    payments.handle_webhook(*event_for(order, first, type="payment.failed"))
    assert payments.handle_webhook(*event_for(order, first)).body["outcome"] == "applied"
    assert payments.handle_webhook(*event_for(order, second)).body["outcome"] == "refund_required"


def test_amount_mismatch_and_event_payload_mismatch(actors, product_factory):
    order, payment = prepared(actors["customer"], product_factory())
    bad = event_for(order, payment, amount_minor=1)
    assert payments.handle_webhook(*bad).status == 409
    assert payments.handle_webhook(*bad).replayed
    with pytest.raises(DomainError):
        payments.handle_webhook(*event_for(order, payment, event_id=str(bad[0].event_id)))
    assert payments.handle_webhook(*event_for(order, payment)).status == 200


def test_payment_limit_and_idempotency(actors, product_factory):
    order, payment = prepared(actors["customer"], product_factory())
    order_id = uuid.UUID(order["id"])
    key = uuid.uuid4()
    first = payments.create_payment(actors["customer"], order_id, key)
    assert payments.create_payment(actors["customer"], order_id, key).body == first.body
    assert payments.create_payment(actors["customer"], order_id, uuid.uuid4()).status == 201
    assert payments.create_payment(actors["customer"], order_id, uuid.uuid4()).status == 409


def test_payment_creation_after_expiry_commits_release(actors, product_factory):
    order, payment = prepared(actors["customer"], product_factory())
    with session_factory().begin() as session:
        session.get(Order, uuid.UUID(order["id"])).expires_at = database_now(session) - timedelta(
            seconds=1
        )
    assert (
        payments.create_payment(actors["customer"], uuid.UUID(order["id"]), uuid.uuid4()).status
        == 409
    )
    assert orders.get_order(actors["customer"], uuid.UUID(order["id"]))["status"] == "expired"
