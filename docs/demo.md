# A five-minute review

This backend is reviewed through HTTP, database invariants and failure scenarios. The payment provider is a mock.

## 1. Run the complete checkout

Follow the [quick start](../README.md), then run:

```bash
docker compose exec -T api python scripts/demo.py
```

The script logs in with generated demo credentials, creates an order, repeats checkout with the same key, creates a payment attempt, sends a signed success event, repeats that event, then waits for the worker-delivered inbox notification.

Example result shape (the order ID changes each run):

```json
{
  "order_id": "<generated UUID>",
  "status": "paid",
  "order_replay": true,
  "webhook_replay": true,
  "notification_received": true
}
```

The same command is exercised by the container CI job. A successful `paid` response alone does not complete the demo: it must observe the notification through the API.

## 2. Race for the last unit

```bash
docker compose exec -T api python scripts/benchmark.py
```

This creates a dedicated SKU with exactly one unit, releases 20 HTTP requests with distinct keys, asserts one `201` and nineteen `409`, saves a local report, then cancels the winning reservation. Expected stock conflicts are separate from server errors.

For the other retry scenario, inspect `test_twenty_retries_one_order` in [the concurrency suite](../tests/concurrency/test_races.py). Twenty identical keys produce one order and nineteen response replays.

## 3. Explain recovery

Read the [outage test](../tests/e2e/test_redis_recovery.py): it starts a private Redis process and API, stops Redis, creates a real HTTP order with an existing token, then restarts the broker and worker and verifies one notification.

The [outbox tests](../tests/integration/test_outbox.py) also cover a lost queued message, stale lease ownership, duplicate processing and dead-event repair. The [payment tests](../tests/integration/test_payments.py) show late success and an additional successful payment becoming `refund_required`.

## 4. Inspect the tradeoffs

Review [architecture](architecture.ru.md), [the runbook](runbook.ru.md) and [measured benchmarks](benchmarks.md). Explain where the implementation guarantees a single business effect and where an external email/bank integration would require its own protocol.

Do not show `.env`, demo passwords or bearer tokens when recording a walkthrough. `/docs` is a local development address, not a hosted public demo.
