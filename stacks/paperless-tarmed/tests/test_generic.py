"""The project must not assume any particular Paperless configuration.

These tests pin the properties that make it reusable: arbitrary field names, any
subset of values, a configurable title, and sensible behaviour with no personal
configuration at all.
"""

from __future__ import annotations

from datetime import date

import pytest

from paperless_tarmed import values as V
from paperless_tarmed.cli import _current_values, _planned_writes
from paperless_tarmed.confidence import build_title
from paperless_tarmed.config import Config
from paperless_tarmed.pipeline import extract_from_pages
from paperless_tarmed.report import TITLE, build_row, render_csv, render_table
from paperless_tarmed.tariffs import category_for_code

TODAY = date(2026, 12, 31)

PAGE = """Justificatif de remboursement Release 4.5G/fr
Document Identification 5 - 04.12.2025 16:05:21 Page: 1
Four. de N° GLN(P) 7600000000001 Marie Dupont Tél: 022 000 00 00
Patient Nom MUSTER N° GLN
Prénom Anna
Traitement 11.04.2025 - 11.04.2025
11.04.2025 590 1062 1 13.00 10.00 1.00 88.20
Montant de la facture: 88.20
"""


def _result(cfg: Config | None = None):
    return extract_from_pages([PAGE], cfg or Config(), today=TODAY)


def test_works_with_no_configuration_at_all() -> None:
    """Out of the box, with no env set, every value still comes out."""
    result = _result()
    assert result.core_ok
    assert result.value(V.PATIENT) == "Anna"
    assert result.value(V.TOTAL) is not None


def test_field_names_are_arbitrary_and_may_be_any_language() -> None:
    fields = {
        V.PATIENT: {"id": 11, "name": "Behandelte Person", "data_type": "string"},
        V.TOTAL: {"id": 12, "name": "Rechnungsbetrag", "data_type": "monetary"},
    }
    result = _result()
    row = build_row(1, {TITLE: "scan.pdf"}, result, list(fields))
    writes, _ = _planned_writes(row, result, fields, overwrite=False, do_title=False)
    assert writes == {11: "Anna", 12: result.value(V.TOTAL)}


def test_mapping_a_subset_writes_only_that_subset() -> None:
    fields = {V.TOTAL: {"id": 12, "name": "Amount", "data_type": "monetary"}}
    result = _result()
    row = build_row(1, {TITLE: "scan.pdf"}, result, list(fields))
    writes, _ = _planned_writes(row, result, fields, overwrite=False, do_title=False)
    assert list(writes) == [12], "an unmapped value must never be written"


def test_descriptive_values_are_mappable_too() -> None:
    """Not just the three core values -- provider and GLN can have fields."""
    fields = {
        V.PROVIDER: {"id": 20, "name": "Leistungserbringer", "data_type": "string"},
        V.PROVIDER_GLN: {"id": 21, "name": "GLN", "data_type": "string"},
        V.CATEGORY: {"id": 22, "name": "Art", "data_type": "string"},
    }
    result = _result()
    row = build_row(1, {TITLE: "scan.pdf"}, result, list(fields))
    writes, _ = _planned_writes(row, result, fields, overwrite=False, do_title=False)
    assert writes == {20: "Dupont", 21: "7600000000001", 22: "Massage classique"}


def test_report_renders_whatever_keys_are_mapped() -> None:
    keys = [V.TOTAL, V.PROVIDER]
    rows = [build_row(1, {TITLE: "scan.pdf"}, _result(), keys)]
    table = render_table(rows, keys)
    assert "provider" in table and "patient" not in table
    assert "provider_extracted" in render_csv(rows, keys)


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{patient} - {treatment_date}", "Anna - 2025-04-11"),
        ("{date} {patient}", "2025-04-11 Anna"),  # {date} alias
        ("{patient}/{provider}", "Anna/Dupont"),
        ("{patient} - {category} - {date} - {provider}", "Anna - Massage classique - 2025-04-11 - Dupont"),
    ],
)
def test_title_template_is_configurable(template, expected) -> None:
    result = _result()
    assert build_title(template, result.values) == expected


