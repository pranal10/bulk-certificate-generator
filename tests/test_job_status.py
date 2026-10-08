"""Job status / progress tracking and the processing pipeline."""

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Certificate, CertificateStatus, Job, JobStatus
from tests.conftest import RECIPIENTS, create_job, make_settings


def test_unknown_job_returns_404(client):
    response = client.get("/jobs/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_job_is_pending_until_a_worker_picks_it_up(manual_client, manual_app):
    job = create_job(manual_client)

    assert job["status"] == "pending"
    assert job["progress"] == {
        "total": 3,
        "pending": 3,
        "generated": 0,
        "failed": 0,
        "percent_complete": 0.0,
    }
    assert manual_app.state.queue.enqueued == [job["id"]]


def test_progress_advances_one_certificate_at_a_time(manual_client, manual_app):
    job = create_job(manual_client)
    processor = manual_app.state.processor
    certificates = manual_client.get(f"/jobs/{job['id']}/certificates").json()["items"]

    processor.generate_certificate(certificates[0]["id"])

    progress = manual_client.get(f"/jobs/{job['id']}").json()["progress"]
    assert progress["generated"] == 1
    assert progress["pending"] == 2
    assert progress["percent_complete"] == 33.3


def test_job_completes_after_processing(manual_client, manual_app):
    job = create_job(manual_client)

    manual_app.state.processor.process_job(job["id"])

    body = manual_client.get(f"/jobs/{job['id']}").json()
    assert body["status"] == "completed"
    assert body["progress"]["generated"] == len(RECIPIENTS)
    assert body["progress"]["pending"] == 0
    assert body["progress"]["percent_complete"] == 100.0


def test_job_status_is_processing_while_running(manual_client, manual_app, monkeypatch):
    from app import processing

    job = create_job(manual_client)
    seen = []
    original = processing.render_certificate_pdf

    def spy(data, path):
        # Observe the job status through the API while a certificate renders.
        seen.append(manual_client.get(f"/jobs/{job['id']}").json()["status"])
        return original(data, path)

    monkeypatch.setattr(processing, "render_certificate_pdf", spy)

    manual_app.state.processor.process_job(job["id"])

    assert seen == ["processing"] * len(RECIPIENTS)


def test_processing_a_job_twice_does_not_regenerate_certificates(manual_client, manual_app):
    job = create_job(manual_client)
    processor = manual_app.state.processor
    processor.process_job(job["id"])
    first = manual_client.get(f"/jobs/{job['id']}/certificates").json()["items"]

    processor.process_job(job["id"])

    second = manual_client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert [c["generated_at"] for c in first] == [c["generated_at"] for c in second]


def test_processing_an_unknown_job_is_a_no_op(manual_client, manual_app):
    manual_app.state.processor.process_job("missing")  # must not raise


def test_background_queue_processes_the_job_outside_the_request(tmp_path):
    settings = make_settings(tmp_path, process_jobs_inline=False)
    app = create_app(settings)

    with TestClient(app) as client:
        job = create_job(client)
        assert job["status"] in {"pending", "processing", "completed"}

        app.state.queue.wait_idle()

        body = client.get(f"/jobs/{job['id']}").json()
        assert body["status"] == "completed"
        assert body["progress"]["generated"] == len(RECIPIENTS)


def test_background_queue_isolates_crashes_in_the_processor(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, process_jobs_inline=False)
    app = create_app(settings)

    def crash(job_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(app.state.processor, "process_job", crash)

    with TestClient(app) as client:
        job = create_job(client)
        app.state.queue.wait_idle()  # must not raise
        assert client.get(f"/jobs/{job['id']}").json()["status"] == "pending"


def test_unfinished_jobs_are_resumed_on_startup(tmp_path):
    settings = make_settings(tmp_path)

    # 1) A job is accepted but the process "dies" before working on it.
    first_app = create_app(settings)
    first_app.state.queue = _NoopQueue()
    with TestClient(first_app) as client:
        job = create_job(client)
        certificates = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
        # Simulate the crash happening half-way: one certificate already done.
        first_app.state.processor.generate_certificate(certificates[0]["id"])
        with first_app.state.session_factory() as session:
            session.get(Job, job["id"]).status = JobStatus.PROCESSING
            session.commit()

    # 2) A fresh process starts against the same database.
    second_app = create_app(settings)
    with TestClient(second_app) as client:
        body = client.get(f"/jobs/{job['id']}").json()

    assert body["status"] == "completed"
    assert body["progress"]["generated"] == len(RECIPIENTS)

    with second_app.state.session_factory() as session:
        statuses = {c.id: c.status for c in session.query(Certificate).filter_by(job_id=job["id"]).all()}
    assert set(statuses.values()) == {CertificateStatus.GENERATED}


class _NoopQueue:
    def enqueue(self, job_id):
        pass

    def shutdown(self):
        pass


def test_percent_complete_counts_failures_as_done(client):
    recipients = [
        {"name": "Pranal Bhatnagar", "email": "pranal@example.com"},
        {"name": "", "email": "bad"},
    ]

    job = create_job(client, recipients=recipients)

    assert job["progress"]["percent_complete"] == 100.0
    assert job["progress"]["failed"] == 1
