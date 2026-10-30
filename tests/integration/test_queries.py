import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from orderflow.db.session import session_factory
from orderflow.models.entities import Inventory
from orderflow.schemas.contracts import OrderInput
from orderflow.services import orders, outbox, payments
from tests.integration.test_payments import event_for, prepared


def test_owner_pagination_and_admin_refund_visibility(client, actors, headers, product_factory):
    product_id = product_factory(stock=20)
    for _ in range(4):
        orders.create_order(
            actors["customer"],
            OrderInput(items=[{"product_id": product_id, "quantity": 1}]),
            uuid.uuid4(),
        )
    order, payment = prepared(actors["other"], product_id)
    orders.cancel_order(actors["other"], uuid.UUID(order["id"]), uuid.uuid4())
    payments.handle_webhook(*event_for(order, payment))
    first = client.get("/api/v1/orders?limit=2", headers=headers["customer"]).json()
    second = client.get(
        "/api/v1/orders",
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers=headers["customer"],
    ).json()
    ids = [row["id"] for row in first["items"] + second["items"]]
    assert len(set(ids)) == 4 and order["id"] not in ids
    assert second["next_cursor"] is None
    admin = client.get("/api/v1/admin/orders", headers=headers["admin"]).json()["items"]
    assert len(admin) == 5
    problem = next(row for row in admin if row["id"] == order["id"])
    assert problem["refund_required"] == [
        {"payment_id": payment["id"], "amount_minor": order["total_minor"]}
    ]
    assert (
        client.get(f"/api/v1/orders/{order['id']}/payments", headers=headers["other"]).json()[
            "items"
        ][0]["application_status"]
        == "refund_required"
    )
    outbox.relay_events(publisher=outbox.process_event)
    inbox = client.get("/api/v1/notifications?limit=2", headers=headers["other"]).json()
    remaining = client.get(
        "/api/v1/notifications",
        params={"limit": 2, "cursor": inbox["next_cursor"]},
        headers=headers["other"],
    ).json()
    assert len(inbox["items"]) == 2 and len(remaining["items"]) == 1


def test_database_enforces_inventory_invariant(product_factory):
    identifier = product_factory(stock=1)
    with pytest.raises(IntegrityError):
        with session_factory().begin() as session:
            session.get(Inventory, identifier).reserved = 2
    with session_factory()() as session:
        assert session.get(Inventory, identifier).reserved == 0
