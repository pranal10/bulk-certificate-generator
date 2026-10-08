"""Database engine / session helpers."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store timezone-aware datetimes as naive UTC and give them back as aware UTC.

    SQLite has no timezone support, so without this the API would return
    naive timestamps and clients would have to guess the zone.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_db_engine(database_url: str) -> Engine:
    kwargs: dict = {}
    is_sqlite = database_url.startswith("sqlite")

    if is_sqlite:
        # The API thread and the worker threads share the engine, so SQLite's
        # same-thread check has to be disabled (each session still gets its
        # own connection from the pool).
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url or database_url == "sqlite://":
            # An in-memory DB only exists on one connection; make every
            # session reuse that connection.
            kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL lets the API read job status while a worker is writing.
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait instead of failing with "database is locked" when the
            # API and a worker both want to write.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
