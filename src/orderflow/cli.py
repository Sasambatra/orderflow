import argparse
import getpass
import json
import os
import secrets
import uuid
from pathlib import Path

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from orderflow.core.config import get_settings
from orderflow.core.security import password_hash
from orderflow.db.session import session_factory
from orderflow.domain.errors import DomainError
from orderflow.models.entities import Inventory, Product, User
from orderflow.services import orders, outbox


def create_admin(email: str, password: str) -> dict:
    email = str(TypeAdapter(EmailStr).validate_python(email)).casefold()
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")
    encoded = password_hash(password)
    with session_factory().begin() as session:
        if session.scalar(select(User.id).where(User.email_normalized == email)):
            raise ValueError("Account already exists; refusing to change its role or password")
        user = User(email_normalized=email, password_hash=encoded, role="admin")
        session.add(user)
        session.flush()
        return {"id": str(user.id), "email": email, "role": "admin"}


def seed_demo() -> dict:
    if get_settings().app_env != "development":
        raise ValueError("Demo seed is allowed only with APP_ENV=development")
    path = Path(".local/demo-credentials.json")
    if path.exists():
        credentials = json.loads(path.read_text())
    else:
        with session_factory()() as session:
            if session.scalar(
                select(User.id)
                .where(
                    User.email_normalized.in_(
                        ["demo-admin@example.com", "demo-customer@example.com"]
                    )
                )
                .limit(1)
            ):
                raise ValueError(
                    "Demo accounts already exist; restore .local/demo-credentials.json from its original volume"
                )
        credentials = {
            "admin": {"email": "demo-admin@example.com", "password": secrets.token_urlsafe(24)},
            "customer": {
                "email": "demo-customer@example.com",
                "password": secrets.token_urlsafe(24),
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            json.dump(credentials, stream, indent=2)
    with session_factory().begin() as session:
        for role, account in credentials.items():
            user = session.scalar(select(User).where(User.email_normalized == account["email"]))
            if user is None:
                session.add(
                    User(
                        email_normalized=account["email"],
                        password_hash=password_hash(account["password"]),
                        role=role,
                    )
                )
        products = [
            ("OF-KEYBOARD", "Mechanical keyboard", 799000, 50),
            ("OF-MOUSE", "Wireless mouse", 349000, 50),
            ("OF-MONITOR", "27 inch monitor", 2499000, 20),
        ]
        for sku, name, price, stock in products:
            if session.scalar(select(Product.id).where(Product.sku == sku)):
                continue
            product = Product(sku=sku, name=name, price_minor=price)
            session.add(product)
            session.flush()
            session.add(Inventory(product_id=product.id, on_hand=stock))
    return {
        "status": "seeded",
        "credentials_file": str(path),
        "note": "Existing users, stock and passwords are preserved",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="OrderFlow maintenance commands")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser(
        "create-admin", help="Create an administrator using a hidden password prompt"
    )
    admin.add_argument("--email", required=True)
    sub.add_parser("seed", help="Seed development demo data")
    sub.add_parser("expire", help="Release expired reservations")
    sub.add_parser("relay", help="Dispatch a batch of persisted events")
    sub.add_parser("outbox-status", help="Show durable backlog")
    retry = sub.add_parser("retry-event", help="Retry a repaired dead event")
    retry.add_argument("event_id", type=uuid.UUID)
    args = parser.parse_args(argv)
    try:
        match args.command:
            case "create-admin":
                result = create_admin(
                    args.email,
                    os.environ.get("ORDERFLOW_ADMIN_PASSWORD")
                    or getpass.getpass("Admin password: "),
                )
            case "seed":
                result = seed_demo()
            case "expire":
                result = {"expired": orders.expire_reservations()}
            case "relay":
                result = outbox.relay_events()
            case "outbox-status":
                result = outbox.backlog()
            case "retry-event":
                result = outbox.retry_dead(args.event_id)
        print(json.dumps(result, indent=2))
    except (ValueError, DomainError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
