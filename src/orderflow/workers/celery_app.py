from celery import Celery

from orderflow.core.config import get_settings

app = Celery("orderflow", broker=get_settings().redis_url, include=["orderflow.workers.tasks"])
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    broker_transport_options={
        "socket_timeout": 2,
        "socket_connect_timeout": 2,
        "visibility_timeout": 60,
    },
    task_publish_retry=False,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "expire-reservations": {"task": "orderflow.expire", "schedule": 5.0},
        "relay-outbox": {"task": "orderflow.relay", "schedule": 5.0},
    },
)
