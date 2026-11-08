# Portfolio case study

[Русский](portfolio.ru.md) · **English**

**OrderFlow — an order and stock-reservation backend.** It prevents overselling,
handles repeated HTTP/payment events, and recovers background notifications
using a transactional outbox. Stack: Python, FastAPI, PostgreSQL, SQLAlchemy,
Alembic, Redis, Celery, Docker, pytest.

## Resume entry

> Built atomic PostgreSQL stock reservations, idempotent operations, and signed
> mock-payment webhooks. Added a transactional outbox, Celery delivery, revocable
> JWT sessions, role checks, migrations, and concurrent-scenario tests.

The recorded baseline is 65 tests on Python 3.13/3.14 and 93.04% coverage. Twenty
buyers competing for one unit create one order; twenty retries sharing a key
return the same order. [Verification conditions](verification.en.md).

Source: [Sasambatra/orderflow](https://github.com/Sasambatra/orderflow).
This is an educational project, not commercial experience or banking processing.

## Five-to-seven-minute walkthrough

1. Show Swagger and [the architecture](architecture.en.md); explain order states and stock invariants.
2. Run `make demo`: checkout, replay, mock payment, webhook, paid, inbox.
3. Run `make benchmark`: 20 buyers, one unit, one `201`, 19 expected conflicts.
4. Show `test_twenty_retries_one_order` and the real Redis-outage e2e test.
5. Explain late payment and `refund_required`, then show verification and CI.

Keep `.env`, passwords, Authorization headers, and real accounts out of recordings.

## Engineering discussion

- A stock read before INSERT is insufficient: competing requests can read the same
  value. Locks and changes share a transaction; CHECK constraints protect valid counters.
- Persisting the full response handles lost post-commit replies and detects changed
  payloads under the same key.
- Redis publication and PostgreSQL commit are not one transaction. Outbox records
  remain until processing completes.
- Unique keys protect HTTP/webhook effects; inbox insertion and `processed_at`
  commit together. External bank/email providers require additional protocols.
- Late success is retained for review without spending already released stock.

Future work: real payment/refund integration, account recovery/verification,
retention rules, multi-instance metrics, target-host load testing, and operating SLOs.
