import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import (
    IdempotencyRecord,
    Inventory,
    Order,
    OrderEvent,
    OutboxEvent,
    Reservation,
)
from orderflow.schemas.contracts import OrderInput, ProductPatch, StockInput
from orderflow.services import catalog, orders


def command(*product_ids, quantity=1):
    return OrderInput(items=[{"product_id": item, "quantity": quantity} for item in product_ids])


def test_reserve_replay_and_snapshot(actors, product_factory):
    product_id = product_factory(stock=3, price=100)
    actor = actors["customer"]
    key = uuid.uuid4()
    first = orders.create_order(actor, command(product_id, quantity=2), key)
    replay = orders.create_order(actor, command(product_id, quantity=2), key)
    assert first.status == replay.status == 201
    assert first.body == replay.body and replay.replayed
    catalog.patch_product(actors["admin"], product_id, ProductPatch(price_minor=500), uuid.uuid4())
    assert orders.get_order(actor, uuid.UUID(first.body["id"]))["total_minor"] == 200
    with session_factory()() as session:
        inventory = session.get(Inventory, product_id)
        assert (inventory.on_hand, inventory.reserved) == (3, 2)
        for model in [Order, OrderEvent, OutboxEvent, Reservation]:
            assert session.scalar(select(func.count()).select_from(model)) == 1
    with pytest.raises(DomainError, match="different request"):
        orders.create_order(actor, command(product_id), key)


def test_multi_item_failure_is_atomic_and_cached(actors, product_factory):
    good, empty = product_factory(stock=5), product_factory(stock=0)
    key = uuid.uuid4()
    result = orders.create_order(actors["customer"], command(good, empty), key)
    assert result.status == 409
    catalog.adjust_stock(
        actors["admin"], empty, StockInput(delta=10, reason="Delivery"), uuid.uuid4()
    )
    assert orders.create_order(actors["customer"], command(good, empty), key).replayed
    with session_factory()() as session:
        assert session.get(Inventory, good).reserved == 0
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2


def test_cancel_release_and_ownership(actors, product_factory):
    product_id = product_factory(stock=1)
    result = orders.create_order(actors["customer"], command(product_id), uuid.uuid4())
    identifier = uuid.UUID(result.body["id"])
    with pytest.raises(DomainError) as error:
        orders.get_order(actors["other"], identifier)
    assert error.value.status == 404
    assert (
        orders.cancel_order(actors["customer"], identifier, uuid.uuid4()).body["status"]
        == "cancelled"
    )
    orders.cancel_order(actors["customer"], identifier, uuid.uuid4())
    with session_factory()() as session:
        assert session.get(Inventory, product_id).reserved == 0
        assert session.get(Order, identifier).version == 2


def test_expiry_is_repeatable(actors, product_factory):
    product_id = product_factory()
    result = orders.create_order(actors["customer"], command(product_id), uuid.uuid4())
    with session_factory().begin() as session:
        session.get(Order, uuid.UUID(result.body["id"])).expires_at = database_now(
            session
        ) - timedelta(seconds=1)
    assert orders.expire_reservations() == 1
    assert orders.expire_reservations() == 0
    with session_factory()() as session:
        assert session.get(Inventory, product_id).reserved == 0
        assert session.scalar(select(Reservation.status)) == "released"


def test_stock_cannot_go_below_reservations(actors, product_factory):
    product_id = product_factory(stock=2)
    orders.create_order(actors["customer"], command(product_id), uuid.uuid4())
    result = catalog.adjust_stock(
        actors["admin"], product_id, StockInput(delta=-2, reason="Audit"), uuid.uuid4()
    )
    assert result.status == 409
    with pytest.raises(DomainError):
        catalog.adjust_stock(
            actors["customer"], product_id, StockInput(delta=1, reason="Hack"), uuid.uuid4()
        )


def test_duplicate_sku_returns_conflict(actors):
    from orderflow.schemas.contracts import ProductInput

    product = ProductInput(sku="test", name="Test", price_minor=100)
    assert catalog.create_product(actors["admin"], product, uuid.uuid4()).status == 201
    assert catalog.create_product(actors["admin"], product, uuid.uuid4()).status == 409
