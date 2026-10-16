"""Getting clean text out of a PDF, one page at a time.

Page isolation matters: a document may bundle the bill itself before or after the
justificatif, and a bill page also contains 'Montant total'. Working per page is
what stops those from bleeding into each other.
"""

from __future__ import annotations

from pathlib import Path

NBSP = " "


def sanitize(text: str) -> str:
    """Strip the junk that breaks naive matching.

    Native PDFs whose fonts lack a ToUnicode entry for a ligature emit a control
    character instead: one real sample renders 'Justificatif' as 'Justi\\x07catif'.
    Dropping C0 controls turns that into 'Justicatif', which the tolerant header
    pattern in locate.py still matches.
    """
    cleaned = "".join(c for c in text if ord(c) >= 32 or c in "\r\n\t")
    return cleaned.replace(NBSP, " ").replace("\r\n", "\n").replace("\r", "\n")


def pages_from_pdf(path: str | Path) -> list[str]:
    """Return sanitized text for every page.

    Uses the embedded text layer. For a scan that means whatever OCR Paperless
    already ran during consume -- we never OCR anything ourselves.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for page in pdf:
            textpage = page.get_textpage()
            try:
                out.append(sanitize(textpage.get_text_range()))
            finally:
                textpage.close()
        return out
    finally:
        pdf.close()
