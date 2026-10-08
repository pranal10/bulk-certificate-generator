"""FastAPI dependencies. Everything hangs off `app.state`, which `create_app`
populates, so tests can build an app with their own settings/DB/queue."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.processing import JobQueue


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue
