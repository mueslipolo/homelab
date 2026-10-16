"""A justificatif bundled behind the billing office's cover letter.

Shape taken from a real Release 5.0/GR/fr upload (a 'Caisse des Médecins' bill):
a cover letter, a service overview, the justificatif itself, then a QR-code
sheet. All names, numbers and addresses here are invented.

Three things went wrong on it:
- the cover letter's running text says "Le justificatif de remboursement ... ci-
  joint", which was taken for a justificatif heading, so the letter became
  justificatif #1 and was the one extracted (no patient, no table, no total);
- 'Montant de la' / 'facture :' / '113.00' wraps over three lines;
- the provider line carries no GLN, so the invoice author was used instead.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from paperless_tarmed import values as V
from paperless_tarmed.config import Config
from paperless_tarmed.locate import find_groups
from paperless_tarmed.pipeline import extract_from_pages

TODAY = date(2026, 12, 31)

COVER = """A
G D
Caisse Exemple
Société coopérative - Agence Exemple
Chemin du Test 1 - 1200 Genève
Date facture 05.10.2026
N° facture 12345 É Traitement 29.09.2026 - 29.09.2026 AUX PARENTS DE Re
Patient MUSTER ANNA MUSTER ANNA
Date de naissance : 01.01.2020 RUE DU TEST 1
Madame Marie Dupont
Ostéopathie
Veuillez trouver ci-joint les honoraires du prestataire (Madame Marie Dupont ) dont le montant dû s'élève à CHF 113.00.
Le justificatif de remboursement ou la note d'honoraires ci-joint servira à vous faire rembourser par votre caisse maladie
le montant qui vous est dû.
Monnaie Montant Monnaie
CHF 113.00 CHF
"""

OVERVIEW = """Caisse Exemple A Société coopérative - Agence Exemple
Date Facture 05.10.2026
Traitement 29.09.2026-29.09.2026
Release 5.0/SR/fr| Ps Aperçu des prestations du traitement
MUSTER ANNA 1 1234.56
Madame Marie Dupont
590 ANAMNESE, BILAN, PAR 5 MIN
590 OSTEOPATHIE, PAR 5 MIN
26.60
86.40
Montant de la facture : 113.00
"""

JUSTIFICATIF = """Justificatif de remboursement
Release 5.0/GR/fr
Envoyer à l'assurance
Document Identification 4 50 000 001 / 05.10.2026 10:07:54 / 0000AAAA 1 Page 1/1
Auteur N° GLN (B) 7600000000009 CAISSE EXEMPLE GENEVE
facture N° RCC (B) SOCIETE COOPERATIVE - 1200 GENEVE
Patient Nom MUSTER
Prénom ANNA
Rue RUE DU TEST 1
NPA 1200
Date de naissance 01.01.2020 AUX PARENTS DE
Sexe Femme / F MUSTER ANNA
Canton GE
Copie de facture Non
Loi LCA Date/N° facture 05.10.2026/450000001-12345
Traitement 29.09.2026 - 29.09.2026 Date/N° rappel
Type traitement Ambulatoire
Four. de N° GLN (P) Madame Marie Dupont
prestations N° GLN (L) Rue du Test 28 - 1200 Genève
N° RCC (P) 1234560 022 000 00 00
Diagnostic
Date Tarif Code Code réf Gr Cé Quantité Pt PM/Prix fPM VPtPM Pt PT fPT VPtPT ERM Montant
29.09.26 590 1200 1 2.00 13.30 1.00 26.60
ANAMNESE, BILAN, DIAGNOSTIQUE,CONSTATATIONS MEDICALES, PAR 5 MIN SA="noObligation'
29.09.26 590 1203 1 6.00 14.40 1.00 86.40
OSTEOPATHIE, PAR 5 MIN SA='noObligation'
N° TVA: Montant total : 113.00
Monnaie CHF
Montant de la
facture :
113.00
"""

QR_SHEET = """Justificatif de remboursement Feuille de code QR - — jee) FE
Identification 4 50 000 001 / 05.10.2026 10:07:54 / 0000AAAA1
Patient : AUX PARENTS DE ANNA MUSTER - RUE DU TEST 1 - 1200 GENEVE -
Date de naissance : 01.01.2020 - Sexe : Femme/F
QR-Code 1 QR-Code 2
"""

PAGES = [COVER, OVERVIEW, JUSTIFICATIF, QR_SHEET]


def _result():
    return extract_from_pages(PAGES, Config(), today=TODAY)


def test_cover_letter_mentioning_the_form_is_not_a_justificatif() -> None:
    groups = find_groups(PAGES)
    assert len(groups) == 1, "the cover letter must not count as a second justificatif"
    assert groups[0].page_indexes[0] == 2, "the justificatif starts on page 3"


def test_qr_sheet_joins_the_justificatif_it_belongs_to() -> None:
    (group,) = find_groups(PAGES)
    assert group.page_indexes == [2, 3]


def test_all_core_values_come_out() -> None:
    result = _result()
    assert result.core_ok, result.warnings
    assert result.value(V.PATIENT) == "Anna"
    assert result.value(V.TREATMENT_DATE) == date(2026, 9, 29)
    assert result.value(V.TOTAL) == Decimal("113.00")


def test_wrapped_montant_de_la_facture_is_read() -> None:
    from paperless_tarmed.extract import extract

    (group,) = find_groups(PAGES)
    assert extract(group).montant_facture == Decimal("113.00")


def test_service_lines_with_two_digit_years_are_parsed() -> None:
    from paperless_tarmed.extract import extract

    (group,) = find_groups(PAGES)
    items = extract(group).line_items
    assert [i.amount for i in items] == [Decimal("26.60"), Decimal("86.40")]
    assert items[1].description and items[1].description.startswith("OSTEOPATHIE")


def test_provider_without_a_gln_beats_the_invoice_author() -> None:
    """'Four. de N° GLN (P) <name>' with no number is still the provider; the
    '(B)' author line is the billing office, which must not end up in titles."""
    from paperless_tarmed.extract import extract

    (group,) = find_groups(PAGES)
    raw = extract(group)
    assert raw.provider_name and "Dupont" in raw.provider_name
    assert raw.provider_gln is None


def test_sentence_case_mention_on_a_real_form_page_still_counts() -> None:
    """Guard against over-correcting: a heading OCR'd with junk in front of it,
    or in capitals, is still a heading."""
    page = JUSTIFICATIF.replace("Justificatif de remboursement", "= JUSTIFICATIF DE REMBOURSEMENT", 1)
    assert len(find_groups([page])) == 1
