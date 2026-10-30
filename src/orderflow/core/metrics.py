from prometheus_client import REGISTRY, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily

HTTP_REQUESTS = Counter(
    "orderflow_http_requests_total", "API requests", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "orderflow_http_duration_seconds", "API request time", ["method", "route"]
)

IDEMPOTENCY_REPLAYS = Counter("orderflow_idempotency_replays_total", "Replayed mutation responses")
WEBHOOKS = Counter("orderflow_webhooks_total", "Verified webhook processing results", ["outcome"])
DB_ERRORS = Counter("orderflow_database_errors_total", "Database errors returned by HTTP API")
DB_RETRIES = Counter(
    "orderflow_transaction_retries_total", "Retried database lock/serialization conflicts"
)
DB_ACQUIRE = Histogram(
    "orderflow_database_acquire_seconds", "Connection acquisition time in write transactions"
)


class BusinessCollector:
    def collect(self):
        from sqlalchemy import func, select

        from orderflow.db.session import database_now, session_factory
        from orderflow.models.entities import IdempotencyRecord, Order, OrderEvent, OutboxEvent

        with session_factory()() as session:
            now = database_now(session)
            transitions = dict(
                session.execute(
                    select(OrderEvent.to_status, func.count()).group_by(OrderEvent.to_status)
                ).all()
            )
            pending = session.execute(
                select(func.count(), func.min(OutboxEvent.next_dispatch_at)).where(
                    OutboxEvent.processed_at.is_(None), OutboxEvent.dead_at.is_(None)
                )
            ).one()
            dead = session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.dead_at.is_not(None))
            )
            oldest = session.scalar(
                select(func.min(Order.expires_at)).where(
                    Order.status == "awaiting_payment", Order.expires_at <= now
                )
            )
            conflicts = session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(
                    IdempotencyRecord.response_body["error"]["code"].astext == "insufficient_stock"
                )
            )
        metric = GaugeMetricFamily(
            "orderflow_order_transitions", "Persisted order transitions", labels=["status"]
        )
        for status in ["awaiting_payment", "paid", "cancelled", "expired"]:
            metric.add_metric([status], transitions.get(status, 0))
        yield metric
        for name, help_text, value in [
            ("outbox_pending", "Unprocessed non-dead events", pending[0]),
            ("outbox_dead", "Events requiring operator repair", dead),
            (
                "outbox_due_age_seconds",
                "Age of oldest due dispatch",
                max(0, (now - pending[1]).total_seconds()) if pending[1] else 0,
            ),
            (
                "expired_waiting_age_seconds",
                "Age of oldest overdue reservation",
                max(0, (now - oldest).total_seconds()) if oldest else 0,
            ),
            ("stock_conflicts", "Persisted insufficient-stock responses", conflicts),
        ]:
            yield GaugeMetricFamily("orderflow_" + name, help_text, value=value)

    def describe(self):
        # Avoid opening database connections when the module is imported.
        return []


REGISTRY.register(BusinessCollector())
