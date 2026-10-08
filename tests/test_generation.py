"""Certificate generation (the PDF template)."""

from datetime import date
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from app.certificates import (
    PAGE_SIZE,
    CertificateData,
    UnprintableTextError,
    _fit_font_size,
    render_certificate_pdf,
)
from tests.conftest import create_job, pdf_text


def sample_data(**overrides) -> CertificateData:
    base = dict(
        certificate_id="3f0d3c8a-1111-2222-3333-444444444444",
        recipient_name="Pranal Bhatnagar",
        event_name="Python Bootcamp 2026",
        issue_date=date(2026, 10, 8),
        organization_name="Test Academy",
    )
    base.update(overrides)
    return CertificateData(**base)


def test_renders_a_pdf_containing_the_recipient_details(tmp_path: Path):
    output = tmp_path / "out" / "cert.pdf"

    returned = render_certificate_pdf(sample_data(), output)

    assert returned == output
    content = output.read_bytes()
    assert content.startswith(b"%PDF")
    text = pdf_text(content)
    assert "Pranal Bhatnagar" in text
    assert "Python Bootcamp 2026" in text
    assert "8 October 2026" in text
    assert "3f0d3c8a-1111-2222-3333-444444444444" in text
    assert "Test Academy" in text


def test_pdf_is_a_single_landscape_page(tmp_path: Path):
    from pypdf import PdfReader

    output = render_certificate_pdf(sample_data(), tmp_path / "cert.pdf")

    reader = PdfReader(str(output))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(PAGE_SIZE[0], abs=1)
    assert float(page.mediabox.height) == pytest.approx(PAGE_SIZE[1], abs=1)


def test_long_names_shrink_to_fit_instead_of_overflowing(tmp_path: Path):
    long_name = "Dr. Maria Fernanda de la Cruz Rodríguez-Villanueva y Santamaría Jiménez"

    assert _fit_font_size("Pranal Bhatnagar", "Times-BoldItalic", 36, 600) == 36
    assert _fit_font_size(long_name, "Times-BoldItalic", 36, 600) < 36

    output = render_certificate_pdf(sample_data(recipient_name=long_name), tmp_path / "long.pdf")
    assert long_name in pdf_text(output.read_bytes())


def test_no_temporary_file_is_left_behind(tmp_path: Path):
    render_certificate_pdf(sample_data(), tmp_path / "cert.pdf")

    assert [p.name for p in tmp_path.iterdir()] == ["cert.pdf"]


def test_failed_render_leaves_no_partial_or_final_file(tmp_path: Path, monkeypatch):
    def explode(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(canvas.Canvas, "save", explode)

    with pytest.raises(RuntimeError):
        render_certificate_pdf(sample_data(), tmp_path / "cert.pdf")

    assert list(tmp_path.iterdir()) == []


def test_text_the_template_cannot_print_is_refused(tmp_path: Path):
    with pytest.raises(UnprintableTextError):
        render_certificate_pdf(sample_data(recipient_name="राहुल"), tmp_path / "cert.pdf")

    assert list(tmp_path.iterdir()) == []


# --- through the API ---------------------------------------------------------


def test_generated_certificates_are_written_under_the_job_folder(client, settings):
    job = create_job(client)

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]

    job_dir = settings.certificates_dir / job["id"]
    files = sorted(p.name for p in job_dir.iterdir())
    assert files == sorted(f"{c['id']}.pdf" for c in items)
    assert all(c["status"] == "generated" and c["generated_at"] for c in items)


def test_each_certificate_carries_its_own_recipient(client):
    job = create_job(client)

    for certificate in client.get(f"/jobs/{job['id']}/certificates").json()["items"]:
        content = client.get(certificate["download_url"]).content
        text = pdf_text(content)
        assert certificate["recipient_name"] in text
        assert certificate["id"] in text
