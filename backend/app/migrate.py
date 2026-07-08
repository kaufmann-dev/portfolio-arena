"""Idempotent migration runner: upgrade to head (fresh databases get the
full baseline schema)."""
import logging

from alembic.config import Config

from alembic import command

from .config import BACKEND_DIR
from .db import wait_for_db
from .log import setup_logging

logger = logging.getLogger(__name__)


def run_migrations() -> None:
    wait_for_db()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(cfg, "head")
    logger.info("database schema is up to date")


if __name__ == "__main__":
    setup_logging()
    run_migrations()
