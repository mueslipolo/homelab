"""A visit whose most expensive lines are medicines.

Shape taken from a real Release 5.0/GR/fr paediatric vaccination visit (TARDOC
007 consultation lines plus three 402 vaccine lines). All names, numbers and
addresses here are invented.

The category once came from the most expensive line's description, and a
vaccine was the most expensive -- so its brand name ('VAXNEUVANCE susp inj ser
pré') became the title's category. Categories now come from identifiers only
(providers.py); this layout stays as an end-to-end check.
"""

from __future__ import annotations

from datetime import date

from paperless_tarmed import values as V
from paperless_tarmed.config import Config
from paperless_tarmed.pipeline import extract_from_pages

TODAY = date(2026, 12, 31)

PAGE = """. . Release 5.0/GR/r este
Justificatif de remboursement Enoe a tessurance] FRESE
Document Identification 4 00 000 002 / 24.09.2026 01:20:13 / 0000BBBB Page 1/1
Auteur N° GLN (B) 7600000000009 CAISSE EXEMPLE GENEVE
facture N° RCC (B) SOCIETE COOPERATIVE - 1200 GENEVE
Patient Nom MUSTER
Prénom ANNA
Rue RUE DU TEST 1
Date de naissance 01.01.2026 MONSIEUR
Sexe Femme / F MUSTER PAUL
Loi LAMal Date/N° facture 24.09.2026/400000002-10000
Traitement 17.09.2026 - 17.09.2026 Date/N° rappel
Rôle/Localité Médecin / Cabinet médical
Four. de N° GLN (P) 7600000000016 Dr Marie Exemple-Dupont
prestations N° GLN (L) 7600000000016 Rue du Test 9 - 1200 Genève

Date Tarif Code Code réf. Gr Cô Quantité Pt PM/Prix FPM VPtPM PLPT fPT VPtPT ERM Montant
0325

17.09.26 007 CA.00.0010 it 1.00 10.56 0.94 8.64 094 110 18.05
Consultation du médecin de famille, 5 premiéres min

17.09.26 007 CG.15.0010 1 1.00 13.82 094 110 12.99
Supplément pour prestations chez les enfants jusqu'à 12 ans

17.09.26 007 AA.05.0010 i 1.00 12.68 0.94 13.01 094 110 24.15
Examen: système circulatoire

17.09.26 007 AA.00.0090 1 5.00 2.11 0.94 1.73 0.94 110 18.05
Conseils spécifiques en matiére de vaccination exempte de franchise, par période de 1 min

17.09.26 402 7680000000011 1 1.00 69.25 1.00 112 69.25

PRODUITA susp inj av 2 aiguilles ser pré 0.5 mi

17.09.26 402 7680000000028 1 1.00 77.95 1.00 112 77.95
PRODUITB susp inj ser pré 0.5 mi

17.09.26 402 7680000000035 1 1.00 §9.20 1.00 172 59.20
PRODUITC liquid susp orale tb 1.5 mi


Code Taux Montant TVA N° TVA CHE-000.000.000 TVA Montant total : 279.64
0 0.00 73.24 0.00 Monnaie: CHF
2 2.60 206.40 5.23
Montant de la
facture : 27965"""


def test_end_to_end_title_uses_the_first_labelled_tariff() -> None:
    """No practitioner listed: the first tariff type on the bill (007, a doctor)
    names the category -- never a vaccine's brand name from a 402 line."""
    cfg = Config(title_template="{patient} - {category} - {treatment_date} - {provider}")
    result = extract_from_pages([PAGE], cfg, today=TODAY)
    assert result.value(V.PATIENT) == "Anna"
    assert result.value(V.TREATMENT_DATE) == date(2026, 9, 17)
    assert result.get("category").value == "Médecin"
    assert result.title.value.startswith("Anna - Médecin - 2026-09-17")
