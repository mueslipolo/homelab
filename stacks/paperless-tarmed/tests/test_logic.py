"""Unit tests for the rules the sample documents happen not to exercise.

All data here is invented. Nothing in this repository may contain real patient
names, provider names or GLNs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from paperless_tarmed.config import Config
from paperless_tarmed.normalize import match_patient, shorten_provider
from paperless_tarmed.pipeline import extract_from_pages
from paperless_tarmed.report import DIFFER, FAIL, FILL, OK, classify

TODAY = date(2026, 12, 31)
PATIENTS = ["Anna", "Bruno", "Carla", "David"]

# Several treatment dates, out of order, spanning months -- plus two dates that
# must NOT be mistaken for treatment dates.
MULTI_DATE_PAGE = """Justificatif de remboursement Release 4.5G/fr
Document Identification 987654321 - 04.12.2025 16:05:21 Page: 1
Four. de N° GLN(P) 7600000000001 Laboratoire Meier Tél: 000 000 00 00
Patient Nom MUSTER N° GLN
Prénom Anna
Date de naissance 04.01.1987
Loi LAMal Date/N° facture 04.12.2025 / F_1
Traitement 15.03.2025 - 20.05.2025
Date Tarif Code Code réf. Sé Cô Quantité Montant
20.05.2025 001 00.0010 1 1.00 10.42 17.86
15.03.2025 001 00.0020 1 2.00 10.42 35.73
02.02.2025 317 00.0030 1 1.00 5.21 8.94
Code Taux Montant TVA N° TVA: Montant total: 62.53
Montant de la facture: 62.55
"""


def _config() -> Config:
    return Config(patients=list(PATIENTS))


def _extract(text: str):
    return extract_from_pages([text], _config(), today=TODAY)


def test_oldest_date_wins_across_line_items() -> None:
    result = _extract(MULTI_DATE_PAGE)
    assert result.get("treatment_date").ok, result.get("treatment_date").reason
    # 02.02.2025 appears only as a line item, and is older than the header range.
    assert result.get("treatment_date").value == date(2025, 2, 2)


def test_birth_date_and_invoice_date_are_not_treatment_dates() -> None:
    dates = _extract(MULTI_DATE_PAGE).raw.all_dates
    assert date(1987, 1, 4) not in dates, "date de naissance leaked in"
    assert date(2025, 12, 4) not in dates, "date de facture leaked in"


def test_payable_amount_is_preferred_over_unrounded_total() -> None:
    assert _extract(MULTI_DATE_PAGE).value("total") == Decimal("62.55")  # not 62.53


def test_category_follows_the_highest_value_tariff() -> None:
    # The 001 lines total 53.59; the 317 line is only 8.94.
    assert _extract(MULTI_DATE_PAGE).value("category") == "Médecin"


def test_missing_payable_amount_falls_back_and_warns() -> None:
    result = _extract(MULTI_DATE_PAGE.replace("Montant de la facture: 62.55", ""))
    assert result.get("total").value == Decimal("62.53")
    assert any("Montant total" in w for w in result.warnings)


def test_no_justificatif_page_writes_nothing() -> None:
    result = extract_from_pages(
        ["Just an ordinary invoice.\nMontant total: 99.00"], _config()
    )
    assert not result.get("patient").ok and not result.get("total").ok and not result.title.ok
    assert "no 'Justificatif de remboursement' page" in result.get("patient").reason


def test_unknown_patient_is_never_guessed() -> None:
    result = _extract(MULTI_DATE_PAGE.replace("Prénom Anna", "Prénom Zacharie"))
    assert not result.get("patient").ok
    assert "matches none of" in result.get("patient").reason
    assert not result.title.ok, "title must not be built without a matched patient"


def test_no_patient_list_falls_back_to_the_document_name() -> None:
    """With no allow-list configured the tool must still be useful."""
    result = extract_from_pages([MULTI_DATE_PAGE], Config(patients=[]), today=TODAY)
    assert result.get("patient").ok
    assert result.get("patient").value == "Anna"


def test_single_future_outlier_is_ignored_not_fatal() -> None:
    result = _extract(MULTI_DATE_PAGE.replace("02.02.2025 317", "02.02.2099 317"))
    assert result.get("treatment_date").ok
    assert result.get("treatment_date").value == date(2025, 3, 15)  # oldest real date
    assert any("future date" in w for w in result.warnings)


def test_all_dates_in_the_future_is_rejected() -> None:
    result = _extract(MULTI_DATE_PAGE.replace(".2025", ".2099"))
    assert not result.get("treatment_date").ok
    assert "future" in result.get("treatment_date").reason


def test_misread_year_does_not_drag_the_oldest_date_back() -> None:
    """An OCR digit error must not silently win just because it is older."""
    result = _extract(MULTI_DATE_PAGE.replace("02.02.2025 317", "02.02.2015 317"))
    assert result.get("treatment_date").value == date(2025, 3, 15)
    assert any("misread year" in w for w in result.warnings)


def test_implausible_amount_is_rejected() -> None:
    result = _extract(
        MULTI_DATE_PAGE.replace("facture: 62.55", "facture: 999999.00")
    )
    assert not result.get("total").ok
    assert "ceiling" in result.get("total").reason


def test_several_bundled_justificatifs_are_flagged() -> None:
    second = MULTI_DATE_PAGE.replace("987654321", "111111111")
    result = extract_from_pages([MULTI_DATE_PAGE, second], _config(), today=TODAY)
    assert any("justificatifs in one document" in w for w in result.warnings)


def test_pages_before_and_after_do_not_interfere() -> None:
    bill = "Facture\nMontant total: 5000.00\nPatient Nom AUTRE\nPrénom Zacharie"
    result = extract_from_pages([bill, MULTI_DATE_PAGE, bill], _config(), today=TODAY)
    assert result.get("patient").value == "Anna"
    assert result.get("total").value == Decimal("62.55")


@pytest.mark.parametrize(
    "prenom,nom,expected",
    [
        ("Anna", "MUSTER", "Anna"),
        ("BRUNO,JEAN", "MUSTER", "Bruno"),  # several given names
        ("Carla", "MÜSTER", "Carla"),  # umlaut in the surname
        ("Cárla", "Muster", "Carla"),  # accented given name
        ("david", "muster", "David"),  # lowercased by OCR
        ("Zacharie", "MUSTER", None),  # not in the preset list
    ],
)
def test_concerne_matching(prenom, nom, expected) -> None:
    assert match_patient(prenom, nom, PATIENTS) == expected


def test_concerne_alias() -> None:
    assert match_patient("Annie", None, PATIENTS, {"Annie": "Anna"}) == "Anna"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("RADIOTEST (Dre. Muster)", "Radiotest"),  # parenthetical dropped
        ("Marie Dupont", "Dupont"),  # person: surname
        ("Mme Claire Favre", "Favre"),  # title dropped
        ("Docteur Marie Claire Dupont", "Dupont"),  # doctor titles too: one rule
        ("Dr. Paul Exemple", "Exemple"),
        ("Monsieur Docteur Luc Muster", "Muster"),
        ("Laboratoire Dr Meier", "Meier"),  # organisation: skip the institution word
        ("Viollier Weintraub SA", "Viollier"),  # organisation: first word
        ("Vision Neuville SA (Dr Von Muster", "Vision"),  # unclosed paren
        ("Clinique Rive Gauche (CRG)", "CRG"),  # acronym in parentheses
        ("Hôpitaux régionaux de Neuville", "HRN"),  # organisation, 3+ words
        ("Hôpitaux universitaires de Gen", "HUG"),  # truncated by OCR, still HUG
        ("Centre médical de Neuville", "CMN"),
        ("Hôpital de Neuville", "Neuville"),
    ],
)
def test_shorten_provider(name, expected) -> None:
    """One fixed rule, read off the printed name -- see shorten_provider."""
    assert shorten_provider(name) == expected


def test_gln_override_beats_the_rule() -> None:
    """The escape hatch for names the simple rule shortens badly."""
    out = shorten_provider(
        "Hôpitaux régionaux de Neuville",
        "7600000000004",
        {"7600000000004": "HRN"},
    )
    assert out == "HRN"


@pytest.mark.parametrize(
    "current,extracted,ok,expected",
    [
        (None, "Anna", True, FILL),
        ("", "Anna", True, FILL),
        ("Anna", "Anna", True, OK),
        ("45.0", Decimal("45.00"), True, OK),  # numeric forms agree
        ("Anna", "Bruno", True, DIFFER),
        ("Anna", None, False, FAIL),
    ],
)
def test_report_classification(current, extracted, ok, expected) -> None:
    assert classify(current, extracted, ok) == expected
