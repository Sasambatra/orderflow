import uuid

from orderflow.services import orders, outbox
from orderflow.workers.celery_app import app


@app.task(name="orderflow.process_event")
def process_event(identifier: str):
    return outbox.process_event(uuid.UUID(identifier))


@app.task(name="orderflow.expire")
def expire():
    return orders.expire_reservations()


@app.task(name="orderflow.relay")
def relay():
    return outbox.relay_events()
