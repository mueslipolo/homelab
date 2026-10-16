"""Category from the official code catalogues (catalogue.py).

Rule: the highest tier present (service > supply > generic) wins; within it,
the category with the largest total. All amounts here are invented; the codes
are real catalogue positions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from paperless_tarmed.catalogue import GENERIC, SERVICE, SUPPLY, classify, decide, render_summary
from paperless_tarmed.models import Extraction, LineItem, RawJustificatif

D = date(2026, 9, 17)


def _line(tariff: str, code: str | None, amount: str | None, description: str = "") -> LineItem:
    return LineItem(
        date=D, tariff=tariff, code=code, description=description,
        amount=Decimal(amount) if amount else None,
    )


# --- tiers ----------------------------------------------------------------


def test_catalogue_basic_positions_are_generic() -> None:
    assert classify(_line("007", "AA.05.0010", "24.15")).tier == GENERIC
    assert classify(_line("007", "AR.05.005", "19.02")).tier == GENERIC
    assert classify(_line("001", "00.0010", "23.27")).tier == GENERIC
    assert classify(_line("590", "1200", "26.60")).tier == GENERIC


def test_medicines_materials_and_lab_are_supplies() -> None:
    for tariff in ("402", "403", "452", "317"):
        assert classify(_line(tariff, "7680000000028", "77.95")).tier == SUPPLY


def test_chapters_and_methods_are_services_with_their_label() -> None:
    assert classify(_line("007", "CA.00.0010", "18.05")).label == "Médecin"
    assert classify(_line("007", "CG.15.0010", "12.99")).label == "Pédiatrie"
    assert classify(_line("007", "GK.20.004", "65.07")).label == "Échographie"
    assert classify(_line("001", "39.3000", "85.00")).label == "Imagerie"
    assert classify(_line("590", "1203", "86.40")).label == "Ostéopathie"


def test_other_tariff_types_use_the_tariff_label() -> None:
    assert classify(_line("311", "7301", "48.00")).label == "Physio"
    assert classify(_line("940", None, "300.00")).label == "Hôpital"


def test_ocr_letter_o_in_a_tariff_code_is_read_as_zero() -> None:
    assert classify(_line("OO1", "39.3000", "85.00")).label == "Imagerie"


def test_unknown_chapter_falls_back_to_the_tariff_label_and_is_flagged() -> None:
    c = classify(_line("007", "ZZ.00.0010", "50.00"))
    assert (c.tier, c.label, c.known, c.key) == (SERVICE, "Médecin", False, "TARDOC ZZ ?")


# --- the decision ------------------------------------------------------------


def test_vaccination_visit_is_the_doctor_never_the_vaccine() -> None:
    items = [
        _line("007", "CA.00.0010", "18.05"),
        _line("007", "CA.00.0020", "18.05"),
        _line("007", "CG.15.0010", "12.99"),
        _line("007", "AA.05.0010", "24.15"),
        _line("402", "7680000000028", "77.95"),
        _line("402", "7680000000011", "69.25"),
    ]
    assert decide(items)[0] == "Médecin"


def test_largest_total_wins_not_the_first_line() -> None:
    items = [_line("007", "CA.00.0010", "18.05"), _line("007", "GK.20.004", "65.07")]
    assert decide(items)[0] == "Échographie"


def test_osteopathy_ignores_the_generic_intake() -> None:
    items = [_line("590", "1200", "26.60"), _line("590", "1203", "86.40")]
    category, why = decide(items)
    assert category == "Ostéopathie"
    assert why.startswith("service: 590 1203")


def test_supplies_name_the_bill_only_when_nothing_medical_was_billed() -> None:
    assert decide([_line("317", "1356.00", "12.00")])[0] == "Labo"
    assert decide([_line("007", "CA.00.0010", "18.05"), _line("317", "1356.00", "90.00")])[0] == "Médecin"


def test_a_gp_visit_billed_with_basic_positions_beats_its_lab_test() -> None:
    """TARMED chapter 00 is the whole visit for a GP; the lab line must not
    rename it 'Labo' even when it is the only non-basic line."""
    items = [_line("001", "00.0010", "17.39"), _line("001", "00.0030", "36.20"),
             _line("317", "1356.00", "8.94")]
    assert decide(items)[0] == "Médecin"


def test_generic_only_bill_still_gets_a_category() -> None:
    assert decide([_line("007", "AA.10.0010", "24.00")])[0] == "Médecin"


def test_tie_goes_to_the_first_listed() -> None:
    items = [_line("007", "CG.00.0010", "20.00"), _line("007", "CA.00.0010", "20.00")]
    assert decide(items)[0] == "Pédiatrie"


def test_without_amounts_lines_are_counted() -> None:
    items = [_line("007", "CA.00.0010", None), _line("007", "CG.00.0010", None),
             _line("007", "CG.00.0020", None)]
    assert decide(items)[0] == "Pédiatrie"


def test_nothing_known_gives_no_category() -> None:
    assert decide([]) == (None, None)
    assert decide([_line("999", None, "10.00")]) == (None, None)


def test_report_lists_categories_and_missing_codes() -> None:
    raw = RawJustificatif(line_items=[_line("007", "ZZ.00.0010", "50.00", "Prestation inconnue")])
    out = render_summary([Extraction(doc_id=1, raw=raw)])
    assert "Médecin" in out
    assert "TARDOC ZZ" in out and "Prestation inconnue" in out


def test_tardoc_domain_a_is_generic_as_a_whole() -> None:
    """AK (injections by paramedical staff), AM... are general services like AA."""
    for code in ("AK.00.0010", "AM.00.0010"):
        assert classify(_line("007", code, "10.00")).tier == GENERIC


def test_tarif_590_travel_compensation_never_names_a_visit() -> None:
    items = [_line("590", "1256", "40.00"), _line("590", "1062", "30.00")]
    assert decide(items)[0] == "Massage classique"


def test_tarif_590_comes_from_the_official_list() -> None:
    """Tier from the official chapter, label from the official text."""
    assert classify(_line("590", "1310", "20.00")).tier == SUPPLY  # chapter 3, remedies
    assert classify(_line("590", "1302", "20.00")).tier == SUPPLY  # chapter 4, laboratory
    assert classify(_line("590", "1251", "20.00")).tier == GENERIC  # chapter 1
    assert "zones réflexes du pied" in classify(_line("590", "1052", "20.00")).key  # official name kept


def test_a_tarmed_chapter_outside_the_official_list_falls_back_and_is_reported() -> None:
    c = classify(_line("001", "14.0010", "40.00"))  # TARMED 1.09 has no chapter 14
    assert (c.label, c.known) == ("Médecin", False)


def test_tarmed_chapters_carry_their_official_meaning() -> None:
    assert classify(_line("001", "08.0110", "40.00")).label == "Ophtalmo"  # Auge
    assert classify(_line("001", "20.2210", "40.00")).label == "Chirurgie digestive"
    assert classify(_line("001", "35.0010", "40.00")).tier == GENERIC  # OP, Tagesklinik


def test_newly_tabled_codes() -> None:
    assert classify(_line("590", "1004", "60.00")).label == "Acupuncture"
    assert classify(_line("007", "RC.00.0010", "40.00")).label == "Ophtalmo"
    assert classify(_line("007", "VG.00.0010", "40.00")).label == "Gynéco"


def test_every_official_chapter_has_a_label() -> None:
    """The label tables must cover the official catalogue, so a rebuilt
    catalogue.json with a new chapter fails here instead of in a title."""
    from paperless_tarmed.catalogue import (
        TARDOC_GENERIC_DOMAINS,
        TARDOC_GROUPS,
        TARMED_CHAPTERS,
        official,
    )

    groups = [g for g in official()["tardoc_groups"] if len(g) == 2]
    assert [g for g in groups if g[0] not in TARDOC_GENERIC_DOMAINS and g not in TARDOC_GROUPS] == []
    chapters = [c for c in official()["tarmed_chapters"] if len(c) == 2]
    assert [c for c in chapters if c not in TARMED_CHAPTERS] == []


def test_official_titles_appear_in_the_explanation() -> None:
    assert "Oeil" in classify(_line("007", "RC.00.0010", "40.00")).key
    assert "Auge" in classify(_line("001", "08.0110", "40.00")).key


def test_report_lists_documents_without_a_category() -> None:
    results = [Extraction(doc_id=7, raw=RawJustificatif(line_items=[]))]
    assert "#7 (0)" in render_summary(results)


def test_every_long_590_method_has_a_short_label() -> None:
    import re

    from paperless_tarmed.catalogue import LABEL_MAX, TARIF_590_SHORT, official

    methods = {
        code: re.split(r",\s*par\b", p["text_f"])[0].strip(" .")
        for code, p in official()["tarif_590"].items() if p["chapter"] == 2
    }
    assert [c for c, name in methods.items() if len(name) > LABEL_MAX and c not in TARIF_590_SHORT] == []
    assert [s for s in TARIF_590_SHORT.values() if len(s) > LABEL_MAX] == []


def test_short_590_labels_are_used_in_titles() -> None:
    assert classify(_line("590", "1052", "20.00")).label == "Réflexologie"
    assert classify(_line("590", "1206", "20.00")).label == "Nutrition"
    assert classify(_line("590", "1062", "20.00")).label == "Massage classique"  # short already