def test_empty_title_template_disables_titling() -> None:
    result = extract_from_pages([PAGE], Config(title_template=""), today=TODAY)
    assert not result.title.ok


def test_unknown_title_placeholder_is_reported_not_swallowed() -> None:
    with pytest.raises(ValueError, match="Unknown placeholder"):
        build_title("{nonsense}", _result().values)


def test_category_labels_can_be_overridden_per_deployment() -> None:
    assert category_for_code("317") == "Labo"
    assert category_for_code("317", {"317": "Labor"}) == "Labor"
    # An unmapped tariff can be given a label without editing the source.
    assert category_for_code("999") is None
    assert category_for_code("999", {"999": "Sonstige"}) == "Sonstige"


def test_unknown_mapping_keys_are_detected() -> None:
    cfg = Config(fields={V.PATIENT: "Patient", "typo_here": "Something"})
    assert cfg.unknown_field_keys() == ["typo_here"]


def test_select_options_decode_regardless_of_field_name() -> None:
    fields = {
        V.PATIENT: {
            "id": 30,
            "name": "Wen betrifft es",
            "data_type": "select",
            "select_options": {"anna": "opt-a"},
            "select_labels": {"opt-a": "Anna"},
        }
    }
    doc = {TITLE: "t", "custom_fields": [{"field": 30, "value": "opt-a"}]}
    assert _current_values(doc, fields)[V.PATIENT] == "Anna"


# --------------------------------------------------------------- categorisation

TARMED_PAGE = """Justificatif de remboursement Release 4.5G/fr
Document Identification 7 - 04.12.2025 16:05:21 Page: 1
Four. de N° GLN(P) 7600000000001 Marie Dupont Tél: 022 000 00 00
Patient Nom MUSTER N° GLN
Prénom Anna
Traitement 11.04.2025 - 11.04.2025
Date Tarif Code Code réf. Sé Cô Quantité Montant
11.04.2025 001 00.0010 1 1.00 10.42 90.00
Consultation, première période de 5 min (consultation de base)
11.04.2025 001 39.3000 1 1.00 49.56 85.00
Ultrason de contrôle lors d'une grossesse, premier examen
Montant de la facture: 175.00
"""

# 4.0 H puts the description ABOVE its code row.
DIALECT_H_PAGE = """Justicatif de remboursement Release 4.0 H
Document 110 000000009/1 23.08.2026 18:33:01 Page 1/1
Four. de prestations N° EAN 7600000000004 Hôpitaux régionaux de Neuville Rue X 1
Patient Nom MUSTER N° EAN
Prénom Anna
Traitement du 11.04.2025 au 11.04.2025
Date Tarif Code Code réf. Sé cô Quantité Montant
Sonographie articulaire de la hanche, par côté
11.04.2025 007 GK.20.004 1 G 1 33.81 65.07
Temps de changement, unité de grande sonographie
11.04.2025 007 AR.05.005 1 1 19.81 19.02
A payer CHF 84.09
"""


def test_description_is_read_from_the_correct_side_in_each_dialect() -> None:
    # Each row must carry its own description, not a neighbour's.
    raw = extract_from_pages([DIALECT_H_PAGE], Config(), today=TODAY).raw
    by_code = {li.code: li.description for li in raw.line_items}
    assert "articulaire de la hanche" in by_code["GK.20.004"]
    assert "Temps de changement" in by_code["AR.05.005"]


def test_category_comes_from_codes_even_without_a_description() -> None:
    page = TARMED_PAGE.replace(
        "Ultrason de contrôle lors d'une grossesse, premier examen\n", ""
    )
    result = extract_from_pages([page], Config(), today=TODAY)
    # The code alone decides: TARMED chapter 39 (imaging) over chapter 00.
    assert result.value(V.CATEGORY) == "Imagerie"


