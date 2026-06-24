from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from skillshub.shared.config import get_database_url

_engine = None
_session_factory: sessionmaker[Session] | None = None


def _get_engine():  # type: ignore[no-untyped-def]
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=_get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


def get_session() -> Generator[Session, None, None]:
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
