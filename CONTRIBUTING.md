# Contributing

Use Python 3.13 or 3.14 and follow the setup in the [README](README.md). All database tests use PostgreSQL; replacing it with SQLite would change the locking semantics under test.

1. Create a branch for a focused change.
2. Describe the behaviour being fixed and include a failing regression scenario when appropriate.
3. Run `make check`. For worker/outbox changes also run `make test-e2e` with dedicated Redis DB 15 and a local `redis-server`.
4. For schema changes, create and review an Alembic migration; verify upgrade and rollback on an isolated database.
5. Open a pull request describing the problem, resulting behaviour and validation. Include limits or measurements when relevant.

Keep stock changes and their history/outbox effects in one transaction. Preserve stable lock ordering, owner checks, integer money and the distinction between provider success and payment application. Do not weaken tests to make a concurrency failure disappear.

Never commit `.env`, database dumps, generated credentials, tokens or real customer data. Redact request headers and passwords from issue reports. Expected business conflicts are not server failures.

Report reproducible bugs through [Issues](https://github.com/Sasambatra/orderflow/issues). Use [private security reporting](SECURITY.md) for vulnerabilities.
