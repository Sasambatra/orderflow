import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from orderflow.db.base import Base


class Identity:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Identity, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('customer','admin')", name="role"),)
    email_normalized: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Identity, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Identity, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_minor > 0", name="positive_price"),
        CheckConstraint("currency = 'RUB'", name="currency"),
    )
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    price_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        CheckConstraint("on_hand >= 0", name="nonnegative_on_hand"),
        CheckConstraint("reserved >= 0", name="nonnegative_reserved"),
        CheckConstraint("reserved <= on_hand", name="reserved_within_stock"),
    )
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.clock_timestamp()
    )


class StockAdjustment(Identity, Base):
    __tablename__ = "stock_adjustments"
    __table_args__ = (CheckConstraint("delta != 0", name="nonzero_delta"),)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Identity, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_payment','paid','cancelled','expired')", name="status"
        ),
        CheckConstraint("total_minor > 0", name="positive_total"),
        CheckConstraint("currency = 'RUB'", name="currency"),
        CheckConstraint("(status = 'paid') = (paid_payment_id IS NOT NULL)", name="paid_payment"),
        Index("ix_orders_owner_cursor", "user_id", "created_at", "id"),
        Index(
            "ix_orders_expiration",
            "expires_at",
            "id",
            postgresql_where=text("status = 'awaiting_payment'"),
        ),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(24), default="awaiting_payment")
    total_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payments.id", use_alter=True, name="fk_orders_paid_payment_id_payments")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.clock_timestamp()
    )


class OrderItem(Identity, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        CheckConstraint("quantity BETWEEN 1 AND 100", name="quantity"),
        CheckConstraint(
            "unit_price_minor > 0 AND line_total_minor = quantity * unit_price_minor",
            name="line_total",
        ),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    sku_snapshot: Mapped[str] = mapped_column(String(64))
    name_snapshot: Mapped[str] = mapped_column(String(200))
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)


class Reservation(Identity, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_reservations_order_product"),
        CheckConstraint("quantity BETWEEN 1 AND 100", name="quantity"),
        CheckConstraint("status IN ('active','consumed','released')", name="status"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Payment(Identity, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_id"),
        CheckConstraint("amount_minor > 0 AND currency = 'RUB'", name="amount"),
        CheckConstraint(
            "provider_status IN ('pending','succeeded','failed')", name="provider_status"
        ),
        CheckConstraint(
            "application_status IN ('not_applied','applied','refund_required','refunded')",
            name="application_status",
        ),
        Index(
            "uq_payments_one_applied",
            "order_id",
            unique=True,
            postgresql_where=text("application_status = 'applied'"),
        ),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16), default="mock")
    provider_payment_id: Mapped[str] = mapped_column(String(80))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    provider_status: Mapped[str] = mapped_column(String(16), default="pending")
    application_status: Mapped[str] = mapped_column(String(24), default="not_applied")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentEvent(Identity, Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_payment_events_provider_event"),
    )
    provider: Mapped[str] = mapped_column(String(16), default="mock")
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(32), default="processing")
    response_status: Mapped[int] = mapped_column(Integer, default=0)
    response_body: Mapped[dict] = mapped_column(JSONB, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OrderEvent(Identity, Base):
    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("order_id", "version", name="uq_order_events_version"),)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(100))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdempotencyRecord(Identity, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "method", "path", "key", name="uq_idempotency_scope"),
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(200))
    key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer, default=0)
    response_body: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEvent(Identity, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_pending",
            "next_dispatch_at",
            postgresql_where=text("processed_at IS NULL AND dead_at IS NULL"),
        ),
    )
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_dispatch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))


class Notification(Identity, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("event_id", "channel", name="uq_notifications_event_channel"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outbox_events.id"))
    channel: Mapped[str] = mapped_column(String(16), default="inbox")
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
