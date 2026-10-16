"""Build paperless_tarmed/data/catalogue.json from the official code lists.

    uv run --extra dev python scripts/build_catalogue.py

Downloads each publisher's own file, keeps only what categorisation needs, and
writes one small JSON file committed with the code. Re-run when a catalogue
publishes a new version; review the diff before committing.

Sources -- all official, all public, no login:
  TARMED 1.09 -- the tariff structure (PDF), table of contents: every chapter
                 and sub-chapter with its official title. Frozen since TARDOC
                 replaced it on 2026-01-01.
  TARDOC      -- OAAT's TARDOC browser, chapter list: every domain ('A') and
                 chapter group ('AA') with its official French title.
  Tarif 590   -- SASIS / santéservices, the 'Tarif 590' Excel: every position
                 with its official chapter and French text.
"""

from __future__ import annotations

import html
import io
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "paperless_tarmed" / "data" / "catalogue.json"

TARMED_URL = "https://www.sumex1.net/images/validators/tarmed/download/tarmed100_109_dt.pdf"
TARDOC_URL = "https://www.tardoc-online.ch/fr/chapitres"
TARIF_590_URL = "https://www.santeservices.ch/sharepoint/public/tarif-590.xlsx"

# Some of these sites turn away clients that do not look like a browser.
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as resp:  # noqa: S310 -- fixed https URLs
        return resp.read()


def tarmed_chapters() -> dict[str, str]:
    """'08' / '08.14' / '00.01.01' -> official (German) title, from the PDF's contents."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument(fetch(TARMED_URL))
    entry = re.compile(r"^(\d{2}(?:\.\d{2})*)\s+(.+?)\s*\.{3,}\s*\d+\s*$")
    chapters: dict[str, str] = {}
    for index in range(1, 20):  # the table of contents; stops where entries end
        for line in pdf[index].get_textpage().get_text_range().splitlines():
            if m := entry.match(line.strip()):
                title = re.sub(r"\s*\(\d{2}\.\d{4} ff\)", "", m.group(2)).strip()
                chapters.setdefault(m.group(1), title)
    return dict(sorted(chapters.items()))


def tardoc_groups() -> dict[str, str]:
    """'A' (domain) / 'AA' (chapter group) -> official French title."""
    page = fetch(TARDOC_URL).decode("utf-8")
    rows = re.findall(
        r"tm-nr'>\s*([A-Z]{1,2})\s*</div>\s*<(?:p|h3)[^>]*>(.*?)</(?:p|h3)>", page, re.S
    )
    groups = {code: html.unescape(re.sub(r"<[^>]+>", "", title)).strip() for code, title in rows}
    return dict(sorted(groups.items()))


def tarif_590() -> dict[str, dict]:
    """Position -> official chapter (1 general, 2 services, 3 remedies, 4 lab) and French text.

    The sheet lists each position once under its main chapter, then again per
    therapy discipline (rows without a chapter number); the first is kept.
    """
    import openpyxl

    sheet = openpyxl.load_workbook(io.BytesIO(fetch(TARIF_590_URL)), read_only=True).worksheets[0]
    positions: dict[str, dict] = {}
    for row in sheet.iter_rows(min_row=3, values_only=True):
        if not row or row[0] != 590 or row[4] in (None, "") or row[12] is None:
            continue
        positions.setdefault(str(row[12]).strip(), {
            "chapter": int(row[4]),
            "chapter_f": (row[10] or row[6] or "").strip(),
            "text_f": re.sub(r"\s+", " ", str(row[14] or "")).strip(),
        })
    return dict(sorted(positions.items()))


def main() -> None:
    catalogue = {
        "built": date.today().isoformat(),
        "sources": {"tarmed": TARMED_URL, "tardoc": TARDOC_URL, "tarif_590": TARIF_590_URL},
        "tarmed_chapters": tarmed_chapters(),
        "tardoc_groups": tardoc_groups(),
        "tarif_590": tarif_590(),
    }
    for key in ("tarmed_chapters", "tardoc_groups", "tarif_590"):
        if not catalogue[key]:
            raise SystemExit(f"{key}: nothing parsed -- has the source's format changed?")
    OUT.write_text(json.dumps(catalogue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(catalogue['tarmed_chapters'])} TARMED chapters, "
          f"{len(catalogue['tardoc_groups'])} TARDOC groups, "
          f"{len(catalogue['tarif_590'])} Tarif 590 positions")


if __name__ == "__main__":
    main()
