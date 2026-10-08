"""The predefined certificate template, rendered to PDF with reportlab.

There is exactly one design. Everything recipient-specific comes in through
`CertificateData`; the layout itself is fixed.
"""

import os
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

PAGE_SIZE = landscape(A4)  # 842 x 595 pt

# reportlab's built-in Type 1 fonts (Helvetica, Times, Courier) only carry the
# WinAnsi (cp1252) character set. Anything outside it - Devanagari, CJK,
# emoji - would silently print as "missing glyph" boxes, so we check up-front.
FONT_ENCODING = "cp1252"

NAVY = HexColor("#1F2A44")
GOLD = HexColor("#C9A227")
GREY = HexColor("#5F6673")
LIGHT_GREY = HexColor("#9AA0AA")


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    recipient_name: str
    event_name: str
    issue_date: date
    organization_name: str


def unprintable_characters(text: str) -> str:
    """Return the distinct characters in `text` that the template cannot print.

    Empty string means the text is safe to render.
    """
    bad: list[str] = []
    for ch in text:
        if unicodedata.category(ch) == "Cc":  # control characters, newlines...
            bad.append(ch)
            continue
        try:
            ch.encode(FONT_ENCODING)
        except UnicodeEncodeError:
            bad.append(ch)
    return "".join(dict.fromkeys(bad))


class UnprintableTextError(ValueError):
    pass


def _fit_font_size(text: str, font: str, max_size: int, max_width: float, min_size: int = 12) -> int:
    """Shrink the font until the text fits in `max_width` (long names, long event titles)."""
    size = max_size
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _draw_text(
    c: canvas.Canvas,
    text: str,
    y: float,
    font: str,
    size: int,
    color,
    *,
    x: float | None = None,
    align: str = "center",
    char_space: float = 0,
) -> None:
    """Draw one line of text. Letter-spacing (PDF `Tc`) is part of the graphics
    state and would otherwise leak into every later string, so every call sets
    it explicitly through a text object."""
    text_width = pdfmetrics.stringWidth(text, font, size) + char_space * max(len(text) - 1, 0)
    if x is None:
        x = PAGE_SIZE[0] / 2
    if align == "center":
        start_x = x - text_width / 2
    elif align == "right":
        start_x = x - text_width
    else:
        start_x = x

    text_obj = c.beginText(start_x, y)
    text_obj.setFont(font, size)
    text_obj.setFillColor(color)
    text_obj.setCharSpace(char_space)
    text_obj.textOut(text)
    c.drawText(text_obj)


def _format_date(d: date) -> str:
    # "8 October 2026" - avoids the platform-specific %-d strftime flag.
    return f"{d.day} {d:%B %Y}"


def _draw_template(c: canvas.Canvas, data: CertificateData) -> None:
    width, height = PAGE_SIZE

    # Double border: a heavy navy frame with a thin gold inset.
    c.setStrokeColor(NAVY)
    c.setLineWidth(3)
    c.rect(24, 24, width - 48, height - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.rect(34, 34, width - 68, height - 68)

    _draw_text(c, data.organization_name.upper(), height - 95, "Helvetica", 12, GREY, char_space=3)

    _draw_text(c, "CERTIFICATE", height - 160, "Helvetica-Bold", 44, NAVY, char_space=6)
    _draw_text(c, "OF COMPLETION", height - 185, "Helvetica", 16, GOLD, char_space=5)

    _draw_text(c, "This certificate is proudly presented to", height - 240, "Helvetica", 14, GREY)

    name_font = "Times-BoldItalic"
    name_size = _fit_font_size(data.recipient_name, name_font, 36, width - 200)
    _draw_text(c, data.recipient_name, height - 290, name_font, name_size, NAVY)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(width / 2 - 220, height - 302, width / 2 + 220, height - 302)

    _draw_text(c, "for successfully completing", height - 340, "Helvetica", 14, GREY)

    event_font = "Helvetica-Bold"
    event_size = _fit_font_size(data.event_name, event_font, 22, width - 200)
    _draw_text(c, data.event_name, height - 372, event_font, event_size, NAVY)

    _draw_text(c, f"Issued on {_format_date(data.issue_date)}", height - 412, "Helvetica", 12, GREY)

    # Footer: signature line on the left, certificate id on the right.
    c.setStrokeColor(NAVY)
    c.setLineWidth(1)
    c.line(90, 95, 290, 95)
    _draw_text(c, "Authorized Signatory", 80, "Helvetica", 10, GREY, x=190)
    _draw_text(c, data.organization_name, 66, "Helvetica", 10, GREY, x=190)

    _draw_text(c, "Certificate ID", 80, "Helvetica", 9, LIGHT_GREY, x=width - 90, align="right")
    _draw_text(c, data.certificate_id, 66, "Courier", 9, LIGHT_GREY, x=width - 90, align="right")


def render_certificate_pdf(data: CertificateData, output_path: Path) -> Path:
    """Render the template for one recipient and write it to `output_path`.

    The PDF is written to a temporary file first and moved into place at the
    end, so a crash half-way never leaves a truncated file that looks like a
    finished certificate.
    """
    for label, text in (("recipient name", data.recipient_name), ("event name", data.event_name)):
        bad = unprintable_characters(text)
        if bad:
            raise UnprintableTextError(f"{label} contains characters the template cannot print: {bad!r}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".part")

    try:
        c = canvas.Canvas(str(tmp_path), pagesize=PAGE_SIZE)
        c.setTitle(f"Certificate - {data.recipient_name}")
        c.setAuthor(data.organization_name)
        c.setSubject(data.event_name)
        _draw_template(c, data)
        c.showPage()
        c.save()
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return output_path
