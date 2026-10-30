# Changelog

## 0.1.0 — 2026-10-30

Initial portfolio release.

- Transactional multi-item stock reservations with PostgreSQL row locks and constraints.
- Snapshot prices, cancellation, expiration and versioned order history.
- UUIDv4 request idempotency with persisted status/body and payload conflict detection.
- HMAC-authenticated mock payment webhooks, event deduplication and late/additional payment handling.
- Transactional outbox with leases, replay until consumer completion and a deduplicated inbox.
- JWT sessions with logout revocation, Argon2 passwords, owner checks and administration.
- Unit, integration, concurrency and real-process Redis/Celery tests.
- Docker Compose, CI matrix, Python distributions, reproducible demos and measured load reports.

Scope: one warehouse, RUB, mock payments, local inbox. No actual money transfer, bank refunds, email/SMS or frontend.
