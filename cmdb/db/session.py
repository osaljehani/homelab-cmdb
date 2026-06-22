from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cmdb.config import settings

# The same SQLite file may be opened concurrently by more than one process
# (e.g. the web container and the on-demand MCP server). `timeout` makes a
# writer wait for a held lock instead of failing immediately with
# "database is locked".
engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    with get_session() as session:
        yield session
