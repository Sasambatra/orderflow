"""A reproducible last-unit HTTP race, not a capacity or production SLA claim."""

import json
import platform
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import httpx


def main():
    credentials = json.loads(Path(".local/demo-credentials.json").read_text())
    with httpx.Client(base_url="http://127.0.0.1:8001", timeout=30) as client:

        def login(role):
            result = client.post("/api/v1/auth/login", json=credentials[role])
            result.raise_for_status()
            return {"Authorization": "Bearer " + result.json()["access_token"]}

        admin, customer = login("admin"), login("customer")
        product = client.post(
            "/api/v1/admin/products",
            json={
                "sku": "RACE-" + uuid.uuid4().hex,
                "name": "Last unit benchmark",
                "price_minor": 10000,
            },
            headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        product.raise_for_status()
        product_id = product.json()["id"]
        stock = client.post(
            f"/api/v1/admin/products/{product_id}/stock-adjustments",
            json={"delta": 1, "reason": "Benchmark fixture"},
            headers={**admin, "Idempotency-Key": str(uuid.uuid4())},
        )
        stock.raise_for_status()
        barrier = Barrier(20)

        def request(_):
            barrier.wait(timeout=10)
            start = time.perf_counter()
            result = client.post(
                "/api/v1/orders",
                json={"items": [{"product_id": product_id, "quantity": 1}]},
                headers={**customer, "Idempotency-Key": str(uuid.uuid4())},
            )
            return result.status_code, time.perf_counter() - start, result.json()

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(request, range(20)))
        counts = {
            str(status): sum(item[0] == status for item in results)
            for status in sorted({item[0] for item in results})
        }
        assert counts == {"201": 1, "409": 19}, counts
        latencies = sorted(item[1] * 1000 for item in results)
        report = {
            "measured_at": datetime.now(UTC).isoformat(),
            "scenario": "20 concurrent HTTP orders, distinct keys, one unit",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "responses": counts,
            "latency_ms": {
                "median": round(statistics.median(latencies), 2),
                "p95": round(latencies[18], 2),
                "max": round(latencies[-1], 2),
            },
            "limits": "Single local run, warm API, no network latency; not a throughput benchmark or SLA.",
        }
        Path("output").mkdir(exist_ok=True)
        Path("output/benchmark.json").write_text(json.dumps(report, indent=2))
        winner = next(item[2]["id"] for item in results if item[0] == 201)
        cleanup = client.post(
            f"/api/v1/orders/{winner}/cancel",
            headers={**customer, "Idempotency-Key": str(uuid.uuid4())},
        )
        cleanup.raise_for_status()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
