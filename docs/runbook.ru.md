# Запуск и эксплуатация

**Русский** · [English](runbook.en.md)

## Настройки

`scripts/setup_env.py` создаёт `.env`, если файла ещё нет. Повторный запуск его не изменяет.
Заполнители в `.env.example` непригодны для работы: приложение требует два разных секрета длиной от 32 символов.

| Настройка | Значение |
| --- | --- |
| APP_ENV | development, test, production; seed доступен только в development |
| DATABASE_URL | PostgreSQL с драйвером `postgresql+psycopg://` |
| TEST_DATABASE_URL | Отдельная БД с суффиксом `_test`, очищаемая pytest |
| POSTGRES_PASSWORD | Пароль сервисов Compose; генерируется случайно |
| REDIS_URL | Broker и rate limiter; приложение использует DB 0 |
| TEST_REDIS_URL | Выделенная DB 15 для e2e; очищается тестом |
| JWT_SECRET / WEBHOOK_SECRET | Независимые случайные секреты |
| ACCESS_TOKEN_TTL_SECONDS | По умолчанию 900 |
| RESERVATION_TTL_SECONDS | По умолчанию 900; можно уменьшить для демо истечения |
| RATE_LIMIT_ENABLED | Ограничение login/register, по умолчанию true |

Порт API — 8001. Порты хоста Docker: PostgreSQL 5433, Redis 6380, тестовая PostgreSQL 5434.
Внутри Compose используются имена сервисов и стандартные порты, а не localhost хоста.
Не запускайте native-стек и Compose на одинаковых портах одновременно.

## Подготовка PostgreSQL без Docker

На своём PostgreSQL создайте пользователя и отдельные базы. В psql администратора:

```sql
CREATE ROLE orderflow LOGIN;
\password orderflow
CREATE DATABASE orderflow OWNER orderflow;
CREATE DATABASE orderflow_test OWNER orderflow;
```

Укажите соответствующие порт/пароль в `.env`; `make migrate` применяет схему только к DATABASE_URL.
Тестовая схема создаётся fixture через Alembic. Не давайте рабочему приложению superuser.
Используйте собственный Redis; для разработки достаточно `redis-server` на порту из `.env`.

В текущем подготовленном WSL-окружении проект использует отдельный кластер `.local/postgres`
на 5433 и Redis в `.local/redis-data` на 6380. Эти локальные данные и двоичные файлы не входят в Git.
Системный PostgreSQL на другом порту проект не меняет.

## Администратор и демо

```bash
.venv/bin/orderflow create-admin --email you@example.com
# Пароль вводится скрыто. Для автоматизации можно задать ORDERFLOW_ADMIN_PASSWORD.
.venv/bin/orderflow seed
make demo
make benchmark
```

Seed создаёт три SKU и случайные демо-пароли в `.local/demo-credentials.json` (права 600).
При повторе существующие остатки, роли и пароли не меняются. Если файл утрачен, а demo-аккаунты
есть в БД, seed отказывается выдавать новые недействительные пароли: восстановите исходный файл.
В Docker он сохраняется в `demo-data` volume API.

## Фоновые процессы

Worker обрабатывает события, Beat каждые 5 секунд ставит expire и relay в очередь.
Держите один экземпляр Beat. Повторы задач и несколько relay/consumer допустимы.

```bash
.venv/bin/orderflow outbox-status
.venv/bin/orderflow expire
.venv/bin/orderflow relay
.venv/bin/orderflow retry-event UUID
```

Admin API: `GET /api/v1/admin/outbox/dead` и `POST /api/v1/admin/outbox/{id}/retry`.
Перед retry исправьте причину в коде/данных; не запускайте бесконечный цикл повторов poison event.
Текущий retry возвращает 409, если событие уже не dead.

Если Redis недоступен, заказы и outbox остаются в PostgreSQL. Login/register временно отвечают 503.
Восстановите Redis, worker и Beat. Relay повторит события, у которых нет processed_at.
Lease живёт 30 секунд; опубликованное, но не завершённое событие снова доступно для dispatch через 30 секунд.
При проблемах с сетью обратите внимание на последние логи worker и поля last_error/dead_at.

При простое worker резервы освобождаются с задержкой; создание платежа и успешный webhook
всё равно проверяют срок под блокировкой заказа. `refund_required` означает необходимость
разобрать успешную, но неприменённую оплату. Посмотреть её можно через `/admin/orders`.
Встроенного банковского возврата нет.

## Наблюдение

`/health/live` проверяет ответ процесса. `/health/ready` проверяет PostgreSQL и ревизию схемы.
Redis не блокирует readiness заказа. `/metrics` требует admin bearer token.

Метрики включают HTTP rate/latency, replay запросов, исходы webhook, persisted order transitions,
конфликты остатков, pending/dead outbox, возраст просроченных ожидающих заказов,
возраст просроченного dispatch, DB errors/retries и время получения соединения для write-транзакций.
Последнее включает ожидание пула и установление соединения, если оно требуется.

Счётчики HTTP/replay/webhook процессные и сбрасываются при рестарте; бизнес-gauges читаются из БД.
UUID не используются как labels. Для нескольких API-процессов настройте Prometheus multiprocess
или сбор метрик каждого экземпляра. Логи используют request_id и шаблон маршрута.

## Резервная копия и пробное восстановление

Пример для Compose из корня проекта (файлы содержат данные, храните их приватно):

```bash
mkdir -p .local/backups
umask 077
docker compose exec -T db pg_dump -U orderflow -Fc orderflow > .local/backups/orderflow.dump
docker compose exec -T db createdb -U orderflow orderflow_restore_check
docker compose exec -T db pg_restore --exit-on-error --no-owner -U orderflow -d orderflow_restore_check < .local/backups/orderflow.dump
docker compose exec -T db psql -U orderflow -d orderflow_restore_check -c 'SELECT version_num FROM alembic_version; SELECT count(*) FROM orders;'
```

Сравните число заказов и проверку инвариантов с исходной БД, затем удалите только созданную
тестовую базу `orderflow_restore_check`. Не восстанавливайте поверх рабочей БД.
Для native-варианта используйте эти же PostgreSQL-инструменты с параметрами своего сервера.

## Работа с данными и проверками

Тесты запускаются последовательно на уровне файлов и сами создают потоки для гонок.
Не включайте pytest-xdist поверх одной БД: нужен отдельный database для каждого worker.
Перед TRUNCATE fixture проверяет `_test`; рабочая БД не очищается.
Нагрузочные скрипты создают отдельные SKU и реальные демо-заказы. Их резервы позднее истекают.
Сырые отчёты сохраняются в `output/`, исключённой из Git.

Проверка счётчиков резервов:

```sql
SELECT i.product_id, i.on_hand, i.reserved, coalesce(r.qty, 0) AS expected_reserved
FROM inventory i
LEFT JOIN (
  SELECT product_id, sum(quantity) AS qty
  FROM reservations WHERE status = 'active' GROUP BY product_id
) r USING (product_id)
WHERE i.reserved <> coalesce(r.qty, 0) OR i.on_hand < i.reserved OR i.reserved < 0;
```

Нормальный результат — 0 строк. Не исправляйте расхождения слепым UPDATE: сначала
установите причину, проверьте заказ, платежи, историю и версии приложения.
