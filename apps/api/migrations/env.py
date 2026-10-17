from alembic import context
from sqlalchemy import create_engine

from joltiq.config import get_settings
from joltiq.db import Base
import joltiq.models  # noqa: F401

target_metadata = Base.metadata


def run() -> None:
    url = context.config.attributes.get("url") or get_settings().migration_database_url
    eng = create_engine(url)
    with eng.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
