import uuid

import pytest

from orderflow.core.config import get_settings
from orderflow.core.security import sign_webhook
from orderflow.db.session import session_factory
from orderflow.models.entities import User
from orderflow.services import auth, outbox
from tests.integration.test_payments import event_for


def test_auth_registration_login_logout(client):
    credentials = {"email": "new@example.com", "password": "strong-password-123"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 409
    assert (
        client.post(
            "/api/v1/auth/login", json={**credentials, "password": "wrong-password-123"}
        ).status_code
        == 401
    )
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "customer"
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_full_http_purchase(client, actors, headers, product_factory):
    product_id = product_factory(stock=1)
    key_headers = {**headers["customer"], "Idempotency-Key": str(uuid.uuid4())}
    body = {"items": [{"product_id": str(product_id), "quantity": 1}]}
    created = client.post("/api/v1/orders", json=body, headers=key_headers)
    assert created.status_code == 201, created.text
    assert created.headers["Idempotency-Replayed"] == "false"
    replay = client.post("/api/v1/orders", json=body, headers=key_headers)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert created.json() == replay.json()
    order = created.json()
    assert client.get(f"/api/v1/orders/{order['id']}", headers=headers["other"]).status_code == 404
    payment_response = client.post(
        f"/api/v1/orders/{order['id']}/payments",
        headers={**headers["customer"], "Idempotency-Key": str(uuid.uuid4())},
    )
    assert payment_response.status_code == 201
    _, raw = event_for(order, payment_response.json())
    timestamp, signature = sign_webhook(raw)
    webhook = client.post(
        "/api/v1/webhooks/mock-payments",
        content=raw,
        headers={
            "X-Mock-Timestamp": timestamp,
            "X-Mock-Signature": signature,
            "Content-Type": "application/json",
        },
    )
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["order_status"] == "paid"
    outbox.relay_events(publisher=outbox.process_event)
    inbox = client.get("/api/v1/notifications", headers=headers["customer"]).json()
    assert len(inbox["items"]) == 2
    assert client.get("/api/v1/notifications", headers=headers["other"]).json()["items"] == []


def test_admin_authorization_and_stock(client, headers):
    data = {"sku": "ABC", "name": "Test", "price_minor": 100}
    for who, expected in [(None, 401), ("customer", 403), ("admin", 201)]:
        h = {**(headers[who] if who else {}), "Idempotency-Key": str(uuid.uuid4())}
        result = client.post("/api/v1/admin/products", json=data, headers=h)
        assert result.status_code == expected, result.text
    product_id = result.json()["id"]
    result = client.post(
        f"/api/v1/admin/products/{product_id}/stock-adjustments",
        json={"delta": 5, "reason": "Delivery"},
        headers={**headers["admin"], "Idempotency-Key": str(uuid.uuid4())},
    )
    assert result.json()["available"] == 5
    assert client.get("/api/v1/admin/orders", headers=headers["customer"]).status_code == 403
    assert client.get("/metrics", headers=headers["admin"]).status_code == 200
    assert client.get("/metrics").status_code == 401


@pytest.mark.parametrize("key", [None, "garbage", str(uuid.uuid1())])
def test_required_uuid4_key(client, headers, product_factory, key):
    h = dict(headers["customer"])
    if key is not None:
        h["Idempotency-Key"] = key
    result = client.post(
        "/api/v1/orders",
        json={"items": [{"product_id": str(product_factory()), "quantity": 1}]},
        headers=h,
    )
    assert result.status_code == 422
    assert result.json()["request_id"] == result.headers["X-Request-ID"]


def test_validation_never_echoes_password(client):
    password = "my-secret-password"
    result = client.post("/api/v1/auth/login", json={"email": password, "password": password})
    assert result.status_code == 422
    assert password not in result.text
    assert client.get("/missing").status_code == 404


def test_unexpected_errors_have_safe_envelope(client, monkeypatch):
    from orderflow.services import catalog

    def broken(*args):
        raise RuntimeError("private exception details")

    monkeypatch.setattr(catalog, "list_products", broken)
    result = client.get("/api/v1/products")
    assert result.status_code == 500
    assert result.json()["error"]["code"] == "internal_error"
    assert result.json()["request_id"] == result.headers["X-Request-ID"]
    assert "private exception details" not in result.text


def test_body_limit_including_no_content_length(client):
    result = client.post(
        "/api/v1/webhooks/mock-payments", content=iter([b"x" * 40000, b"x" * 40000])
    )
    assert result.status_code == 413
    assert client.post("/api/v1/webhooks/mock-payments", content=b"{}").status_code == 401
    raw = b"{}"
    timestamp, signature = sign_webhook(raw)
    assert (
        client.post(
            "/api/v1/webhooks/mock-payments",
            content=raw,
            headers={"X-Mock-Timestamp": timestamp, "X-Mock-Signature": signature},
        ).status_code
        == 422
    )


def test_cursor_pagination_no_duplicates(client, product_factory, actors):
    for _ in range(5):
        product_factory()
    seen, cursor = [], None
    while True:
        result = client.get(
            "/api/v1/products", params={"limit": 2, **({"cursor": cursor} if cursor else {})}
        ).json()
        seen.extend(item["id"] for item in result["items"])
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    assert client.get("/api/v1/products?cursor=invalid").status_code == 422
    assert client.get("/health/ready").json()["status"] == "ready"


def test_disabled_user_and_rate_limiter(client, actors, headers, monkeypatch):
    with session_factory().begin() as session:
        session.get(User, actors["customer"].id).is_active = False
    assert client.get("/api/v1/auth/me", headers=headers["customer"]).status_code == 401
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)

    class RedisUnavailable:
        def eval(self, *args):
            import redis

            raise redis.ConnectionError("unavailable")

        def close(self):
            pass

    monkeypatch.setattr(auth.redis.Redis, "from_url", lambda *args, **kwargs: RedisUnavailable())
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": "portfolio-password-123"},
        ).status_code
        == 503
    )
