# HTTP load measurements

[Русский](benchmarks.md) · **English**

Date: **2026-10-30**. Local educational testbed measurements, not a production SLA.

## Environment and method

Python 3.14.4, PostgreSQL 18.6 (Ubuntu 18.6-0ubuntu0.26.04.1), Redis 8.10.3.
WSL2/Linux, AMD Ryzen 5 7640HS, 12 logical CPUs, approximately 7.36 GiB WSL RAM.
One Uvicorn process, synchronous routes, SQLAlchemy pool 5 + max_overflow 5;
two Celery worker processes and one Beat. No container limits. Loopback HTTP,
without TLS; load generation and all services share one machine.

Each concurrency level has three runs of 500 distinct-key checkouts with sufficient
stock, followed by 500 catalog reads. Reads have ten warm-up requests per run;
checkout uses an already running API without a separate warm-up batch. No pytest
suite ran alongside final measurements. A dependency installation finished during
the beginning of c20; demo products/background jobs remained part of the system.

| Concurrency | Run | Operation | Responses | RPS | p50, ms | p95, ms | p99, ms |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 20 | 1 | Checkout | 201: 500 | 53.39 | 390.16 | 566.75 | 680.39 |
| 20 | 1 | Catalog | 200: 500 | 284.35 | 68.09 | 99.89 | 118.64 |
| 20 | 2 | Checkout | 201: 500 | 47.95 | 413.6 | 510.31 | 587.28 |
| 20 | 2 | Catalog | 200: 500 | 173.82 | 110.44 | 168.48 | 195.22 |
| 20 | 3 | Checkout | 201: 500 | 57.43 | 369.64 | 473.22 | 592.23 |
| 20 | 3 | Catalog | 200: 500 | 190.78 | 99.25 | 163.24 | 211.78 |
| 50 | 1 | Checkout | 201: 500 | 55.34 | 939.43 | 1272.65 | 1537.06 |
| 50 | 1 | Catalog | 200: 500 | 255.16 | 153.74 | 432.39 | 628.8 |
| 50 | 2 | Checkout | 201: 500 | 50.27 | 967.41 | 1596.11 | 3370.57 |
| 50 | 2 | Catalog | 200: 500 | 181.04 | 208.53 | 649.99 | 911.54 |
| 50 | 3 | Checkout | 201: 500 | 45.67 | 1031.25 | 1729.91 | 2425.19 |
| 50 | 3 | Catalog | 200: 500 | 133.8 | 291.44 | 825.71 | 1120.51 |

All sets finished without transport errors, unexpected 4xx/5xx, or reservation
invariant violations. `on_hand >= reserved >= 0` held after both sets. Totals:
3,000 writes and 3,000 reads.

API peak RSS sampled every 50 ms: c20 **112472 KiB**, c50 **130264 KiB**.
This is API memory, not whole-stack memory. Higher concurrency increases pool
wait and hot-SKU lock wait; these results do not describe real internet traffic.
Measured source commit: `942ba88`; full SHA, time, and source SHA-256 are in JSON.
The later reporting commit changes documentation/results only.

## Correctness and reproduction

`scripts/benchmark.py` separately checks 20 buyers for one unit: one `201`,
nineteen expected `409`. It does not measure capacity. The identical-key case
is `test_twenty_retries_one_order`.

```bash
make seed
make benchmark
.venv/bin/python scripts/load_test.py --requests 500 --concurrency 20 --runs 3
.venv/bin/python scripts/load_test.py --requests 500 --concurrency 50 --runs 3
```

API, PostgreSQL, Redis, worker, and Beat must run. Do not run both load generators
together. The development image may run the generator using
`docker compose exec api python scripts/load_test.py`; runtime images include
demo.py and benchmark.py only.

Raw local reports: `output/`. Sanitized reports: [c20](benchmarks/load-c20.json),
[c50](benchmarks/load-c50.json), [last unit](benchmarks/last-unit.json).