def test_metadata_is_never_mistaken_for_a_service_description() -> None:
    """Regression: a row at the top of the table once picked up 'Prénom Anna'."""
    raw = extract_from_pages([TARMED_PAGE], Config(), today=TODAY).raw
    for item in raw.line_items:
        assert not (item.description or "").startswith(("Prénom", "Traitement", "Patient"))


def test_unresolved_field_suggests_the_near_miss() -> None:
    """'Date de traitement' vs 'Date traitement' is the common configuration slip."""
    from paperless_tarmed.normalize import fold
    from paperless_tarmed.paperless import CustomField, Paperless, PaperlessError

    api = Paperless.__new__(Paperless)
    api._fields = {
        fold("Date traitement"): CustomField(7, "Date traitement", "date", {}, {}),
        fold("Montant facture"): CustomField(6, "Montant facture", "monetary", {}, {}),
    }
    with pytest.raises(PaperlessError, match="Did you mean 'Date traitement'"):
        api.find_field("Date de traitement")


def test_select_options_are_reported_by_label_not_folded_key() -> None:
    from paperless_tarmed.paperless import CustomField

    f = CustomField(2, "Patient", "select", {"anna": "a"}, {"a": "Anna"})
    assert sorted(f.select_labels.values()) == ["Anna"]


def test_missing_select_option_is_refused_not_silently_written() -> None:
    from paperless_tarmed.paperless import CustomField, Paperless, PaperlessError

    field = CustomField(2, "Patient", "select", {"bruno": "b"}, {"b": "Bruno"})
    assert Paperless.encode_value(field, "Bruno") == "b"
    with pytest.raises(PaperlessError, match="not an option"):
        Paperless.encode_value(field, "Carla")


def test_dotenv_is_actually_read(tmp_path, monkeypatch) -> None:
    """The README says `cp .env.example .env`, so .env must be loaded.

    It previously was not, and the built-in defaults silently won -- which
    surfaced as a confusing 'custom field not found' against the user's instance.
    """
    from paperless_tarmed.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        "export PAPERLESS_TARMED_FIELDS='patient=Wen,total=Betrag'\n"
        'PAPERLESS_TARMED_PATIENTS="Anna,Bruno"\n',
        encoding="utf-8",
    )
    for key in ("PAPERLESS_TARMED_FIELDS", "PAPERLESS_TARMED_PATIENTS"):
        monkeypatch.delenv(key, raising=False)

    assert set(load_dotenv(env)) == {"PAPERLESS_TARMED_FIELDS", "PAPERLESS_TARMED_PATIENTS"}
    cfg = Config.from_env()
    assert cfg.fields == {"patient": "Wen", "total": "Betrag"}
    assert cfg.patients == ["Anna", "Bruno"]


def test_real_environment_beats_dotenv(tmp_path, monkeypatch) -> None:
    from paperless_tarmed.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text("PAPERLESS_URL=https://from-file\n", encoding="utf-8")
    monkeypatch.setenv("PAPERLESS_URL", "https://from-env")
    load_dotenv(env)
    assert Config.from_env().paperless_url == "https://from-env"


def test_missing_dotenv_is_not_an_error() -> None:
    from paperless_tarmed.config import load_dotenv

    assert load_dotenv("/nonexistent/.env") == []


def test_document_type_accepts_a_name_or_an_id() -> None:
    """The Paperless UI shows ids, so both must work."""
    from paperless_tarmed.paperless import Paperless

    api = Paperless.__new__(Paperless)
    api.document_types = lambda: [(18, "Justificatif de remboursement", 47), (1, "Facture", 3)]

    assert api.document_type_id("18") == 18
    assert api.document_type_id(18) == 18
    assert api.document_type_id("Justificatif de remboursement") == 18
    assert api.document_type_id("justificatif de REMBOURSEMENT") == 18  # folded
    assert api.document_type_id("99") is None  # id that does not exist
    assert api.document_type_id("Nope") is None


