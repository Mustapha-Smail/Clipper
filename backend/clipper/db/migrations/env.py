"""Alembic environment. Run through `clipper db upgrade`, not the alembic CLI."""

from alembic import context
from sqlalchemy import create_engine

from clipper.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",  # SQLite cannot ALTER in place
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
