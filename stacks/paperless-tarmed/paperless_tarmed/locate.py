"""Finding the justificatif page(s) inside a possibly-larger document."""

from __future__ import annotations

import re
from dataclasses import dataclass

# 'Justi\\w*catif' also matches 'Justicatif', which is what the fi-ligature
# control character degrades to after sanitize(). Accents are optional because
# OCR drops them regularly.
#
# Anchored to the start of a line (after a little non-letter OCR noise): the
# form's heading starts its line in the samples seen, while a billing office's cover letter
# mentions the form mid-sentence -- 'Le justificatif de remboursement ... ci-
# joint' -- and was taken for justificatif #1, so the letter got extracted
# instead of the form behind it.
HEADER_RE = re.compile(
    r"^[^\w\n]{0,20}Justi\w{0,3}catif\s+de\s+remboursement", re.IGNORECASE | re.MULTILINE
)

# 'Document Identification 1000000001 - 04.12.2025' / 'Document 110 000000009/1'
# / Release 5.0: 'Document Identification 4 50 000 003 / 05.10.2026 10:07:54'
DOC_IDENT_RE = re.compile(
    r"Document(?:\s+Identification)?\s+([0-9][0-9\s/.-]{4,30}?)(?=\s{2,}|\s*[·|/-]\s*\d{2}[./]\d{2}|\n)",
    re.IGNORECASE,
)

RELEASE_RE = re.compile(r"Release\s+([0-9]+\.[0-9]+\s*[A-Z]?)", re.IGNORECASE)

# Only the first page of a justificatif carries the patient header block; a
# continuation page holds just the rest of the service table and the totals.
PATIENT_BLOCK_RE = re.compile(r"Patient\s+Nom", re.IGNORECASE)


@dataclass
class JustificatifGroup:
    """One logical justificatif, which may span several pages."""

    ident: str | None
    page_indexes: list[int]
    text: str
    release: str | None = None


def _ident(page_text: str) -> str | None:
    m = DOC_IDENT_RE.search(page_text)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).strip(".-/") or None


def find_groups(pages: list[str]) -> list[JustificatifGroup]:
    """Group justificatif pages by their Document Identification number.

    A justificatif can legitimately span pages ('Page: 1', 'Page 1/1'), so pages
    sharing an identification number belong to one document. Several *distinct*
    identifiers in one upload means several justificatifs were bundled together --
    the caller decides what to do about that rather than us silently picking one.
    """
    groups: list[JustificatifGroup] = []
    for i, text in enumerate(pages):
        if not HEADER_RE.search(text):
            continue
        ident = _ident(text)
        release_m = RELEASE_RE.search(text)
        release = release_m.group(1).strip() if release_m else None

        # Merge on a matching identifier, or when the page cannot be the start of
        # a new justificatif because it has no patient header. Requiring the
        # identifier alone split two-page justificatifs whose 'Document
        # Identification' line did not parse, and the totals live on page 2 --
        # so the amount silently went missing.
        continuation = not PATIENT_BLOCK_RE.search(text)
        if groups and ((ident is not None and groups[-1].ident == ident) or continuation):
            prev = groups[-1]
            prev.page_indexes.append(i)
            prev.text = f"{prev.text}\n{text}"
        else:
            groups.append(
                JustificatifGroup(
                    ident=ident, page_indexes=[i], text=text, release=release
                )
            )
    return groups
