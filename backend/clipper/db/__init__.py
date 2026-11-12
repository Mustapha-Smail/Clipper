"""Database engine, sessions and migrations."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .. import config

_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        url = config.database_url()
        if url.startswith("sqlite"):
            config.data_dir().mkdir(parents=True, exist_ok=True)
            _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

            @event.listens_for(_engine, "connect")
            def _pragmas(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")   # the API and the worker share the file
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        else:
            _engine = create_engine(url, pool_pre_ping=True)
        _factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Forget the engine so the next call re-reads the configuration (tests)."""
    global _engine, _factory
    if _engine is not None:
        _engine.dispose()
    _engine = _factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """A short transaction: commit on success, roll back on error."""
    get_engine()
    session = _factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with session_scope() as session:
        yield session


def _alembic_config():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(config.PACKAGE / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", config.database_url().replace("%", "%%"))
    return cfg


def upgrade() -> None:
    """Bring the schema to the latest revision. Safe to run on every start."""
    from alembic import command

    if config.database_url().startswith("sqlite"):
        config.data_dir().mkdir(parents=True, exist_ok=True)
    command.upgrade(_alembic_config(), "head")


def make_revision(message: str) -> None:
    """Developer helper: write a new migration from the model changes."""
    from alembic import command

    command.revision(_alembic_config(), message=message, autogenerate=True)
