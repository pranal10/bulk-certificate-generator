"""One certificate failing must not take the rest of the job down with it."""

import pytest
from reportlab.pdfgen import canvas

from app import processing
from tests.conftest import create_job


@pytest.fixture
def broken_renderer(monkeypatch):
    """Make rendering blow up for one specific recipient only."""
    original = processing.render_certificate_pdf

    def flaky(data, output_path):
        if data.recipient_name == "Broken Person":
            raise RuntimeError("font cache exploded")
        return original(data, output_path)

    monkeypatch.setattr(processing, "render_certificate_pdf", flaky)


RECIPIENTS = [
    {"name": "Pranal Bhatnagar", "email": "pranal@example.com"},
    {"name": "Broken Person", "email": "broken@example.com"},
    {"name": "Priya Sharma", "email": "priya@example.com"},
]


def test_render_failure_only_fails_that_certificate(client, broken_renderer):
    job = create_job(client, recipients=RECIPIENTS)

    assert job["status"] == "completed"
    assert job["progress"]["generated"] == 2
    assert job["progress"]["failed"] == 1

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert [c["status"] for c in items] == ["generated", "failed", "generated"]
    assert items[1]["error"] == "RuntimeError: font cache exploded"
    assert items[1]["download_url"] is None
    assert items[1]["generated_at"] is None


def test_failed_certificates_are_listed_with_their_reason(client, broken_renderer):
    job = create_job(client, recipients=RECIPIENTS)

    response = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["recipient_email"] == "broken@example.com"
    assert "font cache exploded" in body["items"][0]["error"]


def test_failed_certificate_cannot_be_downloaded(client, broken_renderer):
    job = create_job(client, recipients=RECIPIENTS)
    failed = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed"}).json()["items"][0]

    response = client.get(f"/certificates/{failed['id']}/download")

    assert response.status_code == 409
    assert "font cache exploded" in response.json()["detail"]


def test_failed_certificate_leaves_no_file_behind(client, settings, broken_renderer):
    job = create_job(client, recipients=RECIPIENTS)

    pdfs = list((settings.certificates_dir / job["id"]).glob("*.pdf"))
    assert len(pdfs) == 2
    assert not list(settings.certificates_dir.rglob("*.part"))


def test_crash_mid_write_leaves_no_half_written_pdf(client, settings, monkeypatch):
    calls = {"n": 0}
    original_save = canvas.Canvas.save

    def save_then_die_once(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return original_save(self)

    monkeypatch.setattr(canvas.Canvas, "save", save_then_die_once)

    job = create_job(client, recipients=RECIPIENTS)

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    assert [c["status"] for c in items] == ["failed", "generated", "generated"]
    assert items[0]["error"] == "OSError: disk full"
    job_dir = settings.certificates_dir / job["id"]
    assert sorted(p.name for p in job_dir.iterdir()) == sorted(f"{c['id']}.pdf" for c in items[1:])


def test_zip_download_skips_failed_certificates(client, broken_renderer):
    import io
    import zipfile

    job = create_job(client, recipients=RECIPIENTS)

    response = client.get(f"/jobs/{job['id']}/download")

    assert response.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert names == ["0001-pranal-bhatnagar.pdf", "0003-priya-sharma.pdf"]


def test_error_messages_are_truncated(client, monkeypatch):
    def flaky(data, output_path):
        raise RuntimeError("x" * 10_000)

    monkeypatch.setattr(processing, "render_certificate_pdf", flaky)

    job = create_job(client, recipients=RECIPIENTS[:1])

    item = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]
    assert item["status"] == "failed"
    assert len(item["error"]) == processing.MAX_ERROR_LENGTH
