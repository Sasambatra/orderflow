# Verification record

[Русский](verification.ru.md) · **English**

Date: **2026-10-30**. Environment: Linux/WSL2, PostgreSQL 18.6, Redis 8.10.3.

| Check | Observed result |
| --- | --- |
| Full pytest on Python 3.14.4 | **65 passed**, including 2 e2e |
| Full pytest on Python 3.13.18 | **65 passed**, including 2 e2e |
| Combined statement/branch coverage | **93.04%** |
| `make check` | Ruff, formatting, 63 non-e2e tests, 85% coverage threshold, pip check, Alembic check passed |
| Last-unit checkout | 20 distinct keys: one order and 19 stock conflicts |
| Same-request retries | 20 identical keys: one order and 19 response replays |
| Concurrent operations | Payment/cancellation, payment/expiry, reversed item order, repeated consumers |
| Deadline after lock wait | A webhook waiting past expiry becomes refund_required |
| Real worker | A Celery process through Redis releases stock and delivers inbox events |
| Redis outage | A private broker is actually stopped; checkout persists and recovery delivers one notification |
| Transaction failure | A controlled pre-commit crash rolls back order, reservation, and key |
| Migrations | Empty schema, downgrade to base, upgrade to head, no drift |
| HTTP demo | paid, checkout/webhook replay, notification_received=true |
| Load | Three 500-write/500-read runs each at concurrency 20 and 50; see benchmarks |
| Python package | Wheel/sdist built; non-editable wheel runs ready outside the source tree |
| Clean install | Hash-locked development requirements installed with pip in a new Python 3.13 environment; pip check passed |
| `.env` creation | Independent secrets, mode 600, existing file preserved |
| Backup/restore | Separate-database restore, correct schema revision, no reservation-invariant violations |
| Compose | `docker compose config --quiet` passed |

Full local logs and coverage XML are ignored. Sanitized load reports are under
`docs/benchmarks/`. Coverage measures the `orderflow` package, excluding tests,
scripts, and migrations. Worker-subprocess coverage is not merged into its parent.

## GitHub Actions evidence

[CI run 41107674412](https://github.com/Sasambatra/orderflow/actions/runs/41107674412)
verified commit `382a7bb`: **success**. All three jobs passed: `test (3.13)`,
`test (3.14)`, and `container`.

The container job built the image, migrated an empty database, waited for
readiness, ran the HTTP demo with a real worker, checked the last-unit scenario,
and ran full pytest in the development container.
[Machine-readable summary](ci-verification.json). JUnit/coverage XML are run artifacts.

The local WSL Docker socket was unavailable by permissions; container execution
was verified on GitHub-hosted runners. Socket permissions were not changed.
The [workflow](https://github.com/Sasambatra/orderflow/actions/workflows/ci.yml)
shows current main status; the linked run records a specific tested commit.

## Limits

A completed educational backend with mock payments, local notifications, and
one warehouse. TLS, real payments/bank refunds, account recovery/verification,
and target-infrastructure operating guarantees are not implemented.
