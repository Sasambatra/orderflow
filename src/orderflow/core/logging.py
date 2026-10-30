import json
import logging
from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="")


def configure_logging() -> None:
    from orderflow.core.config import get_settings

    settings = get_settings()

    class SafeFormatter(logging.Formatter):
        def format(self, record):
            value = super().format(record)
            for secret in (
                settings.jwt_secret.get_secret_value(),
                settings.webhook_secret.get_secret_value(),
                settings.database_url,
            ):
                value = value.replace(secret, "[REDACTED]")
            return value

    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=settings.log_level, handlers=[handler], force=True)


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request_id.get(),
    }


def log_event(name: str, **fields) -> None:
    logging.getLogger("orderflow").info(json.dumps({"event": name, **fields}, default=str))
