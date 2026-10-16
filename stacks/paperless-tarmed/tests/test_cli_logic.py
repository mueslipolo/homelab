"""Tests for the batch-mode plumbing that decides what gets written.

All data here is invented -- no real names, providers or GLNs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from paperless_tarmed.cli import _current_values, _planned_writes
from paperless_tarmed.config import Config
from paperless_tarmed.pipeline import extract_from_pages
from paperless_tarmed.report import DIFFER, FILL, OK, TITLE, build_row

TODAY = date(2026, 12, 31)

FIELDS = {
    "patient": {
        "id": 3,
        "name": "Patient (any name)",
        "data_type": "select",
        "select_options": {"anna": "opt-a", "bruno": "opt-b"},
        "select_labels": {"opt-a": "Anna", "opt-b": "Bruno"},
    },
    "treatment_date": {"id": 4, "name": "Whatever it is called", "data_type": "date"},
    "total": {"id": 5, "name": "Any name you like", "data_type": "monetary"},
}

PAGE = """Justificatif de remboursement Release 4.5G/fr
Document Identification 5 - 04.12.2025 16:05:21 Page: 1
Four. de N° GLN(P) 7600000000001 Marie Dupont Tél: 022 000 00 00
Patient Nom MUSTER N° GLN
Prénom Anna
Traitement 11.04.2025 - 11.04.2025
11.04.2025 590 1062 1 13.00 10.00 1.00 88.20
Montant de la facture: 88.20
"""


PATIENTS = ["Anna", "Bruno", "Carla", "David"]


KEYS = ("patient", "treatment_date", "total")


def _result():
    return extract_from_pages([PAGE], Config(patients=PATIENTS), today=TODAY)


def test_select_value_decodes_to_its_original_label() -> None:
    """A stored option id must read back as 'Anna', never as the folded 'anna'.

    Getting this wrong made every correctly-filled select look like a mismatch.
    """
    doc = {TITLE: "whatever", "custom_fields": [{"field": 3, "value": "opt-a"}]}
    assert _current_values(doc, FIELDS)["patient"] == "Anna"


def test_missing_custom_fields_read_as_empty() -> None:
    current = _current_values({TITLE: "t", "custom_fields": []}, FIELDS)
    assert current["patient"] is None and current["total"] is None


def test_empty_fields_are_filled() -> None:
    result = _result()
    row = build_row(1, {TITLE: "scan_0001.pdf"}, result, KEYS)
    writes, title = _planned_writes(row, result, FIELDS, overwrite=False, do_title=False)

    assert writes == {3: "Anna", 4: date(2025, 4, 11), 5: Decimal("88.20")}
    assert title is None, "--title was not requested"


def test_existing_values_are_not_clobbered_by_default() -> None:
    """Re-running must be safe, and manual corrections must survive."""
    result = _result()
    current = {
        "title": "t",
        "patient": "Bruno",  # deliberately disagrees
        "treatment_date": "2025-04-11",
        "total": "88.20",
    }
    row = build_row(1, current, result, KEYS)
    assert row.status["patient"] == DIFFER
    assert row.status["total"] == OK

    writes, _ = _planned_writes(row, result, FIELDS, overwrite=False, do_title=False)
    assert writes == {}


def test_overwrite_replaces_disagreeing_values_only() -> None:
    result = _result()
    current = {TITLE: "t", "patient": "Bruno", "total": "88.20"}
    row = build_row(1, current, result, KEYS)
    writes, _ = _planned_writes(row, result, FIELDS, overwrite=True, do_title=False)

    assert 3 in writes and writes[3] == "Anna"  # differ -> replaced
    assert 4 in writes  # was empty -> filled
    assert 5 not in writes  # already correct -> untouched


def test_title_requires_its_own_opt_in() -> None:
    result = _result()
    row = build_row(1, {TITLE: "scan_0001.pdf"}, result, KEYS)

    _, without = _planned_writes(row, result, FIELDS, overwrite=False, do_title=False)
    assert without is None

    _, with_title = _planned_writes(row, result, FIELDS, overwrite=False, do_title=True)
    assert with_title == "Anna - Massage classique - 2025-04-11 - Dupont"


def test_failed_extraction_writes_nothing() -> None:
    result = extract_from_pages(
        ["An unrelated invoice"], Config(patients=PATIENTS), today=TODAY
    )
    row = build_row(1, {TITLE: "scan.pdf"}, result, KEYS)
    writes, title = _planned_writes(row, result, FIELDS, overwrite=True, do_title=True)
    assert writes == {} and title is None


def test_numeric_forms_do_not_register_as_disagreement() -> None:
    result = _result()
    row = build_row(1, {TITLE: "t", "total": "88.2"}, result, KEYS)
    assert row.status["total"] == OK


def test_fill_verdict_for_blank_string() -> None:
    result = _result()
    row = build_row(1, {TITLE: "t", "patient": ""}, result, KEYS)
    assert row.status["patient"] == FILL
