from datetime import timedelta

import pytest
from sqlalchemy import func, select

from orderflow.db.session import database_now, session_factory
from orderflow.models.entities import Notification, OutboxEvent
from orderflow.services import outbox
from tests.integration.test_payments import prepared


def event_id():
    with session_factory()() as session:
        return session.scalar(select(OutboxEvent.id))


def test_broker_failure_does_not_lose_event(actors, product_factory):
    prepared(actors["customer"], product_factory())

    def unavailable(identifier):
        raise ConnectionError("Redis stopped")

    assert outbox.relay_events(publisher=unavailable) == {"published": 0, "failed": 1}
    identifier = event_id()
    with session_factory().begin() as session:
        event = session.get(OutboxEvent, identifier)
        assert event.dead_at is None and event.processing_attempts == 0
        event.next_dispatch_at = database_now(session)
    assert outbox.relay_events(publisher=outbox.process_event)["published"] == 1
    assert outbox.backlog() == {"pending": 0, "dead": 0}


def test_published_but_lost_job_is_republished(actors, product_factory):
    prepared(actors["customer"], product_factory())
    outbox.relay_events(publisher=lambda identifier: None)
    identifier = event_id()
    with session_factory().begin() as session:
        event = session.get(OutboxEvent, identifier)
        assert event.published_at is not None and event.processed_at is None
        event.next_dispatch_at = database_now(session)
    outbox.relay_events(publisher=outbox.process_event)
    assert not outbox.process_event(identifier)
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_lease_owner_fences_stale_dispatcher(actors, product_factory):
    prepared(actors["customer"], product_factory())
    identifier, token = outbox.claim_events()[0]
    assert outbox.claim_events() == []
    with session_factory().begin() as session:
        session.get(OutboxEvent, identifier).lease_until = database_now(session) - timedelta(
            seconds=1
        )
    new_id, new_token = outbox.claim_events()[0]
    assert new_id == identifier and new_token != token
    assert not outbox.finish_dispatch(identifier, token, success=True)
    assert outbox.finish_dispatch(identifier, new_token, success=True)


def test_poison_event_dead_letter_and_retry(actors, product_factory):
    prepared(actors["customer"], product_factory())
    identifier = event_id()
    with session_factory().begin() as session:
        event = session.get(OutboxEvent, identifier)
        original = event.payload
        event.payload = {"schema_version": 999}
    for _ in range(5):
        with pytest.raises(ValueError):
            outbox.process_event(identifier)
    assert outbox.backlog() == {"pending": 0, "dead": 1}
    assert outbox.list_dead(10, None)["items"][0]["processing_attempts"] == 5
    assert not outbox.process_event(identifier)
    with session_factory().begin() as session:
        session.get(OutboxEvent, identifier).payload = original
    outbox.retry_dead(identifier)
    assert outbox.process_event(identifier)
