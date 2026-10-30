import uuid
from datetime import timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from orderflow.core.config import get_settings
from orderflow.core.pagination import decode_cursor, encode_cursor
from orderflow.core.security import Actor
from orderflow.db.session import database_now, session_factory
from orderflow.domain.errors import DomainError, missing_resource
from orderflow.models.entities import (
    Inventory,
    Order,
    OrderEvent,
    OrderItem,
    Payment,
    Product,
    Reservation,
)
from orderflow.schemas.contracts import OrderInput
from orderflow.services.common import Result, add_history, idempotent, run_transaction


def owned_order(
    session: Session, actor: Actor, order_id: uuid.UUID, *, lock: bool = False
) -> Order:
    query = select(Order).where(Order.id == order_id, Order.user_id == actor.id)
    order = session.scalar(query.with_for_update() if lock else query)
    if not order:
        raise missing_resource()
    return order


def order_view(session: Session, order: Order, *, history: bool = False) -> dict:
    items = session.scalars(
        select(OrderItem).where(OrderItem.order_id == order.id).order_by(OrderItem.product_id)
    ).all()
    result = {
        "id": str(order.id),
        "status": order.status,
        "version": order.version,
        "total_minor": order.total_minor,
        "currency": order.currency,
        "expires_at": order.expires_at.isoformat(),
        "created_at": order.created_at.isoformat(),
        "can_pay": order.status == "awaiting_payment" and database_now(session) < order.expires_at,
        "items": [
            {
                "product_id": str(item.product_id),
                "sku": item.sku_snapshot,
                "name": item.name_snapshot,
                "quantity": item.quantity,
                "unit_price_minor": item.unit_price_minor,
                "line_total_minor": item.line_total_minor,
            }
            for item in items
        ],
    }
    if history:
        events = session.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id, OrderEvent.version <= order.version)
            .order_by(OrderEvent.version)
        ).all()
        result["history"] = [
            {
                "version": event.version,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "reason": event.reason,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    return result


def create_order(actor: Actor, command: OrderInput, key: uuid.UUID) -> Result:
    def operation(session: Session):
        quantities = {item.product_id: item.quantity for item in command.items}
        ids = sorted(quantities)
        products = session.scalars(
            select(Product).where(Product.id.in_(ids)).order_by(Product.id).with_for_update()
        ).all()
        if len(products) != len(ids) or not all(product.is_active for product in products):
            raise missing_resource()
        inventories = {
            item.product_id: item
            for item in session.scalars(
                select(Inventory)
                .where(Inventory.product_id.in_(ids))
                .order_by(Inventory.product_id)
                .with_for_update()
            ).all()
        }
        for product in products:
            inventory = inventories[product.id]
            if inventory.on_hand - inventory.reserved < quantities[product.id]:
                raise DomainError(
                    "insufficient_stock",
                    "Not enough available stock",
                    details={"product_id": str(product.id)},
                )
        expires_at = database_now(session) + timedelta(
            seconds=get_settings().reservation_ttl_seconds
        )
        order = Order(
            user_id=actor.id,
            status="awaiting_payment",
            total_minor=sum(product.price_minor * quantities[product.id] for product in products),
            expires_at=expires_at,
        )
        session.add(order)
        session.flush()
        for product in products:
            quantity = quantities[product.id]
            inventories[product.id].reserved += quantity
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    sku_snapshot=product.sku,
                    name_snapshot=product.name,
                    unit_price_minor=product.price_minor,
                    quantity=quantity,
                    line_total_minor=product.price_minor * quantity,
                )
            )
            session.add(
                Reservation(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=quantity,
                    status="active",
                    expires_at=expires_at,
                )
            )
        add_history(session, order, None, "created", actor)
        session.flush()
        return Result(201, order_view(session, order))

    return idempotent(actor, "POST", "/api/v1/orders", key, command.canonical(), operation)


def finish_reservation(
    session: Session, order: Order, target: str, actor: Actor | None = None
) -> None:
    if order.status != "awaiting_payment" or target not in {"paid", "cancelled", "expired"}:
        raise RuntimeError("Illegal reservation transition")
    reservations = session.scalars(
        select(Reservation)
        .where(Reservation.order_id == order.id, Reservation.status == "active")
        .order_by(Reservation.product_id)
    ).all()
    if not reservations:
        raise RuntimeError("Active order must have a reservation")
    ids = [reservation.product_id for reservation in reservations]
    inventories = {
        item.product_id: item
        for item in session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_(ids))
            .order_by(Inventory.product_id)
            .with_for_update()
        ).all()
    }
    for reservation in reservations:
        inventory = inventories[reservation.product_id]
        if inventory.reserved < reservation.quantity:
            raise RuntimeError("Reservation counters are inconsistent")
        inventory.reserved -= reservation.quantity
        if target == "paid":
            inventory.on_hand -= reservation.quantity
        reservation.status = "consumed" if target == "paid" else "released"
    previous = order.status
    order.status = target
    order.version += 1
    add_history(session, order, previous, target, actor)


def cancel_order(actor: Actor, order_id: uuid.UUID, key: uuid.UUID) -> Result:
    def operation(session: Session):
        order = owned_order(session, actor, order_id, lock=True)
        if order.status == "paid":
            raise DomainError(
                "order_not_cancellable", "Paid orders require a separate refund process"
            )
        if order.status == "awaiting_payment":
            target = "expired" if database_now(session) >= order.expires_at else "cancelled"
            finish_reservation(session, order, target, actor)
            session.flush()
        return Result(200, order_view(session, order))

    return idempotent(actor, "POST", f"/api/v1/orders/{order_id}/cancel", key, {}, operation)


def get_order(actor: Actor, order_id: uuid.UUID) -> dict:
    with session_factory()() as session:
        return order_view(session, owned_order(session, actor, order_id), history=True)


def list_orders(actor: Actor, limit: int, cursor: str | None, *, admin: bool = False) -> dict:
    if admin and actor.role != "admin":
        raise DomainError("forbidden", "Administrator access required", 403)
    with session_factory()() as session:
        query = select(Order)
        if not admin:
            query = query.where(Order.user_id == actor.id)
        if cursor:
            query = query.where(tuple_(Order.created_at, Order.id) < decode_cursor(cursor))
        rows = session.scalars(
            query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)
        ).all()
        items = [order_view(session, order) for order in rows[:limit]]
        if admin:
            by_order = {item["id"]: item for item in items}
            for order in rows[:limit]:
                by_order[str(order.id)]["user_id"] = str(order.user_id)
                by_order[str(order.id)]["refund_required"] = []
            problems = session.scalars(
                select(Payment).where(
                    Payment.order_id.in_([order.id for order in rows[:limit]]),
                    Payment.application_status == "refund_required",
                )
            ).all()
            for payment in problems:
                by_order[str(payment.order_id)]["refund_required"].append(
                    {"payment_id": str(payment.id), "amount_minor": payment.amount_minor}
                )
        return {
            "items": items,
            "next_cursor": encode_cursor(rows[limit - 1].created_at, rows[limit - 1].id)
            if len(rows) > limit
            else None,
        }


def expire_reservations(limit: int = 100) -> int:
    def expire_one(session: Session) -> bool:
        order = session.scalar(
            select(Order)
            .where(Order.status == "awaiting_payment", Order.expires_at <= func.clock_timestamp())
            .order_by(Order.expires_at, Order.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if order is None:
            return False
        if database_now(session) < order.expires_at:
            return False
        finish_reservation(session, order, "expired")
        return True

    count = 0
    for _ in range(limit):
        if not run_transaction(expire_one):
            break
        count += 1
    return count
