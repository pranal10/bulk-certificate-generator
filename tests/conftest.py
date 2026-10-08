from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        certificates_dir=tmp_path / "certificates",
        process_jobs_inline=True,  # deterministic: jobs finish inside the request
        max_recipients_per_job=20,
        organization_name="Test Academy",
        worker_threads=2,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # The context manager runs the lifespan (schema creation, recovery).
    with TestClient(app) as client:
        yield client


class ManualJobQueue:
    """Queue that never processes anything; tests drive the processor by hand."""

    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)

    def wait_idle(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


@pytest.fixture
def manual_app(settings: Settings):
    """App whose jobs stay pending until the test calls the processor itself."""
    app = create_app(settings)
    app.state.queue = ManualJobQueue()
    return app


@pytest.fixture
def manual_client(manual_app):
    with TestClient(manual_app) as client:
        yield client


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

RECIPIENTS = [
    {"name": "Pranal Bhatnagar", "email": "pranal@example.com"},
    {"name": "Priya Sharma", "email": "priya@example.com"},
    {"name": "Rohan Mehta", "email": "rohan@example.com"},
]


def make_payload(recipients=None, **overrides) -> dict:
    payload = {
        "event_name": "Python Bootcamp 2026",
        "issue_date": date(2026, 10, 8).isoformat(),
        "recipients": recipients if recipients is not None else [dict(r) for r in RECIPIENTS],
    }
    payload.update(overrides)
    return payload


def create_job(client: TestClient, recipients=None, **overrides) -> dict:
    response = client.post("/jobs", json=make_payload(recipients, **overrides))
    assert response.status_code == 202, response.text
    return response.json()


def pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() for page in reader.pages)
