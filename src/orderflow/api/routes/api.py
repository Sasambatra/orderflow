import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from orderflow.api.dependencies import AdminActor, CurrentActor, IdempotencyKey
from orderflow.core.security import verify_webhook
from orderflow.db.session import session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import User
from orderflow.schemas.contracts import (
    Credentials,
    OrderInput,
    PaymentWebhook,
    ProductInput,
    ProductPatch,
    StockInput,
)
from orderflow.services import auth, catalog, notifications, orders, outbox, payments
from orderflow.services.common import Result

router = APIRouter(prefix="/api/v1")
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=512)]


def response(result: Result) -> JSONResponse:
    return JSONResponse(
        result.body,
        status_code=result.status,
        headers={"Idempotency-Replayed": str(result.replayed).lower()},
    )


@router.post("/auth/register", tags=["Authentication"], status_code=201)
def register(command: Credentials, request: Request):
    auth.rate_limit(request.client.host if request.client else "unknown", "register")
    return auth.register(str(command.email), command.password)


@router.post("/auth/login", tags=["Authentication"])
def login(command: Credentials, request: Request):
    auth.rate_limit(request.client.host if request.client else "unknown", "login")
    return auth.login(str(command.email), command.password)


@router.post("/auth/logout", tags=["Authentication"], status_code=204)
def logout(actor: CurrentActor):
    auth.logout(actor)
    return Response(status_code=204)


@router.get("/auth/me", tags=["Authentication"])
def me(actor: CurrentActor):
    with session_factory()() as session:
        return auth.profile(session.get(User, actor.id))


@router.get("/products", tags=["Catalog"])
def products(limit: Limit = 20, cursor: Cursor = None):
    return catalog.list_products(limit, cursor)


@router.get("/products/{product_id}", tags=["Catalog"])
def product(product_id: uuid.UUID):
    return catalog.get_product(product_id)


@router.post("/orders", tags=["Orders"], status_code=201)
def create_order(command: OrderInput, actor: CurrentActor, key: IdempotencyKey):
    return response(orders.create_order(actor, command, key))


@router.get("/orders", tags=["Orders"])
def my_orders(actor: CurrentActor, limit: Limit = 20, cursor: Cursor = None):
    return orders.list_orders(actor, limit, cursor)


@router.get("/orders/{order_id}", tags=["Orders"])
def get_order(order_id: uuid.UUID, actor: CurrentActor):
    return orders.get_order(actor, order_id)


@router.post("/orders/{order_id}/cancel", tags=["Orders"])
def cancel(order_id: uuid.UUID, actor: CurrentActor, key: IdempotencyKey):
    return response(orders.cancel_order(actor, order_id, key))


@router.post("/orders/{order_id}/payments", tags=["Payments"], status_code=201)
def create_payment(order_id: uuid.UUID, actor: CurrentActor, key: IdempotencyKey):
    return response(payments.create_payment(actor, order_id, key))


@router.get("/orders/{order_id}/payments", tags=["Payments"])
def order_payments(order_id: uuid.UUID, actor: CurrentActor):
    return payments.list_payments(actor, order_id)


async def signed_event(request: Request) -> tuple[PaymentWebhook, bytes]:
    raw = await request.body()
    verify_webhook(
        raw,
        request.headers.get("X-Mock-Timestamp", ""),
        request.headers.get("X-Mock-Signature", ""),
    )
    try:
        return PaymentWebhook.model_validate_json(raw), raw
    except ValidationError as error:
        raise DomainError("invalid_webhook", "Webhook payload is invalid", 422) from error


@router.post("/webhooks/mock-payments", tags=["Payments"])
def webhook(data: Annotated[tuple[PaymentWebhook, bytes], Depends(signed_event)]):
    return response(payments.handle_webhook(*data))


@router.get("/notifications", tags=["Notifications"])
def inbox(actor: CurrentActor, limit: Limit = 20, cursor: Cursor = None):
    return notifications.list_notifications(actor, limit, cursor)


@router.post("/admin/products", tags=["Admin"], status_code=201)
def add_product(command: ProductInput, actor: AdminActor, key: IdempotencyKey):
    return response(catalog.create_product(actor, command, key))


@router.patch("/admin/products/{product_id}", tags=["Admin"])
def update_product(
    product_id: uuid.UUID, command: ProductPatch, actor: AdminActor, key: IdempotencyKey
):
    return response(catalog.patch_product(actor, product_id, command, key))


@router.post("/admin/products/{product_id}/stock-adjustments", tags=["Admin"])
def stock(product_id: uuid.UUID, command: StockInput, actor: AdminActor, key: IdempotencyKey):
    return response(catalog.adjust_stock(actor, product_id, command, key))


@router.get("/admin/orders", tags=["Admin"])
def all_orders(actor: AdminActor, limit: Limit = 20, cursor: Cursor = None):
    return orders.list_orders(actor, limit, cursor, admin=True)


@router.get("/admin/outbox/dead", tags=["Admin"])
def dead_events(actor: AdminActor, limit: Limit = 20, cursor: Cursor = None):
    return outbox.list_dead(limit, cursor)


@router.post("/admin/outbox/{event_id}/retry", tags=["Admin"])
def retry_event(event_id: uuid.UUID, actor: AdminActor):
    return outbox.retry_dead(event_id)