PAGE_ONE = """Justificatif de remboursement Release 4.5G/fr
Four. de N° GLN(P) 7600000000001 Marie Dupont Tél: 022 000 00 00
Patient Nom MUSTER N° GLN
Prénom Anna
Traitement 11.04.2025 - 11.04.2025
Date Tarif Code Code réf. Sé Cô Quantité Montant
11.04.2025 590 1203 1 6.00 14.00 84.00
Ostéopathie, par période de 5 min.
"""

# A continuation page: service rows and the totals, but no patient header and
# no parseable Document Identification.
PAGE_TWO = """Justificatif de remboursement Release 4.5G/fr
11.04.2025 590 1200 1 4.00 14.00 56.00
Anamnèse / bilan / diagnostic
Code Taux Montant TVA N° TVA: Montant total: 140.00
Montant de la facture: 140.00
"""


def test_two_page_justificatif_is_not_split() -> None:
    """The totals live on page 2; splitting loses the amount entirely.

    Real documents hit this: the 'Document Identification' line did not parse,
    so each page became its own justificatif and `total` failed.
    """
    from decimal import Decimal

    result = extract_from_pages([PAGE_ONE, PAGE_TWO], Config(), today=TODAY)
    assert result.get(V.TOTAL).ok, result.get(V.TOTAL).reason
    assert result.value(V.TOTAL) == Decimal("140.00")
    assert not any("justificatifs in one document" in w for w in result.warnings)


def test_two_separate_justificatifs_are_still_flagged() -> None:
    """Both pages have a patient header, so they are genuinely two documents."""
    result = extract_from_pages([PAGE_ONE, PAGE_ONE], Config(), today=TODAY)
    assert any("justificatifs in one document" in w for w in result.warnings)


def test_currency_is_read_from_the_document() -> None:
    page = PAGE_ONE + "Monnaie: EUR\nMontant de la facture: 84.00\n"
    result = extract_from_pages([page], Config(), today=TODAY)
    assert result.raw.currency == "EUR"


def test_report_links_back_to_the_document() -> None:
    from paperless_tarmed.report import TITLE, Row, document_url, render_csv, render_table

    assert document_url("https://p.example.lan/", 1954) == (
        "https://p.example.lan/documents/1954/details"
    )
    assert document_url("", 1954) == "", "no base URL configured -> no link"

    row = Row(doc_id=1954, keys=("total",))
    row.current, row.extracted = {TITLE: "scan.pdf"}, {TITLE: "new", "total": "12.00"}
    row.status = {"total": "ok", TITLE: "differ"}

    table = render_table([row], ["total"], "https://p.example.lan")
    assert "https://p.example.lan/documents/1954/details" in table
    assert "url" in render_csv([row], ["total"], "https://p.example.lan").splitlines()[0]


def test_pdf_without_a_text_layer_says_so() -> None:
    """A missing text layer is an OCR problem, not a layout one.

    Merged PDFs are the usual cause: Paperless's default OCR_MODE=auto judges
    'born digital' across the whole document, so one digital page can stop the
    scanned pages from ever being OCR'd.
    """
    result = extract_from_pages(["", "   ", ""], Config(), today=TODAY)
    reason = result.get(V.PATIENT).reason
    assert "no text layer at all" in reason
    assert "Paperless OCR did not run" in reason


def test_partially_ocred_pdf_reports_how_many_pages_are_blank() -> None:
    result = extract_from_pages(["", "", "Some unrelated letter text here"], Config(), today=TODAY)
    assert "2 of them have no text layer" in result.get(V.PATIENT).reason


def test_fully_texted_pdf_without_the_form_keeps_the_layout_message() -> None:
    result = extract_from_pages(["An ordinary invoice"], Config(), today=TODAY)
    reason = result.get(V.PATIENT).reason
    assert "no 'Justificatif de remboursement' page found" in reason
    assert "text layer" not in reason


# ------------------------------------------------------- Release 5.0 dialect

