import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config() -> Config:
    # Built in code so startup does not depend on alembic.ini (not shipped in the Docker image).
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def run_migrations() -> None:
    command.upgrade(alembic_config(), "head")


async def init_db() -> None:
    # Alembic is synchronous; run it off the event loop.
    await asyncio.to_thread(run_migrations)
    logger.info("Database migrations applied")
