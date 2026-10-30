# OrderFlow

A portfolio backend for transactional inventory reservations, idempotent checkout and durable payment-event processing.

**Python 3.13/3.14 · FastAPI · PostgreSQL 18 · SQLAlchemy · Alembic · Redis · Celery**

An order reserves all items atomically. Repeated requests replay the stored response, signed payment events are deduplicated, and late or additional successful payments require a refund instead of consuming inventory again. A PostgreSQL outbox keeps events recoverable through broker outages; the consumer writes an idempotent local inbox notification.

## Run

Requires Docker Compose and Python to generate development secrets:

```bash
python3 scripts/setup_env.py
docker compose up -d --build --wait api worker beat
docker compose exec api orderflow seed
docker compose exec api python scripts/demo.py
```

Open **http://localhost:8001/docs**. Register, log in and use the bearer token in Swagger's Authorize dialog. Mutations require a UUIDv4 `Idempotency-Key`; reuse it when retrying the same operation.

```bash
docker compose --profile test run --build --rm tests
docker compose exec api python scripts/benchmark.py
docker compose logs -f api worker beat
docker compose down
```

Normal shutdown preserves volumes. Database/Redis ports bind to loopback only. Generated demo credentials live in the API's private `.local` volume. E2e tests clear Redis DB 15; do not use it for application data.

## Development

Use Python 3.13 or 3.14 with PostgreSQL and Redis. Run `make setup`, configure `.env`, then `make migrate`, `make seed`, `make run`, `make worker` and `make beat` in separate terminals. On Linux/WSL, `scripts/local_stack.py` manages those three processes; it does not provision databases or Redis.

`make check` runs lint, coverage, dependency and migration checks. `make test-e2e` launches real workers. The broker-stop test also needs `redis-server` or `REDIS_SERVER`. `make build-python` produces wheel/sdist; `make build` adds a Docker build. Runtime and development dependencies are hash-locked.

## Design and limits

- PostgreSQL row locks protect stock: `on_hand >= reserved >= 0`.
- Prices are integer RUB minor units, snapshotted into order lines.
- Auth uses Argon2, short-lived JWTs, revocable sessions, owner checks and an admin role.
- HMAC webhooks validate raw bytes, timestamp, amount and persisted event identity.
- History, reservations, idempotency responses and outbox events commit with the business operation.
- Expiration uses a fresh database clock after locking the order.
- Outbox transport is at least once; inbox insertion and event completion share one transaction.
- Payment is a mock: no real money, bank refunds, email/SMS, frontend or multi-warehouse support.

The repository includes a CI matrix and container smoke job. The [verification report](docs/verification.ru.md) lists checks actually performed. [Benchmarks](docs/benchmarks.md) include measured conditions and limitations. Production additionally needs TLS, account recovery, monitoring and a real payment integration. Process-local metrics assume one API process.

Full documentation, architecture, runbook and portfolio notes are available in [Russian](README.md). License: [MIT](LICENSE).
