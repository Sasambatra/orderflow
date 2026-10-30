"""Start a private Redis process, stop it during checkout, then recover delivery."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import redis
from celery import Celery
from sqlalchemy import func, select

from orderflow.core.config import get_settings
from orderflow.db.session import session_factory
from orderflow.models.entities import Notification
from orderflow.services import outbox

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="Set RUN_E2E=1"),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_redis_outage_preserves_order_and_recovers_inbox(headers, product_factory, tmp_path):
    binary = os.environ.get("REDIS_SERVER") or shutil.which("redis-server")
    if not binary:
        candidates = list(Path(".local/redis-source").glob("*/src/redis-server"))
        binary = str(candidates[0].resolve()) if candidates else None
    if not binary:
        pytest.skip("Private Redis process requires redis-server or REDIS_SERVER")
    redis_port, api_port = free_port(), free_port()
    while api_port == redis_port:
        api_port = free_port()
    url = f"redis://127.0.0.1:{redis_port}/0"
    settings = get_settings()
    env = dict(
        os.environ,
        DATABASE_URL=settings.database_url,
        REDIS_URL=url,
        JWT_SECRET=settings.jwt_secret.get_secret_value(),
        WEBHOOK_SECRET=settings.webhook_secret.get_secret_value(),
        RATE_LIMIT_ENABLED="true",
    )
    log = (tmp_path / "recovery.log").open("w+")
    processes = []

    def start(args):
        process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def start_redis():
        process = start(
            [
                binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(redis_port),
                "--save",
                "",
                "--appendonly",
                "no",
            ]
        )
        client = redis.Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2)
        for _ in range(50):
            try:
                if client.ping():
                    return process
            except redis.RedisError:
                time.sleep(0.05)
        raise RuntimeError("Private Redis did not start")

    try:
        broker_process = start_redis()
        start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "orderflow.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ]
        )
        client = httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=10)
        for _ in range(100):
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("Private API did not start")
        broker_process.terminate()
        broker_process.wait(timeout=5)
        import uuid

        response = client.post(
            "/api/v1/orders",
            json={"items": [{"product_id": str(product_factory()), "quantity": 1}]},
            headers={**headers["customer"], "Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 201, response.text
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"email": "customer@example.com", "password": "portfolio-password-123"},
            ).status_code
            == 503
        )
        assert outbox.backlog()["pending"] == 1
        start_redis()
        start(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "orderflow.workers.celery_app:app",
                "worker",
                "--pool=solo",
                "--loglevel=WARNING",
                "--without-gossip",
                "--without-mingle",
            ]
        )
        celery = Celery("recovery-test", broker=url)
        outbox.relay_events(
            publisher=lambda identifier: celery.send_task(
                "orderflow.process_event", args=[str(identifier)], retry=False
            )
        )
        for _ in range(100):
            with session_factory()() as session:
                if session.scalar(select(func.count()).select_from(Notification)) == 1:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("Delivery did not recover")
        assert outbox.backlog()["pending"] == 0
        client.close()
        celery.close()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        log.close()
