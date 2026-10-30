"""Exercise the public HTTP API; requires a running API, worker and demo seed."""

import json
import time
import uuid
from pathlib import Path

import httpx

from orderflow.core.security import sign_webhook


def main():
    credentials = json.loads(Path(".local/demo-credentials.json").read_text())
    with httpx.Client(base_url="http://127.0.0.1:8001", timeout=15) as client:
        response = client.post("/api/v1/auth/login", json=credentials["customer"])
        response.raise_for_status()
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        product = next(
            item for item in client.get("/api/v1/products").json()["items"] if item["available"] > 0
        )
        key = str(uuid.uuid4())
        body = {"items": [{"product_id": product["id"], "quantity": 1}]}
        order = client.post(
            "/api/v1/orders", json=body, headers={**headers, "Idempotency-Key": key}
        )
        assert order.status_code == 201, order.text
        replay = client.post(
            "/api/v1/orders", json=body, headers={**headers, "Idempotency-Key": key}
        )
        assert replay.json() == order.json() and replay.headers["Idempotency-Replayed"] == "true"
        order_id = order.json()["id"]
        payment = client.post(
            f"/api/v1/orders/{order_id}/payments",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert payment.status_code == 201, payment.text
        raw = json.dumps(
            {
                "event_id": str(uuid.uuid4()),
                "type": "payment.succeeded",
                "payment_id": payment.json()["provider_payment_id"],
                "order_id": order_id,
                "amount_minor": order.json()["total_minor"],
                "currency": "RUB",
            }
        ).encode()
        timestamp, signature = sign_webhook(raw)
        webhook_headers = {
            "X-Mock-Timestamp": timestamp,
            "X-Mock-Signature": signature,
            "Content-Type": "application/json",
        }
        paid = client.post("/api/v1/webhooks/mock-payments", content=raw, headers=webhook_headers)
        assert paid.status_code == 200 and paid.json()["order_status"] == "paid", paid.text
        duplicate = client.post(
            "/api/v1/webhooks/mock-payments", content=raw, headers=webhook_headers
        )
        assert duplicate.headers["Idempotency-Replayed"] == "true"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            inbox = client.get("/api/v1/notifications", headers=headers).json()["items"]
            if any(
                order_id in item["message"] and "order.paid" in item["message"] for item in inbox
            ):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Notification timeout; check worker, beat and outbox-status")
        print(
            json.dumps(
                {
                    "order_id": order_id,
                    "status": "paid",
                    "order_replay": True,
                    "webhook_replay": True,
                    "notification_received": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
