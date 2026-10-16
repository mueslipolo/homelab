"""The one path from a PDF to a decided Extraction.

Both entry points -- the webhook and the batch CLI -- go through here, so what
gets written automatically is exactly what the audit reported.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import values as V
from .confidence import evaluate
from .config import Config
from .extract import extract
from .locate import find_groups
from .models import Extraction, FieldOutcome
from .text import pages_from_pdf


def extract_from_pages(
    pages: list[str],
    cfg: Config,
    *,
    doc_id: int | None = None,
    source: str | None = None,
    today: date | None = None,
) -> Extraction:
    groups = find_groups(pages)
    if not groups:
        # A PDF with no text layer is a Paperless OCR problem, not a layout one,
        # and the remedy is completely different -- so say which it is.
        blank = sum(1 for page in pages if not page.strip())
        if blank == len(pages):
            reason = (
                f"no text layer at all ({len(pages)} page(s)) -- Paperless OCR did not "
                "run on this document, so there is nothing to read"
            )
        elif blank:
            reason = (
                f"no 'Justificatif de remboursement' page found in {len(pages)} page(s); "
                f"{blank} of them have no text layer (Paperless OCR skipped those pages)"
            )
        else:
            reason = f"no 'Justificatif de remboursement' page found in {len(pages)} page(s)"
        return Extraction(
            doc_id=doc_id,
            source=source,
            values={key: FieldOutcome(reason=reason) for key in V.ALL},
            title=FieldOutcome(reason=reason),
            found=False,
        )

    raw = extract(groups[0])
    result = evaluate(raw, cfg, today=today, doc_id=doc_id, source=source)
    if len(groups) > 1:
        # Several distinct justificatifs bundled together: use the first, but say
        # so rather than silently choosing.
        idents = [g.ident or "?" for g in groups]
        result.warnings.append(
            f"{len(groups)} justificatifs in one document (idents {idents}); used the first"
        )
    return result


def extract_pdf(
    path: str | Path,
    cfg: Config,
    *,
    doc_id: int | None = None,
    today: date | None = None,
) -> Extraction:
    return extract_from_pages(
        pages_from_pdf(path),
        cfg,
        doc_id=doc_id,
        source=str(path),
        today=today,
    )
