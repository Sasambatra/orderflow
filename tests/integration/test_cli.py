import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from orderflow import cli
from orderflow.core.config import get_settings
from orderflow.db.session import session_factory
from orderflow.models.entities import Inventory, Product, User


def test_create_admin_does_not_promote_existing_account(actors):
    with pytest.raises(ValueError):
        cli.create_admin("customer@example.com", "valid-password-123")
    with pytest.raises(ValueError):
        cli.create_admin("short@example.com", "short")
    result = cli.create_admin("new-admin@example.com", "valid-password-123")
    assert result["role"] == "admin"


def test_seed_is_development_only_and_repeatable(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        cli.seed_demo()
    monkeypatch.setattr(get_settings(), "app_env", "development")
    monkeypatch.chdir(tmp_path)
    cli.seed_demo()
    original = Path(".local/demo-credentials.json").read_text()
    assert Path(".local/demo-credentials.json").stat().st_mode & 0o777 == 0o600
    cli.seed_demo()
    assert Path(".local/demo-credentials.json").read_text() == original
    with session_factory()() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 2
        assert session.scalar(select(func.count()).select_from(Product)) == 3
        assert session.scalar(select(func.sum(Inventory.on_hand))) == 120


def test_cli_commands(capsys):
    cli.main(["outbox-status"])
    assert json.loads(capsys.readouterr().out) == {"pending": 0, "dead": 0}
    cli.main(["expire"])
    assert json.loads(capsys.readouterr().out) == {"expired": 0}
