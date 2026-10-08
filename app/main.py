"""Application factory and ASGI entry point (`uvicorn app.main:app`)."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import router
from app.config import Settings, get_settings
from app.database import Base, create_db_engine, create_session_factory
from app.processing import (
    InlineJobQueue,
    JobProcessor,
    ThreadPoolJobQueue,
    recover_unfinished_jobs,
)

logger = logging.getLogger(__name__)

DEMO_PAGE = Path(__file__).parent / "static" / "index.html"

DESCRIPTION = """
Submit a list of recipients once, get a **job id** back immediately, poll the job
for progress, then download the generated PDF certificates one by one or as a ZIP.

Typical flow: `POST /jobs` → `GET /jobs/{job_id}` → `GET /jobs/{job_id}/certificates`
→ `GET /certificates/{certificate_id}/download` (or `GET /jobs/{job_id}/download`).
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # uvicorn only configures its own loggers; make the app's visible too.
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    processor = JobProcessor(session_factory, settings)

    if settings.process_jobs_inline:
        queue = InlineJobQueue(processor)
    else:
        queue = ThreadPoolJobQueue(processor, max_workers=settings.worker_threads)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _ensure_sqlite_dir(settings.database_url)
        Path(settings.certificates_dir).mkdir(parents=True, exist_ok=True)
        # Small project, no migrations: create the schema if it isn't there.
        Base.metadata.create_all(engine)

        # Jobs interrupted by a previous shutdown/crash pick up where they left off.
        requeued = recover_unfinished_jobs(session_factory, queue)
        if requeued:
            logger.info("re-queued %d unfinished job(s) on startup", requeued)
        yield
        queue.shutdown()
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.processor = processor
    app.state.queue = queue

    app.include_router(router)

    @app.get("/health", tags=["meta"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # A small built-in UI for trying the API by hand (see README "Demo UI").
    @app.get("/", include_in_schema=False)
    def demo_page() -> FileResponse:
        return FileResponse(DEMO_PAGE, media_type="text/html")

    return app


def _ensure_sqlite_dir(database_url: str) -> None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and ":memory:" not in database_url:
        Path(database_url[len(prefix) :]).parent.mkdir(parents=True, exist_ok=True)


app = create_app()
