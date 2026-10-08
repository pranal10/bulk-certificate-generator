"""Retrieving generated certificates."""

import io
import zipfile
from pathlib import Path

from tests.conftest import RECIPIENTS, create_job, pdf_text


def test_list_certificates_of_a_job(client):
    job = create_job(client)

    response = client.get(f"/jobs/{job['id']}/certificates")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(RECIPIENTS)
    assert [c["position"] for c in body["items"]] == [0, 1, 2]
    for item, recipient in zip(body["items"], RECIPIENTS, strict=True):
        assert item["job_id"] == job["id"]
        assert item["recipient_name"] == recipient["name"]
        assert item["status"] == "generated"
        assert item["download_url"] == f"/certificates/{item['id']}/download"


def test_list_certificates_filtered_by_status(client):
    recipients = RECIPIENTS + [{"name": "", "email": ""}]
    job = create_job(client, recipients=recipients)

    generated = client.get(f"/jobs/{job['id']}/certificates", params={"status": "generated"}).json()
    failed = client.get(f"/jobs/{job['id']}/certificates", params={"status": "failed"}).json()

    assert generated["total"] == 3
    assert failed["total"] == 1
    assert failed["items"][0]["position"] == 3


def test_list_certificates_is_paginated(client):
    job = create_job(client)

    page = client.get(f"/jobs/{job['id']}/certificates", params={"limit": 2, "offset": 1}).json()

    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert [c["position"] for c in page["items"]] == [1, 2]


def test_invalid_status_filter_is_rejected(client):
    job = create_job(client)

    response = client.get(f"/jobs/{job['id']}/certificates", params={"status": "bogus"})

    assert response.status_code == 422


def test_list_certificates_of_unknown_job_404(client):
    assert client.get("/jobs/nope/certificates").status_code == 404


def test_get_single_certificate(client):
    job = create_job(client)
    certificate = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]

    response = client.get(f"/certificates/{certificate['id']}")

    assert response.status_code == 200
    assert response.json() == certificate


def test_get_unknown_certificate_404(client):
    assert client.get("/certificates/nope").status_code == 404
    assert client.get("/certificates/nope/download").status_code == 404


def test_download_certificate_pdf(client):
    job = create_job(client)
    certificate = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]

    response = client.get(certificate["download_url"])

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"certificate-pranal-bhatnagar-{certificate['id'][:8]}.pdf" in disposition
    assert response.content.startswith(b"%PDF")
    assert "Pranal Bhatnagar" in pdf_text(response.content)


def test_download_pending_certificate_409(manual_client):
    job = create_job(manual_client)
    certificate = manual_client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]

    response = manual_client.get(f"/certificates/{certificate['id']}/download")

    assert response.status_code == 409
    assert "not been generated yet" in response.json()["detail"]


def test_download_when_file_was_removed_from_storage_404(client):
    job = create_job(client)
    certificate = client.get(f"/jobs/{job['id']}/certificates").json()["items"][0]
    with client.app.state.session_factory() as session:
        from app.models import Certificate

        Path(session.get(Certificate, certificate["id"]).file_path).unlink()

    response = client.get(certificate["download_url"])

    assert response.status_code == 404
    assert "missing from storage" in response.json()["detail"]


def test_download_all_certificates_as_zip(client):
    job = create_job(client)

    response = client.get(f"/jobs/{job['id']}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"certificates-python-bootcamp-2026-{job['id'][:8]}.zip" in response.headers["content-disposition"]

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.namelist() == [
        "0001-pranal-bhatnagar.pdf",
        "0002-priya-sharma.pdf",
        "0003-rohan-mehta.pdf",
    ]
    for name in archive.namelist():
        assert archive.read(name).startswith(b"%PDF")


def test_zip_download_before_completion_409(manual_client):
    job = create_job(manual_client)

    response = manual_client.get(f"/jobs/{job['id']}/download")

    assert response.status_code == 409
    assert "still pending" in response.json()["detail"]


def test_zip_download_with_nothing_generated_404(client):
    job = create_job(client, recipients=[{"name": "", "email": ""}])

    response = client.get(f"/jobs/{job['id']}/download")

    assert response.status_code == 404


def test_zip_download_unknown_job_404(client):
    assert client.get("/jobs/nope/download").status_code == 404
