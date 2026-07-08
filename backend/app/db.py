"""SQLAlchemy engine, session factory, and FastAPI session dependency."""
import logging
import time
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_size=10,
            pool_pre_ping=True,
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _session_factory = None


def wait_for_db() -> None:
    """Block until PostgreSQL accepts connections (Coolify may start it late)."""
    settings = get_settings()
    engine = get_engine()
    last_error: Exception | None = None
    for attempt in range(1, settings.db_connect_retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            logger.warning(
                "database connection attempt %d/%d failed: %s",
                attempt, settings.db_connect_retries, exc,
            )
            if attempt < settings.db_connect_retries:
                time.sleep(settings.db_connect_retry_delay)
    raise RuntimeError("Could not connect to PostgreSQL") from last_error


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Handlers commit explicitly; rollback happens on error."""
    session = session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
