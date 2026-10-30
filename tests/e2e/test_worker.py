import os
import subprocess
import sys
import time
from datetime import timedelta

import pytest
import redis
from sqlalchemy import func, select

from orderflow.core.config import get_settings
from orderflow.db.session import database_now, session_factory
from orderflow.models.entities import Notification, Order
from orderflow.services import outbox
from tests.integration.test_payments import prepared

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1", reason="Set RUN_E2E=1 to launch a real Celery worker"
    ),
]


def test_real_broker_worker_and_expiration(actors, product_factory, tmp_path):
    settings = get_settings()
    broker = redis.Redis.from_url(settings.redis_url)
    assert broker.connection_pool.connection_kwargs["db"] == 15, (
        "E2E uses dedicated Redis database 15"
    )
    broker.flushdb()
    env = dict(
        os.environ,
        DATABASE_URL=settings.database_url,
        REDIS_URL=settings.redis_url,
        JWT_SECRET=settings.jwt_secret.get_secret_value(),
        WEBHOOK_SECRET=settings.webhook_secret.get_secret_value(),
    )
    log = (tmp_path / "worker.log").open("w+")
    worker = subprocess.Popen(
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
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        order, _ = prepared(actors["customer"], product_factory())
        import uuid

        from orderflow.workers.tasks import expire

        with session_factory().begin() as session:
            session.get(Order, uuid.UUID(order["id"])).expires_at = database_now(
                session
            ) - timedelta(seconds=1)
        expire.delay()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            outbox.relay_events()
            with session_factory()() as session:
                status = session.get(Order, uuid.UUID(order["id"])).status
                notifications = session.scalar(select(func.count()).select_from(Notification))
            if status == "expired" and notifications == 2:
                break
            time.sleep(0.2)
        else:
            log.flush()
            pytest.fail("Worker did not finish: " + (tmp_path / "worker.log").read_text()[-3000:])
        assert outbox.backlog() == {"pending": 0, "dead": 0}
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
        log.close()
        broker.flushdb()
        broker.close()