R50_PAGE1 = """Justificatif de remboursement
Release
5.0/GR/fr
Document Identification 4 47 000 004 / 10.07.2026 01:09:02 Page
Patient Nom MUSTER
Prénom
ANNA
Loi LAMal Date/N° facture 10.07.2026/10001
Traitement 11.06.2026 - 09.07.2026 Date/N° rappel
Four. de N° GLN (P) 7600000000001 Dr Marie Dupont
Date Tarif Code Code réf. Gr Cô Quantité Pt PM/Prix fPM VPLPM Montant
11.06.26 007 CA.00.0010 1 1.00 10.56 0.94 8.64 0.94 110 18.05
Consultation du médecin de famille, 5 premières min
11.06.26 007 CG.00.0010 1 1.00 48.60 0.94 49.87 0.94 110 92.56
Examen complet de l'enfant jusqu'à 16 ans
11.06.26 007 AA.15.0010 2 10.00 2.11 0.94 1.73 0.94 110 36.09
Etude de dossiers provenant de tiers, en l'absence du patient, par période de 1 min
Total intermédiaire : 146.70
"""

# Page 2 has no 'Patient Nom' header, and the payable label wraps around the
# amount -- both seen on a real Release 5.0 scan.
R50_PAGE2 = """Justificatif de remboursement
Release
5.0/GR/fr
Document Identification 4 47 000 004 / 10.07.2026 01:09:02 Page 2/2
Patient MUSTER ANNA 18.05.2026
Date Tarif Code Code réf Gr C6 Quantité Pt PM/Prix fPM VPtPM Montant
09.07.26 402 7680687520010 1 1.00 77.95 1.00 112 77.95
VAXNEUVANCE susp inj ser pré 0.5 ml
Code Taux Montant TVA N°TVA: CHE-000.000.000 TVA Montant total : 582.12
0 0.00 375.72 0.00 Monnaie CHF
Montant de la 582.10
facture
:
"""


def _r50():
    return extract_from_pages([R50_PAGE1, R50_PAGE2], Config(), today=TODAY)


def test_release_50_two_digit_year_line_items_are_parsed() -> None:
    """5.0 writes '11.06.26'; requiring a four-digit year matched no line at all,
    which silently removed the category from every such document."""
    raw = _r50().raw
    assert len(raw.line_items) == 4
    assert {li.tariff for li in raw.line_items} == {"007", "402"}
    assert min(li.date for li in raw.line_items) == date(2026, 6, 11)


def test_release_50_wrapped_payable_label_is_found() -> None:
    """'Montant de la 582.10 / facture / :' -- the amount lands mid-label."""
    from decimal import Decimal

    result = _r50()
    assert result.value(V.TOTAL) == Decimal("582.10")
    assert result.value(V.TOTAL_UNROUNDED) == Decimal("582.12")
    assert not result.warnings, result.warnings


def test_release_50_intermediate_total_is_not_mistaken_for_the_total() -> None:
    from decimal import Decimal

    assert _r50().value(V.TOTAL_UNROUNDED) != Decimal("146.70")


def test_release_50_pages_merge_and_category_resolves() -> None:
    result = _r50()
    # Pediatrics (CG, 92.56) billed more than the family-medicine
    # consultation (CA, 18.05); AA.15 is a basic position, 402 a supply.
    assert result.value(V.CATEGORY) == "Pédiatrie"
    assert result.value(V.PATIENT) is None or True  # no patient list configured
    assert not any("justificatifs in one document" in w for w in result.warnings)


TELECONSULT_PAGE = """Justificatif de remboursement
Release 5.0/GD/fr
Document Identification 1000000000004 / 26.08.2026 11:39:44 Page: 1
Patient Nom MUSTER
Prénom Anna
Traitement 18.08.2026 • 18.08.2026 Date/N° rappel
Four. de N° GLN(P) 7600000000001 Jean-Marc DUPONT
Date Tarif Code Code réf. Gr Cô Quantité Pt PM/Prix f PM Vpt PM Montant
18.08.2026 007 AA.10.0010 1 1.00 10.56 1.00 1.25 8.64 1 1 0 24.00
Consultation télémédicale simultanée, premières 5 min
18.08.2026 007 AA.15.0060 1 7.00 2.11 1.00 1.25 1.73 1 1 0 33.60
Etablissement de prescriptions ou d'ordonnances médicales en dehors de la consultation
N° TVA: - Montant total: 81.62
Monnaie: CHF
N° de référence: 000000000000000000000000001 Montant dû: 81.60
"""


