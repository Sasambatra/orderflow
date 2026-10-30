import uuid

from sqlalchemy import select, tuple_

from orderflow.core.pagination import decode_cursor, encode_cursor
from orderflow.core.security import Actor
from orderflow.db.session import session_factory
from orderflow.domain.errors import DomainError, missing_resource
from orderflow.models.entities import Inventory, Product, StockAdjustment
from orderflow.schemas.contracts import ProductInput, ProductPatch, StockInput
from orderflow.services.common import Result, idempotent


def product_view(product: Product, inventory: Inventory) -> dict:
    return {
        "id": str(product.id),
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "price_minor": product.price_minor,
        "currency": product.currency,
        "is_active": product.is_active,
        "on_hand": inventory.on_hand,
        "reserved": inventory.reserved,
        "available": inventory.on_hand - inventory.reserved,
    }


def list_products(limit: int = 20, cursor: str | None = None) -> dict:
    with session_factory()() as session:
        query = select(Product, Inventory).join(Inventory).where(Product.is_active.is_(True))
        if cursor:
            query = query.where(tuple_(Product.created_at, Product.id) < decode_cursor(cursor))
        rows = session.execute(
            query.order_by(Product.created_at.desc(), Product.id.desc()).limit(limit + 1)
        ).all()
        selected = rows[:limit]
        return {
            "items": [product_view(*row) for row in selected],
            "next_cursor": encode_cursor(selected[-1][0].created_at, selected[-1][0].id)
            if len(rows) > limit
            else None,
        }


def get_product(product_id: uuid.UUID) -> dict:
    with session_factory()() as session:
        product = session.get(Product, product_id)
        if not product or not product.is_active:
            raise missing_resource()
        return product_view(product, session.get(Inventory, product_id))


def create_product(actor: Actor, command: ProductInput, key: uuid.UUID) -> Result:
    require_admin(actor)

    def operation(session):
        product = Product(**command.model_dump())
        session.add(product)
        session.flush()
        inventory = Inventory(product_id=product.id)
        session.add(inventory)
        session.flush()
        return Result(201, product_view(product, inventory))

    return idempotent(
        actor, "POST", "/api/v1/admin/products", key, command.model_dump(mode="json"), operation
    )


def patch_product(
    actor: Actor, product_id: uuid.UUID, command: ProductPatch, key: uuid.UUID
) -> Result:
    require_admin(actor)

    def operation(session):
        product = session.scalar(select(Product).where(Product.id == product_id).with_for_update())
        if not product:
            raise missing_resource()
        for name, value in command.model_dump(exclude_unset=True).items():
            setattr(product, name, value)
        return Result(200, product_view(product, session.get(Inventory, product_id)))

    return idempotent(
        actor,
        "PATCH",
        f"/api/v1/admin/products/{product_id}",
        key,
        command.model_dump(mode="json", exclude_unset=True),
        operation,
    )


def adjust_stock(
    actor: Actor, product_id: uuid.UUID, command: StockInput, key: uuid.UUID
) -> Result:
    require_admin(actor)

    def operation(session):
        product = session.scalar(select(Product).where(Product.id == product_id).with_for_update())
        if not product:
            raise missing_resource()
        inventory = session.scalar(
            select(Inventory).where(Inventory.product_id == product_id).with_for_update()
        )
        if inventory.on_hand + command.delta < inventory.reserved:
            raise DomainError(
                "stock_below_reserved", "Stock cannot be lower than existing reservations"
            )
        inventory.on_hand += command.delta
        session.add(
            StockAdjustment(
                product_id=product_id, delta=command.delta, reason=command.reason, actor_id=actor.id
            )
        )
        return Result(200, product_view(product, inventory))

    return idempotent(
        actor,
        "POST",
        f"/api/v1/admin/products/{product_id}/stock-adjustments",
        key,
        command.model_dump(mode="json"),
        operation,
    )


def require_admin(actor: Actor) -> None:
    if actor.role != "admin":
        raise DomainError("forbidden", "Administrator access required", 403)
