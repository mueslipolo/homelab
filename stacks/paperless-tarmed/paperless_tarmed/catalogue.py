"""Category from the official code catalogues -- no configuration, no reading
of free text.

Every service line carries a tariff type and a position code. The category is
decided from those codes alone:

1. Each line falls into a **tier**, by its code:
     service  -- a treatment (a Tarif 590 method, a TARDOC or TARMED chapter,
                 physio, psy, hospital, ...);
     supply   -- what is dispensed alongside (medicines, materials, lab);
     generic  -- the catalogues' own basic positions (TARDOC chapters AA/AR,
                 TARMED chapter 00, Tarif 590 code 1200), which every visit
                 bills whatever its nature.
2. The first tier present, in the order service > generic > supply, wins;
   within it, the **category with the largest total amount** (ties: the one
   listed first; no amounts: count lines).

So: "the specialty that billed the most; basic positions only when nothing
more specific was billed; supplies only when nothing medical was". It does not
depend on the order a billing system prints lines in, and a vaccine or a lab
test never names a doctor's visit.

Official data -- chapter titles, Tarif 590 chapters and texts -- comes from
data/catalogue.json, built from the publishers' own files by
scripts/build_catalogue.py. What is editorial is only the short label per
chapter below, written from those official titles. A code the data does not
cover falls back to the tariff type's label (tariffs.py), and `report` lists it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .models import LineItem
from .tariffs import CODES_BY_CATEGORY, category_for_code

SERVICE, SUPPLY, GENERIC = "service", "supply", "generic"
# Basic positions outrank supplies: for a GP, TARMED chapter 00 *is* the
# visit, and a lab test billed alongside must not rename it 'Labo'.
_TIER_ORDER = (SERVICE, GENERIC, SUPPLY)

# Tariff types whose lines are dispensed items, not the visit itself.
SUPPLY_TARIFFS = frozenset(
    {"400", *CODES_BY_CATEGORY["Pharma"], *CODES_BY_CATEGORY["Matériel"], *CODES_BY_CATEGORY["Labo"]}
)

TARDOC_TARIFFS = frozenset({"005", "007"})
TARMED_TARIFFS = frozenset({"001"})
TARIF_590 = "590"

# --- Official data (data/catalogue.json, built by scripts/build_catalogue.py) --


@lru_cache(maxsize=1)
def official() -> dict:
    return json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))


CATALOGUE_PATH = Path(__file__).resolve().parent / "data" / "catalogue.json"

# Tarif 590's own chapters: 1 general positions (anamnesis, travel, reports,
# supplements...), 2 the therapy methods, 3 remedies, 4 laboratory.
TARIF_590_TIERS = {1: GENERIC, 2: SERVICE, 3: SUPPLY, 4: SUPPLY}

# Tarif 590 method names longer than this get a short label for titles; the
# official name stays in `report`. A test checks every long one has a label.
LABEL_MAX = 20
TARIF_590_SHORT: dict[str, str] = {
    "1005": "Thérapie APM", "1008": "Anthroposophie", "1011": "Énergie asiatique",
    "1012": "Respiration", "1014": "Tomatis", "1022": "Fleurs de Bach",
    "1024": "Thérapie mouvement", "1025": "Tissu conjonctif", "1028": "Sels de Schüssler",
    "1029": "Biodynamique", "1032": "Hydrothérapie côlon", "1034": "Craniosacrale",
    "1036": "Autohémothérapie", "1038": "Électroacupuncture", "1047": "Eutonie",
    "1049": "Chromothérapie", "1052": "Réflexologie", "1053": "Oxydation sanguine",
    "1055": "Eurythmie", "1057": "Homéopathie", "1063": "Kneipp",
    "1064": "Acupuncture laser", "1065": "Luminothérapie", "1066": "Drainage lymphatique",
    "1067": "Magnétothérapie", "1068": "Thérapie peinture", "1070": "Métamorphique",
    "1073": "Réflexes musculaires", "1074": "Myofonctionnelle", "1077": "Auriculothérapie",
    "1079": "Bougies auriculaires", "1080": "Méthode Medau", "1082": "Orthomoléculaire",
    "1088": "Intégr. posturale", "1090": "Zones psychogènes", "1091": "Marionnettes",
    "1094": "Nouvelle naissance", "1097": "Rolfing", "1098": "Massage rythmique",
    "1099": "Oxygénothérapie", "1102": "Sophrologie", "1108": "Danse-thérapie",
    "1114": "Médecine tibétaine", "1118": "Sang irradié UV", "1120": "Vitalpratique",
    "1121": "Cataplasmes", "1122": "Équilibr. vertébrale", "1127": "Homéopathie",
    "1128": "Adveda-Eugemed", "1131": "Médiation plastique", "1132": "Thér. intermédiale",
    "1133": "Méridiens", "1144": "PäPKi", "1146": "Traitement parallèle",
    "1201": "Dramathérapie", "1202": "Art de la parole", "1205": "Détoxication",
    "1206": "Nutrition", "1207": "Diététique", "1214": "Orthonomie",
    "1222": "Thérapie sportive", "1223": "Secours émotionnels",
}

# TARDOC: the two letters that open a position code ('CG.15.0010') are its
# chapter group, the first letter its domain; catalogue.json has the official
# title of each. Domain A, "Prestations de base: médicales, paramédicales",
# is billed by every specialty whatever the visit: generic as a whole.
TARDOC_GENERIC_DOMAINS = frozenset({"A"})

# The one editorial part: a short French label per official chapter, for the
# title. Official titles are in catalogue.json; `report` shows them per code.
# A test checks every chapter in catalogue.json has a label here.
TARDOC_GROUPS: dict[str, str] = {
    "CA": "Médecin", "CG": "Pédiatrie", "EA": "Psy",
    "GA": "Imagerie", "GG": "Radiologie", "GK": "Échographie", "GM": "Scanner", "GP": "IRM",
    "JA": "Gériatrie", "JC": "Médecine du travail", "JE": "Endocrinologie",
    "JG": "Médecine hyperbare", "JK": "Infectiologie", "JM": "Pathologie",
    "JP": "Pharmacologie", "JR": "Thérapie", "JT": "Génétique", "JV": "Oncologie",
    "JX": "Médecine nucléaire", "JZ": "Psy",
    "KD": "Fertilité", "KF": "Rhumatologie", "KH": "Sommeil", "KK": "Grossesse",
    "KM": "Médecine du sport", "KP": "Médecine tropicale",
    "MA": "Angiologie", "MG": "Chirurgie vasculaire", "MK": "Dermato",
    "MP": "Neurologie", "MR": "Neurochirurgie", "PG": "Main",
    "RA": "Maxillo-facial", "RC": "Ophtalmo", "RE": "Phoniatrie", "RG": "ORL",
    "TA": "Seins", "TG": "Pneumo", "TK": "Cardio", "TM": "Chirurgie thoracique",
    "VA": "Gastro", "VC": "Chirurgie digestive", "VE": "Urologie", "VG": "Gynéco",
    "VK": "Néphrologie",
    "WA": "Anesthésie", "WF": "Médecin", "WG": "Urgences", "WK": "Soins intensifs",
}

# TARMED: the number before the first dot ('39.3000') is the main chapter.
# Short label and tier per official main chapter (titles in catalogue.json).
# 00 basic services, 01 bandages and 35 operating theatre / day clinic are
# billed whatever the visit: generic.
TARMED_GENERIC = frozenset({"00", "01", "35"})
TARMED_CHAPTERS: dict[str, str] = {
    "00": "Médecin", "01": "Médecin", "02": "Psy", "03": "Pédiatrie", "04": "Dermato",
    "05": "Neurologie", "06": "Rachis", "07": "Maxillo-facial", "08": "Ophtalmo",
    "09": "ORL", "10": "ORL", "11": "ORL", "12": "ORL", "13": "ORL",
    "15": "Pneumo", "16": "Chirurgie thoracique", "17": "Cardio",
    "18": "Chirurgie cardiaque", "19": "Gastro", "20": "Chirurgie digestive",
    "21": "Urologie", "22": "Gynéco", "23": "Seins", "24": "Orthopédie",
    "26": "Lymphologie", "27": "Transplantation", "28": "Anesthésie",
    "29": "Antalgie", "31": "Médecine nucléaire", "32": "Radio-oncologie",
    "33": "Dialyse", "34": "Soins intensifs", "35": "Hôpital", "37": "Pathologie",
    "39": "Imagerie",
}


@dataclass(frozen=True)
class LineClass:
    tier: str
    label: str | None
    known: bool  # False: the code is not in our tables yet (report lists it)
    key: str  # what was looked up, e.g. 'TARDOC CA', '590 1203', 'tariff 317'


def _tariff(item: LineItem) -> str:
    """'OO1' is how OCR reads 001: letters O in an otherwise numeric code."""
    code = item.tariff.upper()
    return code.replace("O", "0") if re.fullmatch(r"[0-9O]{3}", code) else code


def classify(item: LineItem, overrides: dict[str, str] | None = None) -> LineClass:
    tariff = _tariff(item)
    code = (item.code or "").upper()
    fallback = category_for_code(tariff, overrides)

    if tariff in SUPPLY_TARIFFS:
        return LineClass(SUPPLY, fallback, True, f"tariff {tariff}")

    if tariff in TARDOC_TARIFFS and (m := re.match(r"([A-Z]{2})\.", code)):
        group = m.group(1)
        key = f"TARDOC {group} {official()['tardoc_groups'].get(group, '?')}"
        if group[0] in TARDOC_GENERIC_DOMAINS:
            return LineClass(GENERIC, fallback, True, key)
        if group in TARDOC_GROUPS:
            return LineClass(SERVICE, TARDOC_GROUPS[group], True, key)
        return LineClass(SERVICE, fallback, False, key)

    if tariff in TARMED_TARIFFS and (m := re.match(r"(\d{2})\.", code)):
        chapter = m.group(1)
        key = f"TARMED {chapter} {official()['tarmed_chapters'].get(chapter, '?')}"
        if chapter in TARMED_CHAPTERS:
            tier = GENERIC if chapter in TARMED_GENERIC else SERVICE
            return LineClass(tier, TARMED_CHAPTERS[chapter], True, key)
        return LineClass(SERVICE, fallback, False, key)

    if tariff == TARIF_590 and (m := re.match(r"(\d{4})\b", code)):
        position = official()["tarif_590"].get(m.group(1))
        if position is None:
            return LineClass(SERVICE, fallback, False, f"590 {m.group(1)}")
        tier = TARIF_590_TIERS.get(position["chapter"], SERVICE)
        # The method's official name, without its unit: 'Ostéopathie, par
        # période de 5 min.' -> 'Ostéopathie'.
        method = re.split(r",\s*par\b", position["text_f"])[0].strip(" .")
        short = TARIF_590_SHORT.get(m.group(1)) or method
        label = short if tier == SERVICE else fallback
        return LineClass(tier, label, True, f"590 {m.group(1)} {method}")

    return LineClass(SERVICE, fallback, True, f"tariff {tariff}")


def decide(
    items: list[LineItem], overrides: dict[str, str] | None = None
) -> tuple[str | None, str | None]:
    """(category, why) -- e.g. ('Ostéopathie', '590 1203: 86.40 of 113.00')."""
    classes = [(item, classify(item, overrides)) for item in items]
    for tier in _TIER_ORDER:
        lines = [(item, c) for item, c in classes if c.tier == tier and c.label]
        if not lines:
            continue
        use_amounts = any(item.amount is not None for item, _ in lines)
        totals: dict[str, float] = {}
        keys: dict[str, str] = {}
        for item, c in lines:  # dict keeps first-seen order: ties go to the first
            weight = float(item.amount or 0) if use_amounts else 1.0
            totals[c.label] = totals.get(c.label, 0.0) + weight
            keys.setdefault(c.label, c.key)
        label = max(totals, key=lambda k: totals[k])
        unit = "CHF" if use_amounts else "line(s)"
        return label, f"{tier}: {keys[label]} ({totals[label]:.2f} {unit} of {sum(totals.values()):.2f})"
    return None, None


def render_summary(results) -> str:
    """For `report`: which categories came out, and the codes our tables miss."""
    from collections import Counter

    categories: Counter = Counter()
    missing: dict[str, list] = {}
    uncategorised: list = []
    for result in results:
        raw = result.raw
        if raw is None:
            continue
        category = decide(raw.line_items)[0]
        categories[category or "(none)"] += 1
        if category is None:
            uncategorised.append((result.doc_id, len(raw.line_items)))
        for item in raw.line_items:
            c = classify(item)
            if not c.known:
                docs, sample = missing.get(c.key, (set(), item.description or ""))
                docs.add(result.doc_id)
                missing[c.key] = (docs, sample)

    out = ["", "=" * 74, " CATEGORIES", "=" * 74, ""]
    out += [f"  {count:>4}  {label}" for label, count in categories.most_common()]
    if uncategorised:
        # Almost always: no service line could be read off the table.
        out += ["", f"  (none) = {len(uncategorised)} doc(s); service lines read per doc:"]
        out += ["  " + ", ".join(f"#{d} ({n})" for d, n in uncategorised)]
    out += ["", "=" * 74, f" CODES NOT IN THE CATALOGUE TABLES ({len(missing)}) -- add to catalogue.py", "=" * 74, ""]
    if not missing:
        out.append("  (none)")
    for key, (docs, sample) in sorted(missing.items(), key=lambda kv: -len(kv[1][0])):
        out.append(f"  {key:<12} {len(docs):>3} doc(s)   e.g. {sample[:50]}")
    return "\n".join(out) + "\n"
