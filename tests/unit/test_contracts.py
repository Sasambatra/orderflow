import json
import time
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from orderflow.core.pagination import decode_cursor, encode_cursor
from orderflow.core.security import decode_token, request_digest, sign_webhook, verify_webhook
from orderflow.domain.errors import DomainError
from orderflow.schemas.contracts import OrderInput, ProductPatch, StockInput


@pytest.mark.parametrize("quantity", [0, -1, 101, True, 1.5, "2"])
def test_quantities_are_strict(quantity):
    with pytest.raises(ValidationError):
        OrderInput(items=[{"product_id": uuid.uuid4(), "quantity": quantity}])


def test_duplicate_products_and_unknown_input_rejected():
    item = {"product_id": uuid.uuid4(), "quantity": 1}
    with pytest.raises(ValidationError):
        OrderInput(items=[item, item])
    with pytest.raises(ValidationError):
        OrderInput(items=[item], price=1)


def test_canonical_order_ignores_item_order():
    items = [{"product_id": uuid.uuid4(), "quantity": 1} for _ in range(2)]
    assert request_digest(OrderInput(items=items).canonical()) == request_digest(
        OrderInput(items=items[::-1]).canonical()
    )


@pytest.mark.parametrize("value", ["garbage", "%%%%", "W10", "e30", "bnVsbA", "WyJ4IiwieCJd"])
def test_bad_cursor(value):
    with pytest.raises(DomainError):
        decode_cursor(value)


def test_cursor_round_trip():
    pair = (datetime.now(UTC), uuid.uuid4())
    assert decode_cursor(encode_cursor(*pair)) == pair


@pytest.mark.parametrize("body", [{}, {"name": None}, {"price_minor": -1}, {"is_active": None}])
def test_patch_rejects_empty_or_invalid(body):
    with pytest.raises(ValidationError):
        ProductPatch(**body)


def test_zero_adjustment_rejected():
    with pytest.raises(ValidationError):
        StockInput(delta=0, reason="test")


def test_hmac_checks_raw_body_and_freshness():
    body = json.dumps({"hello": "world"}).encode()
    timestamp, signature = sign_webhook(body)
    verify_webhook(body, timestamp, signature)
    for raw, ts, sig in [
        (body + b" ", timestamp, signature),
        (body, "invalid", signature),
        (body, timestamp, "wrong"),
    ]:
        with pytest.raises(DomainError):
            verify_webhook(raw, ts, sig)
    old, signature = sign_webhook(body, int(time.time()) - 1000)
    with pytest.raises(DomainError):
        verify_webhook(body, old, signature)


def test_invalid_jwt():
    with pytest.raises(DomainError):
        decode_token("not.a.token")
