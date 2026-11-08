# Setup and operations

[Русский](runbook.ru.md) · **English**

## Configuration

`scripts/setup_env.py` creates `.env` only if absent. Example placeholders cannot
run the application: two independent secrets of at least 32 characters are required.

| Setting | Meaning |
| --- | --- |
| APP_ENV | development, test, production; seed is development-only |
| DATABASE_URL | PostgreSQL using `postgresql+psycopg://` |
| TEST_DATABASE_URL | Separate `_test` database, cleared by pytest |
| POSTGRES_PASSWORD | Randomly generated Compose password |
| REDIS_URL | Broker/rate limiter; application DB 0 |
| TEST_REDIS_URL | Dedicated DB 15; cleared by e2e |
| JWT_SECRET / WEBHOOK_SECRET | Independent random secrets |
| ACCESS_TOKEN_TTL_SECONDS | Default 900 |
| RESERVATION_TTL_SECONDS | Default 900; shorten for expiry demonstrations |
| RATE_LIMIT_ENABLED | Login/registration limiting, default true |

API port: 8001. Docker host ports: PostgreSQL 5433, Redis 6380, test PostgreSQL
5434. Compose services use service names and internal ports, not host localhost.
Do not run native and Compose stacks on the same ports together.

## PostgreSQL without Docker

Use an administrator's psql session to create a dedicated role and databases:

```sql
CREATE ROLE orderflow LOGIN;
\password orderflow
CREATE DATABASE orderflow OWNER orderflow;
CREATE DATABASE orderflow_test OWNER orderflow;
```

Set matching connection values privately in `.env`. `make migrate` changes only
DATABASE_URL; test fixtures apply Alembic to their separate database. The runtime
role does not need superuser privileges. Use Redis on the port configured in `.env`.

The earlier prepared WSL setup used `.local/postgres` on 5433 and `.local/redis-data`
on 6380. Those local data/binaries are not published. A system PostgreSQL on another
port is outside this project's scope.

## Administrator and demo

```bash
.venv/bin/orderflow create-admin --email you@example.com
# Password input is hidden; automation may use ORDERFLOW_ADMIN_PASSWORD.
.venv/bin/orderflow seed
make demo
make benchmark
```

Seed creates three SKUs and random demo passwords in `.local/demo-credentials.json`
(mode 600), preserving existing stock, roles, and passwords. If the credentials
file is lost while accounts remain, restore it rather than generating unusable
replacement passwords. Docker stores it in the API's `demo-data` volume.

## Background processes and recovery

Keep one Beat instance; every five seconds it schedules expiration and relay work.
Task retries and multiple relay/consumer processes are supported.

```bash
.venv/bin/orderflow outbox-status
.venv/bin/orderflow expire
.venv/bin/orderflow relay
.venv/bin/orderflow retry-event UUID
```

Admin endpoints: `GET /api/v1/admin/outbox/dead` and
`POST /api/v1/admin/outbox/{id}/retry`. Fix the cause before retrying a poison event.
Retry returns `409` if the event is no longer dead.

During Redis failure, orders/outbox remain in PostgreSQL; login/registration
temporarily return `503`. Restore Redis, worker, and Beat. The relay republishes
events without `processed_at`. Leases last 30 seconds; published but unfinished
events become dispatchable again after 30 seconds. Inspect worker logs,
`last_error`, and `dead_at`.

A stopped worker delays stock release. Payment creation and successful webhooks
still check expiry under the order lock. `refund_required` marks successful but
unapplied payments for operator review through `/admin/orders`; bank refunds are
not implemented.

## Observability

`/health/live` checks process response; `/health/ready` checks PostgreSQL and
migration revision. Redis is not required for checkout readiness. `/metrics`
requires an admin bearer token.

Metrics cover HTTP rate/latency, replay, webhooks, persisted transitions, stock
conflicts, pending/dead outbox, expired orders, overdue dispatch, database errors,
and connection-acquisition time, including pool wait and connection setup.
HTTP/replay/webhook counters reset per process; business gauges read the database.
UUIDs are not labels. Multiple API processes need per-instance scraping or a
Prometheus multiprocess setup. Logs use request IDs and route templates.

## Backup and isolated restore

Run from the Compose project root. Dumps contain application data and must stay private.

```bash
mkdir -p .local/backups
umask 077
docker compose exec -T db pg_dump -U orderflow -Fc orderflow > .local/backups/orderflow.dump
docker compose exec -T db createdb -U orderflow orderflow_restore_check
docker compose exec -T db pg_restore --exit-on-error --no-owner -U orderflow -d orderflow_restore_check < .local/backups/orderflow.dump
docker compose exec -T db psql -U orderflow -d orderflow_restore_check -c 'SELECT version_num FROM alembic_version; SELECT count(*) FROM orders;'
```

Compare counts/invariants with the original, then remove only the temporary
`orderflow_restore_check` database. Never restore over the working database.
Native deployments use the same PostgreSQL tools with their own connection settings.

## Test and data boundaries

Files run sequentially; race tests create their own threads. pytest-xdist needs
a separate database per worker. Fixtures require `_test` before TRUNCATE.
Load scripts create dedicated SKUs and real demo orders; their holds later expire.
Raw reports go to ignored `output/`.

```sql
SELECT i.product_id, i.on_hand, i.reserved, coalesce(r.qty, 0) AS expected_reserved
FROM inventory i
LEFT JOIN (
  SELECT product_id, sum(quantity) AS qty
  FROM reservations WHERE status = 'active' GROUP BY product_id
) r USING (product_id)
WHERE i.reserved <> coalesce(r.qty, 0) OR i.on_hand < i.reserved OR i.reserved < 0;
```

Expected result: zero rows. Investigate orders, payments, history, and application
versions before attempting any correction.
