"""Measure checkout and catalog separately with sufficient stock and real HTTP."""

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx
import redis
from sqlalchemy import text

from orderflow.core.config import get_settings
from orderflow.db.session import session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requests", type=int, default=500, choices=range(20, 5001), metavar="20..5000"
    )
    parser.add_argument(
        "--concurrency", type=int, default=20, choices=range(1, 101), metavar="1..100"
    )
    parser.add_argument("--runs", type=int, default=3, choices=range(1, 11), metavar="1..10")
    args = parser.parse_args()
    credentials = json.loads(Path(".local/demo-credentials.json").read_text())
    with httpx.Client(
        base_url="http://127.0.0.1:8001",
        timeout=60,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=100),
    ) as client:

        def login(role):
            response = client.post("/api/v1/auth/login", json=credentials[role])
            response.raise_for_status()
            return {"Authorization": "Bearer " + response.json()["access_token"]}

        admin, customer = login("admin"), login("customer")

        def mutate(path, payload):
            response = client.post(
                path, json=payload, headers={**admin, "Idempotency-Key": str(uuid.uuid4())}
            )
            response.raise_for_status()
            return response.json()

        product = mutate(
            "/api/v1/admin/products",
            {"sku": "LOAD-" + uuid.uuid4().hex, "name": "Load test product", "price_minor": 10000},
        )
        mutate(
            f"/api/v1/admin/products/{product['id']}/stock-adjustments",
            {"delta": args.requests * args.runs + 10, "reason": "Sufficient stock load fixture"},
        )
        results = []
        stop = threading.Event()
        memory = []
        state_file = Path(".local/processes.json")
        api_pid = json.loads(state_file.read_text()).get("api") if state_file.exists() else None

        def sample_memory():
            while not stop.wait(0.05):
                try:
                    lines = Path(f"/proc/{api_pid}/status").read_text().splitlines()
                    memory.append(
                        int(next(line for line in lines if line.startswith("VmRSS:")).split()[1])
                    )
                except (OSError, StopIteration):
                    return

        monitor = threading.Thread(target=sample_memory)
        monitor.start()
        try:
            for run in range(args.runs):
                for action in ["create_order", "read_catalog"]:

                    def perform(_, action=action):
                        start = time.perf_counter()
                        try:
                            if action == "create_order":
                                response = client.post(
                                    "/api/v1/orders",
                                    json={"items": [{"product_id": product["id"], "quantity": 1}]},
                                    headers={**customer, "Idempotency-Key": str(uuid.uuid4())},
                                )
                            else:
                                response = client.get("/api/v1/products")
                            return response.status_code, time.perf_counter() - start
                        except httpx.TransportError:
                            return "transport_error", time.perf_counter() - start

                    # Ten warm-up requests are measured separately from the main sample.
                    if action == "read_catalog":
                        for i in range(10):
                            perform(i)
                    started = time.perf_counter()
                    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                        samples = list(pool.map(perform, range(args.requests)))
                    duration = time.perf_counter() - started
                    latencies = sorted(item[1] * 1000 for item in samples)
                    counts = dict(Counter(str(item[0]) for item in samples))
                    expected = "201" if action == "create_order" else "200"
                    result = {
                        "run": run + 1,
                        "action": action,
                        "requests": args.requests,
                        "concurrency": args.concurrency,
                        "duration_seconds": round(duration, 3),
                        "requests_per_second": round(args.requests / duration, 2),
                        "responses": counts,
                        "latency_ms": {
                            f"p{percentile}": round(
                                latencies[math.ceil(percentile / 100 * len(latencies)) - 1], 2
                            )
                            for percentile in [50, 95, 99]
                        },
                        "successful": counts == {expected: args.requests},
                    }
                    results.append(result)
                    print(json.dumps(result), flush=True)
        finally:
            stop.set()
            monitor.join()
        with session_factory()() as session:
            violations = session.scalar(
                text(
                    "SELECT count(*) FROM inventory i LEFT JOIN (SELECT product_id, sum(quantity) AS qty FROM reservations WHERE status='active' GROUP BY product_id) r USING(product_id) WHERE i.reserved != coalesce(r.qty,0) OR i.on_hand < i.reserved OR i.reserved < 0"
                )
            )
            postgres = session.scalar(text("SHOW server_version"))
        broker = redis.Redis.from_url(get_settings().redis_url)
        redis_version = broker.info("server")["redis_version"]
        broker.close()
        digest = hashlib.sha256()
        for path in sorted(Path("src").rglob("*.py")) + [Path("scripts/load_test.py")]:
            digest.update(str(path).encode() + path.read_bytes())
        commit = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
            or None
        )
        report = {
            "measured_at": datetime.now(UTC).isoformat(),
            "commit": commit,
            "source_sha256": digest.hexdigest(),
            "python": platform.python_version(),
            "postgresql": postgres,
            "redis": redis_version,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "api_processes": 1,
            "db_pool_size": 5,
            "db_max_overflow": 5,
            "api_rss_sampled_peak_kib": max(memory) if memory else None,
            "warmup": "10 catalog reads per run; warm API for writes; checkout starts without an additional warmup batch",
            "results": results,
            "inventory_invariant_violations": violations,
            "limits": "Local single-host loopback HTTP; no TLS or external network. RSS sampled every 50ms, not total stack memory. No container resource limits. Not an SLA.",
        }
        Path("output").mkdir(exist_ok=True)
        output = Path(f"output/load-c{args.concurrency}.json")
        output.write_text(json.dumps(report, indent=2))
        if violations or not all(result["successful"] for result in results):
            raise SystemExit(f"Load check failed; inspect {output}")
        print(f"Saved {output}; inventory invariant violations: {violations}")


if __name__ == "__main__":
    main()
