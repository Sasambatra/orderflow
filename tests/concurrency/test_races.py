import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from sqlalchemy import func, select

from orderflow.db.session import database_now, session_factory
from orderflow.models.entities import (
    IdempotencyRecord,
    Inventory,
    Notification,
    Order,
    OrderEvent,
    OutboxEvent,
)
from orderflow.schemas.contracts import OrderInput
from orderflow.services import orders, outbox, payments
from tests.integration.test_payments import event_for, prepared


def test_twenty_buyers_one_unit(actors, product_factory):
    product_id = product_factory(stock=1)
    command = OrderInput(items=[{"product_id": product_id, "quantity": 1}])
    barrier = Barrier(20)

    def buy(_):
        barrier.wait(timeout=10)
        return orders.create_order(actors["customer"], command, uuid.uuid4()).status

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(buy, range(20)))
    assert statuses.count(201) == 1 and statuses.count(409) == 19
    with session_factory()() as session:
        assert session.get(Inventory, product_id).reserved == 1
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_twenty_retries_one_order(actors, product_factory):
    product_id = product_factory(stock=1)
    command = OrderInput(items=[{"product_id": product_id, "quantity": 1}])
    key = uuid.uuid4()
    barrier = Barrier(20)

    def buy(_):
        barrier.wait(timeout=10)
        return orders.create_order(actors["customer"], command, key)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(buy, range(20)))
    assert {result.status for result in results} == {201}
    assert len({result.body["id"] for result in results}) == 1
    assert sum(result.replayed for result in results) == 19


def test_payment_cancel_race(actors, product_factory):
    product_id = product_factory(stock=1)
    order, payment = prepared(actors["customer"], product_id)
    event = event_for(order, payment)
    barrier = Barrier(2)

    def pay():
        barrier.wait()
        return payments.handle_webhook(*event)

    def cancel():
        barrier.wait()
        return orders.cancel_order(actors["customer"], uuid.UUID(order["id"]), uuid.uuid4())

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pay), pool.submit(cancel)]
        for future in futures:
            future.result()
    with session_factory()() as session:
        state = session.get(Order, uuid.UUID(order["id"])).status
        inventory = session.get(Inventory, product_id)
        assert inventory.reserved == 0
        assert inventory.on_hand == (0 if state == "paid" else 1)
        assert state in {"paid", "cancelled"}
        assert session.scalar(select(func.count()).select_from(OrderEvent)) == 2


def test_webhook_checks_clock_after_waiting_for_order_lock(actors, product_factory):
    order, payment = prepared(actors["customer"], product_factory(stock=1))
    identifier = uuid.UUID(order["id"])
    with session_factory().begin() as session:
        session.get(Order, identifier).expires_at = database_now(session) + timedelta(seconds=0.3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with session_factory().begin() as blocker:
            blocker.scalar(select(Order).where(Order.id == identifier).with_for_update())
            future = pool.submit(payments.handle_webhook, *event_for(order, payment))
            time.sleep(0.5)
        result = future.result()
    assert result.body["outcome"] == "refund_required"
    assert result.body["order_status"] == "expired"


def test_duplicate_consumers_create_one_notification(actors, product_factory):
    prepared(actors["customer"], product_factory())
    with session_factory()() as session:
        identifier = session.scalar(select(OutboxEvent.id))
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(outbox.process_event, [identifier] * 10))
    assert sum(results) == 1
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_crash_rolls_back_order_and_idempotency(actors, product_factory, monkeypatch):
    product_id = product_factory(stock=1)

    def crash(*args):
        raise RuntimeError("simulated process failure before commit")

    monkeypatch.setattr(orders, "add_history", crash)
    import pytest

    with pytest.raises(RuntimeError):
        orders.create_order(
            actors["customer"],
            OrderInput(items=[{"product_id": product_id, "quantity": 1}]),
            uuid.uuid4(),
        )
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 0
        assert session.get(Inventory, product_id).reserved == 0


def test_reverse_item_order_does_not_deadlock(actors, product_factory):
    first, second = product_factory(stock=2), product_factory(stock=2)
    barrier = Barrier(2)

    def buy(ids):
        barrier.wait()
        return orders.create_order(
            actors["customer"],
            OrderInput(items=[{"product_id": identifier, "quantity": 1} for identifier in ids]),
            uuid.uuid4(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(buy, [(first, second), (second, first)]))
    assert [result.status for result in results] == [201, 201]


def test_expiry_and_late_payment_race(actors, product_factory):
    product_id = product_factory(stock=1)
    order, payment = prepared(actors["customer"], product_id)
    with session_factory().begin() as session:
        session.get(Order, uuid.UUID(order["id"])).expires_at = database_now(session) - timedelta(
            seconds=1
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orders.expire_reservations)
        second = pool.submit(payments.handle_webhook, *event_for(order, payment))
        first.result()
        assert second.result().body["outcome"] == "refund_required"
    with session_factory()() as session:
        assert session.get(Inventory, product_id).reserved == 0
        assert session.get(Inventory, product_id).on_hand == 1
