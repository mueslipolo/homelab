"""Turning what the document says into what we want to store."""

from __future__ import annotations

import difflib
import re
import unicodedata

# Words that carry no identity and should not become part of an acronym.
PARTICLES = {
    "de", "des", "du", "da", "la", "le", "les", "et", "aux", "au", "a",
    "of", "the", "sa", "sarl", "ag", "gmbh", "cie", "co", "snc", "ltd", "llc",
}
#: Legal forms: mark an organisation, never part of its short name.
LEGAL_FORMS = {"sa", "sarl", "ag", "gmbh", "sas", "snc", "ltd", "llc", "cie", "co"}
#: Institution words: mark an organisation; skipped when picking its name word.
INSTITUTION_WORDS = {
    "centre", "center", "zentrum", "clinique", "klinik", "hopital", "hopitaux",
    "spital", "institut", "laboratoire", "labor", "cabinet", "praxis",
    "permanence", "fondation", "stiftung", "groupe", "groupement",
}
HONORIFIC_RE = re.compile(
    r"^(?:M\.|Mme|Mlle|Dr\.?|Dre\.?|Drs\.?|Docteur|Doctoresse|Doktor|Prof\.?|PD"
    r"|med\.?|Herr|Frau|Monsieur|Madame)$",
    re.IGNORECASE,
)


def fold(value: str) -> str:
    """Casefold and strip accents, so 'MÜSTER' == 'Muster' and 'Carla' == 'Cárla'."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", stripped.casefold())


def _name_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    cleaned = re.sub(r"\([^)]*\)", " ", value)
    parts = (p.strip("().,;:'\u2019\"-") for p in re.split(r"[\s,;/]+", cleaned))
    return [p for p in parts if p and not HONORIFIC_RE.match(p)]


def match_patient(
    prenom: str | None,
    nom: str | None,
    presets: list[str],
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Map the document's patient name onto one of the configured names.

    Deliberately forgiving, because one person shows up as 'Anna', as
    'MUSTER'/'MÜSTER'/'Muster', and as 'BRUNO,JEAN' across real documents. Matching
    is done on folded tokens, so accents, case and punctuation never matter.

    Returns None when nothing matches -- the caller must not guess.
    """
    aliases = aliases or {}
    by_folded = {fold(p): p for p in presets}
    for alias, target in aliases.items():
        if target in presets:
            by_folded.setdefault(fold(alias), target)

    # Given names first: that is what identifies the person within one family.
    for token in _name_tokens(prenom):
        if hit := by_folded.get(fold(token)):
            return hit
    # A preset written as a full name ('Anna Muster') still resolves.
    joined = fold(f"{prenom or ''}{nom or ''}")
    if joined and (hit := by_folded.get(joined)):
        return hit
    for token in _name_tokens(nom):
        if hit := by_folded.get(fold(token)):
            return hit
    return None


def titlecase_name(value: str) -> str:
    """Short acronyms survive ('HRN'); longer shouted words are calmed down.

    'RADIOTEST' -> 'Radiotest', 'BRUNO' -> 'Bruno', but 'HRN' stays 'HRN'.
    """
    return value.title() if value.isupper() and len(value) > 3 else value


def close_patient(
    prenom: str | None,
    nom: str | None,
    presets: list[str],
    aliases: dict[str, str] | None = None,
    cutoff: float = 0.85,
) -> str | None:
    """Near-match a misspelt name against the configured list.

    Providers mistype names ('Delphinne' for 'Delphine'), and an exact match
    then fails on a document that is otherwise perfectly readable. The candidate
    set is tiny, so a high similarity threshold is safe -- but only when a single
    name stands out, or one clearly beats the runner-up. Two plausible family
    members must never be silently disambiguated.

    `cutoff` of 0 disables this entirely.
    """
    if cutoff <= 0 or not presets:
        return None

    candidates = {fold(p): p for p in presets}
    for alias, target in (aliases or {}).items():
        if target in presets:
            candidates.setdefault(fold(alias), target)

    scores: dict[str, float] = {}
    for token in (*_name_tokens(prenom), *_name_tokens(nom)):
        folded = fold(token)
        if not folded:
            continue
        for key, name in candidates.items():
            ratio = difflib.SequenceMatcher(None, folded, key).ratio()
            if ratio >= cutoff:
                scores[name] = max(scores.get(name, 0.0), ratio)

    if len(scores) == 1:
        return next(iter(scores))
    if len(scores) > 1:
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        # Require a clear winner; otherwise refuse rather than pick.
        if ranked[0][1] - ranked[1][1] >= 0.08:
            return ranked[0][0]
    return None


def shorten_provider(
    name: str | None,
    gln: str | None = None,
    overrides: dict[str, str] | None = None,
    maxlen: int = 16,
) -> str | None:
    """A short provider name for titles, by one fixed rule -- no list per practitioner.

        Clinique Rive Gauche (CRG)         -> CRG        acronym in parentheses
        Hôpitaux universitaires de Genève  -> HUG        organisation, 3+ words: initials
        Viollier Weintraub SA              -> Viollier   organisation: first non-institution word
        Laboratoire Dr Meier               -> Meier
        Docteur Lucien MUSTERMANN          -> Mustermann person: titles dropped, surname kept

    An organisation is a name with a legal form (SA, SARL, AG, GmbH...) or an
    institution word (Hôpital, Clinique, Centre, Laboratoire...). Anything
    else is a person. PAPERLESS_TARMED_GLN_OVERRIDES still pins a name by GLN.
    """
    overrides = overrides or {}
    if gln and (pinned := overrides.get(gln)):
        return pinned
    if not name:
        return None
    if m := re.search(r"\(([A-Z]{2,6})\)", name):
        return m.group(1)

    # Drop parentheticals -- including an unclosed one, which real documents
    # produce when the line is truncated: 'Vision Neuville SA (Dr Von Muster'.
    cleaned = re.sub(r"\([^)]*\)", " ", name).split("(")[0]
    cleaned = re.sub(r"[,;].*$", "", cleaned)
    tokens = [re.sub(r"^[lLdD]['\u2019]", "", t) for t in _name_tokens(cleaned)]
    folded = [fold(t) for t in tokens]
    organisation = any(f in LEGAL_FORMS or f in INSTITUTION_WORDS for f in folded)
    words = [
        t for t, f in zip(tokens, folded, strict=True)
        if t[:1].isalpha() and f not in PARTICLES and f not in LEGAL_FORMS
    ]
    if not words:
        return None

    if organisation and len(words) >= 3:
        return "".join(w[0] for w in words).upper()
    if organisation:
        named = [w for w in words if fold(w) not in INSTITUTION_WORDS] or words
        short = named[0]
    else:
        short = words[-1]
    return titlecase_name(short.strip(" .,-")[:maxlen]) or None