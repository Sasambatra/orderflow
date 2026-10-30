import os
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from orderflow.core.config import get_settings


@lru_cache(maxsize=16)
def _factory(url: str, process_id: int) -> sessionmaker[Session]:
    # PID in the cache key prevents sharing a prefork worker's parent pool.
    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c timezone=UTC -c lock_timeout=2000 -c statement_timeout=10000",
        },
    )
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_factory() -> sessionmaker[Session]:
    return _factory(get_settings().database_url, os.getpid())


def database_now(session: Session):
    return session.scalar(text("SELECT clock_timestamp()"))