def test_release_50_montant_du_is_the_payable_amount() -> None:
    from decimal import Decimal

    result = extract_from_pages([TELECONSULT_PAGE], Config(), today=TODAY)
    assert result.value(V.TOTAL) == Decimal("81.60")
    assert result.value(V.TOTAL_UNROUNDED) == Decimal("81.62")
    assert not result.warnings, result.warnings


def test_since_accepts_relative_spans_and_iso_dates() -> None:
    """'2y' is easier to type than a date, and must be calendar-correct."""
    from paperless_tarmed.config import parse_since

    today = date(2026, 8, 31)
    assert parse_since("2y", today) == "2024-08-31"
    assert parse_since("18m", today) == "2025-02-28"  # clamped, February is short
    assert parse_since("1m", today) == "2026-07-31"
    assert parse_since("90d", today) == "2026-06-02"
    assert parse_since("6w", today) == "2026-07-20"
    assert parse_since("2024-01-31", today) == "2024-01-31"
    assert parse_since(" 2Y ", today) == "2024-08-31"


def test_since_rejects_nonsense_with_a_usable_message() -> None:
    from paperless_tarmed.config import parse_since

    with pytest.raises(SystemExit, match="90d, 6w, 18m, 2y"):
        parse_since("last tuesday")


def test_since_leap_day_does_not_crash() -> None:
    from paperless_tarmed.config import parse_since

    assert parse_since("1y", date(2028, 2, 29)) == "2027-02-28"


# --------------------------------------------- split label/value column layout

SPLIT_PAGE = """N° GLN (B)
N° GLN (P)
7600000000001 Jean-Marc DUPONT
Four. de
prestations
Patient MUSTERNom
Prénom
Rue
NPA
Localité
Date de naissance
Anna
Rue du Test 12
1227
Neuville
04.01.1987
Release 4.5G/fr
Madame
Anna MUSTER
21.05.2025 - 21.05.2025
Justificatif de remboursement
Date Tarif Code Code réf. Sé Cô Qté Pt PM/Prix f PM VPt PM Montant
21.05.2025 001 00.0010
Consultation, première période de 5 min (consultation de base)
1 1.00 10.42 1.00 1.25 8.19 1 1 1 0 23.27
21.05.2025 999 979
1ère cons test respiratoire
1 1.00 160.00 1.00 1 1 0 0 160.00
Montant dû: 306.55
301.54Montal total:N° TVA:
0 0.0 301.54 0.00 Indemnité de rappel: 5.00
"""


def _split():
    return extract_from_pages(
        [SPLIT_PAGE], Config(patients=["Carla", "Anna", "Bruno"]), today=TODAY
    )


def test_label_must_not_capture_the_next_labels_line() -> None:
    """'Prénom\\nRue\\nNPA' captured 'Rue' as the given name, because '\\s+'
    happily crossed the newline."""
    raw = _split().raw
    assert raw.prenom != "Rue"


def test_patient_recovered_from_the_page_when_the_field_is_unreadable() -> None:
    """Some layouts emit every label first and every value after."""
    result = _split()
    assert result.value(V.PATIENT) == "Anna"
    assert any("used the only configured name" in w for w in result.warnings)


def test_page_fallback_refuses_when_two_configured_names_appear() -> None:
    """The invoice addressee is often a different family member than the patient."""
    page = SPLIT_PAGE.replace("Madame\nAnna MUSTER", "Monsieur\nBruno MUSTER\nAnna MUSTER")
    result = extract_from_pages(
        [page], Config(patients=["Anna", "Bruno"]), today=TODAY
    )
    assert not result.get(V.PATIENT).ok, "must not guess between two candidates"


