import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from orderflow.core.config import Settings, get_settings

# Refuse to truncate a development/production database, even on a typo.
test_url = os.environ.get("TEST_DATABASE_URL") or Settings().test_database_url
if not test_url or not make_url(test_url).database.endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must point to a dedicated database ending in _test")
os.environ["DATABASE_URL"] = test_url
os.environ["APP_ENV"] = "test"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/15")
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from orderflow.core.security import Actor, password_hash  # noqa: E402
from orderflow.db.base import Base  # noqa: E402
from orderflow.db.session import session_factory  # noqa: E402
from orderflow.main import create_app  # noqa: E402
from orderflow.models.entities import Inventory, Product, User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    command.check(config)
    yield


@pytest.fixture(autouse=True)
def clean_database(migrated_database):
    names = ", ".join('"' + table.name + '"' for table in Base.metadata.tables.values())
    with session_factory().begin() as session:
        session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def encoded_password():
    return password_hash("portfolio-password-123")


@pytest.fixture
def actors(encoded_password):
    with session_factory().begin() as session:
        customer = User(email_normalized="customer@example.com", password_hash=encoded_password)
        other = User(email_normalized="other@example.com", password_hash=encoded_password)
        admin = User(
            email_normalized="admin@example.com", password_hash=encoded_password, role="admin"
        )
        session.add_all([customer, other, admin])
        session.flush()
        return {
            "customer": Actor(customer.id, "customer", uuid.uuid4()),
            "other": Actor(other.id, "customer", uuid.uuid4()),
            "admin": Actor(admin.id, "admin", uuid.uuid4()),
        }


@pytest.fixture
def product_factory():
    def create(stock=10, price=129900, sku=None):
        with session_factory().begin() as session:
            product = Product(
                sku=sku or uuid.uuid4().hex, name="Mechanical keyboard", price_minor=price
            )
            session.add(product)
            session.flush()
            session.add(Inventory(product_id=product.id, on_hand=stock))
            return product.id

    return create


@pytest.fixture
def client():
    with TestClient(create_app()) as instance:
        yield instance


@pytest.fixture
def headers(client, actors):
    result = {}
    for name in actors:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": f"{name}@example.com", "password": "portfolio-password-123"},
        )
        assert response.status_code == 200, response.text
        result[name] = {"Authorization": "Bearer " + response.json()["access_token"]}
    return result