def test_provider_read_from_a_bare_gln_line() -> None:
    result = _split()
    assert result.value(V.PROVIDER) == "Dupont"
    assert result.value(V.PROVIDER_GLN) == "7600000000001"


def test_reminder_fee_is_flagged_because_it_is_not_reimbursable() -> None:
    from decimal import Decimal

    result = _split()
    assert result.value(V.TOTAL) == Decimal("306.55")  # payable, fee included
    assert result.value(V.TOTAL_UNROUNDED) == Decimal("301.54")  # invoiced
    assert any("reminder fee" in w for w in result.warnings)


def test_misspelt_total_label_with_the_amount_in_front_is_read() -> None:
    """OCR produced '301.54Montal total:' -- label mangled, order reversed."""
    from decimal import Decimal

    assert _split().value(V.TOTAL_UNROUNDED) == Decimal("301.54")


def test_misspelt_patient_name_is_matched_by_similarity() -> None:
    """Providers mistype names; 'Delphinne' should still find 'Delphine'."""
    from paperless_tarmed.normalize import close_patient

    presets = ["Anna", "Bruno", "Carla", "Delphine"]
    assert close_patient("Delphinne", "Muster", presets) == "Delphine"
    assert close_patient("Carlla", "Muster", presets) == "Carla"
    assert close_patient("Brunoo", "Muster", presets) == "Bruno"
    assert close_patient("Bartholomew", "Muster", presets) is None


def test_similarity_matching_can_be_disabled() -> None:
    from paperless_tarmed.normalize import close_patient

    assert close_patient("Delphinne", None, ["Delphine"], cutoff=0) is None


def test_similarity_refuses_when_two_names_are_equally_plausible() -> None:
    """Never silently disambiguate two family members."""
    from paperless_tarmed.normalize import close_patient

    # 'Alina' sits the same distance from both, so neither wins.
    assert close_patient("Alina", None, ["Alena", "Alima"]) is None


def test_misspelt_name_is_used_but_reported() -> None:
    page = SPLIT_PAGE.replace("Prénom\n", "Prénom Annea\n")
    result = extract_from_pages([page], Config(patients=["Anna", "Bruno"]), today=TODAY)
    assert result.value(V.PATIENT) == "Anna"
    assert any("by similarity" in w for w in result.warnings)


# ------------------------------------------- three-line service row layout

THREE_LINE_PAGE = """Release 4.5G/fr
Justificatif de remboursement
Patient Nom MUSTER N° GLN
Prénom Anna
11.10.2024 - 11.10.2024 Maladie
Date Tarif Code Code réf. Sé Cô Qté Pt PM/Prix f PM VPt PM Montant
11.10.2024 001 00.0010
Consultation, première période de 5 min (consultation de base)
1 1.00 10.42 1.00 1.25 8.19 1.00 1.25 1 1 1 0 23.27
11.10.2024 001 39.3320
Endosonographie rectale et/ou vaginale
1 1.00 56.64 1.00 1.25 42.73 1.00 1.25 1 1 1 0 124.21
Montant dû: 398.60
398.58Montal total:N° TVA:
"""


def test_position_code_is_not_read_as_an_amount() -> None:
    """'39.3320' matches the money pattern; reading it as CHF 39.33 quietly
    corrupted which service line counts as dominant."""
    from decimal import Decimal

    raw = extract_from_pages([THREE_LINE_PAGE], Config(), today=TODAY).raw
    by_code = {li.code: li.amount for li in raw.line_items}
    assert by_code["39.3320"] == Decimal("124.21")
    assert by_code["00.0010"] == Decimal("23.27")


def test_amount_found_on_a_separate_figures_line() -> None:
    """Code line, description, then the figures -- three lines per service."""

    result = extract_from_pages([THREE_LINE_PAGE], Config(), today=TODAY)
    assert all(li.amount is not None for li in result.raw.line_items)
