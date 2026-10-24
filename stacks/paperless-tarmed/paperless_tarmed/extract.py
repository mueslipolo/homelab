"""Reading fields off a justificatif.

Three label dialects are handled (the samples seen so far):

  Release 4.5G/fr   'Traitement 15.03.2025 - 20.05.2025'    'Montant de la facture: 62.55'
  Release 4.0 H     'Traitement du 15.03.2025 au 20.05.2025'  'A payer CHF 62.55'
  Release 5.0       two-digit years, 'Montant dû: 81.60', wrapped labels

Everything here is label-anchored. Nothing depends on coordinates or column
positions, because OCR shifts those around.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .locate import JustificatifGroup
from .models import LineItem, RawJustificatif

DATE_RE = r"(\d{2})[./](\d{2})[./](\d{4}|\d{2})\b"
_DATE = re.compile(DATE_RE)

# Swiss money: 62.55, 1'234.55, 1 234.55, 62,55
MONEY = r"\d{1,3}(?:['’]\d{3})*[.,]\d{2}|\d+[.,]\d{2}"
_MONEY_TOKEN = re.compile(MONEY)

NOM_RE = re.compile(r"Patient[^\S\n]+Nom[^\S\n]+(\S.*)")
PRENOM_RE = re.compile(r"Pr[ée]nom[^\S\n]+(\S.*)")
# Trailing column header that shares the 'Nom' line: 'Patient Nom MUSTER N° GLN'
NOM_TAIL_RE = re.compile(r"\s+N[°ºo]\s*(?:GLN|EAN).*$", re.IGNORECASE)

TRAITEMENT_RE = re.compile(
    r"Traitement\s+(?:du\s+)?" + DATE_RE + r"\s*(?:-|–|au)\s*" + DATE_RE,
    re.IGNORECASE,
)

LINE_ITEM_RE = re.compile(r"(?m)^[ \t]*" + DATE_RE + r"[ \t]+([0-9A-Z]{3})\b(.*)$")

H = r"[^\S\n]"  # horizontal whitespace only: an amount never wraps to a label

MONTANT_FACTURE_RE = re.compile(
    # 'Montant de la facture: 120.00', and the wrapped Release 5.0 form where
    # the amount lands between 'la' and 'facture'.
    rf"Montant{H}+de{H}+la(?:{H}+facture)?{H}*:?{H}*(?:CHF{H}*)?(" + MONEY + r")"
    # Release 5.0/GR wraps the label itself over three lines:
    #   'Montant de la' / 'facture :' / '113.00'
    # Only this exact shape, and only when the amount is the whole next line,
    # so an unrelated figure further down can never be picked up.
    rf"|Montant{H}+de{H}+la{H}*\n{H}*facture{H}*:?{H}*\n{H}*(?:CHF{H}*)?(" + MONEY + rf"){H}*$",
    re.IGNORECASE | re.MULTILINE,
)

# The payable total goes by a different name in each dialect:
#   4.5G  'Montant de la facture: 120.00'
#   4.0 H 'A payer CHF 388.80'
#   5.0   'Montant dû: 81.60'
A_PAYER_RE = re.compile(
    rf"(?:A{H}+payer|Montant{H}+d[ûuū]|Zu{H}+bezahlen|Zahlbar){H}*:?{H}*(?:CHF{H}*)?("
    + MONEY
    + r")",
    re.IGNORECASE,
)

# 'Montant total: 342.44', but OCR also produces the label misspelt with the
# amount jammed on in front: '301.54Montal total:'. 'Mont\w*' absorbs the
# misspelling. Both forms are anchored to one line -- allowing a newline let the
# reversed alternative pick up an unrelated '0.00' from the VAT table above.
MONTANT_TOTAL_RE = re.compile(
    rf"Mont\w*{H}+total{H}*:?{H}*(?:CHF{H}*)?(" + MONEY + r")"
    r"|(" + MONEY + rf"){H}{{0,1}}Mont\w*{H}+total",
    re.IGNORECASE,
)

# 'Indemnité de rappel: 5.00' -- a late-payment fee the insurer will not refund.
REMINDER_FEE_RE = re.compile(
    r"(?:Indemnit[ée]\s+de\s+rappel|Mahngeb[üu]hr|Frais\s+de\s+rappel)\s*:?\s*(" + MONEY + r")",
    re.IGNORECASE,
)

# 'Monnaie: CHF' / 'Währung: CHF'
CURRENCY_RE = re.compile(r"(?:Monnaie|W[äa]hrung|Valuta|Currency)\s*:?\s*([A-Z]{3})")

PROVIDER_RE = re.compile(
    r"N[°ºo]\s*(?:GLN|EAN)\s*\(?\s*([BP])?\s*\)?\s*(\d{13})\s+(.+)", re.IGNORECASE
)
# Release 5.0/GR can print the provider's '(P)' line with no GLN at all:
# 'Four. de N° GLN (P) Monsieur Jean Exemple'. Without this the '(B)' author --
# the billing office -- became the provider and ended up in titles.
PROVIDER_NO_GLN_RE = re.compile(
    r"N[°ºo]\s*(?:GLN|EAN)\s*\(\s*P\s*\)\s+([^\W\d].+)", re.IGNORECASE
)
# Where the provider's name stops and their contact details begin.
PROVIDER_TAIL_RE = re.compile(
    r"\s{2,}"
    r"|\s+(?:T[ée]l|Tel|Fax|E-?mail|Natel)\s*[.:]"
    r"|\s+(?:Rue|Route|Quai|Av\.|Avenue|Chemin|Case|Place|Bd|Boulevard|Strasse|Weg)\s"
    r"|\s+\d{4}\s+\w",
    re.IGNORECASE,
)


def parse_date(day: str, month: str, year: str) -> date | None:
    """Parse a form date, accepting the two-digit year Release 5.0 uses.

    'YY' is read as 20YY: these are recent medical bills, and the plausibility
    checks in confidence.py catch anything that lands somewhere absurd.
    """
    try:
        value = int(year)
        return date(2000 + value if len(year) == 2 else value, int(month), int(day))
    except ValueError:
        return None


def parse_money(raw: str) -> Decimal | None:
    cleaned = re.sub(r"['’]", "", raw).replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _first_money(pattern: re.Pattern[str], text: str) -> Decimal | None:
    """First amount the pattern finds, from whichever alternative matched."""
    m = pattern.search(text)
    if not m:
        return None
    for group in m.groups():
        if group:
            return parse_money(group)
    return None


def _provider(text: str) -> tuple[str | None, str | None]:
    """Pick the *fournisseur de prestations* rather than the invoice author.

    They are usually the same party, but when they differ the provider is the one
    that actually gave the treatment. 4.5G marks the roles '(B)' author and '(P)'
    provider; 4.0 H marks neither, so we fall back to the 'Four.' label and then
    to the first match.
    """
    candidates = []
    for line in text.split("\n"):
        m = PROVIDER_RE.search(line)
        if not m:
            if bare_p := PROVIDER_NO_GLN_RE.search(line):
                name = PROVIDER_TAIL_RE.split(bare_p.group(1).strip(), maxsplit=1)[0].strip(" .,;:-")
                is_four = bool(re.search(r"Four\.?\s|Fournisseur|prestations", line, re.I))
                if name:
                    candidates.append((is_four, True, None, name))
            continue
        role, gln, rest = m.groups()
        name = PROVIDER_TAIL_RE.split(rest.strip(), maxsplit=1)[0].strip(" .,;:-")
        if not name:
            continue
        is_four = bool(re.search(r"Four\.?\s|Fournisseur|prestations", line, re.I))
        candidates.append((is_four, (role or "").upper() == "P", gln, name))

    if not candidates:
        # Fallback: a bare '<13-digit GLN> <name>' line, the label having been
        # emitted separately by the text extractor.
        for line in text.split("\n"):
            bare = re.match(r"\s*(\d{13})\s+(\S.*)", line)
            if not bare:
                continue
            name = PROVIDER_TAIL_RE.split(bare.group(2).strip(), maxsplit=1)[0].strip(" .,;:-")
            if name and not name[0].isdigit():
                return name, bare.group(1)
        return None, None
    for want_four, want_p in ((True, True), (True, False), (False, True)):
        for is_four, is_p, gln, name in candidates:
            if is_four == want_four and is_p == want_p:
                return name, gln
    _, _, gln, name = candidates[0]
    return name, gln


# The service table's column header. Everything above it is the form's metadata
# block, never a service description.
TABLE_HEADER_RE = re.compile(r"^\s*Date\s+Tarif\b|^\s*Date\b.*\bTarif\b.*\bMontant\b", re.IGNORECASE)

# Rows that are structure, not a service description.
_NOT_DESCRIPTION_RE = re.compile(
    r"Montant\s+(total|de\s+la\s+facture)|Code\s+Taux|N[°ºo]\s*TVA|A\s+payer"
    r"|^\s*Date\s+Tarif|Monnaie|dont\s+pr\.|Acompte|^\s*Code\s",
    re.IGNORECASE,
)

# The form's own field labels. Only meaningful above the service table: below
# it, a description may legitimately begin with one of these words.
_METADATA_LABEL_RE = re.compile(
    r"^\s*(Patient|Pr[ée]nom|Nom|Rue|NPA|Localit[ée]|Sexe|Canton|Copie|Loi"
    r"|Traitement|Type\s+(de\s+)?(remb|traitement)|Motif|Diagnostic|Mandataire"
    r"|Liste\s+GLN|Commentaire|Document|Auteur|Four\.|prestations|facture"
    r"|Date\s+(de\s+naissance|cas)|N[°ºo]\s|Admis|R[ôo]le)\b",
    re.IGNORECASE,
)

# A row of nothing but figures: quantities, point values and the amount. Some
# layouts put these on their own line, below the code and its description.
NUMERIC_ROW_RE = re.compile(r"^[\s\d.,'\u2019]+$")


def _row_amount(rest: str, lines: list[str], index: int) -> Decimal | None:
    """The amount for a service row, wherever the layout happens to put it.

    The position code must be removed first: '39.3320' matches the money pattern
    and would otherwise be read as CHF 39.33, silently corrupting which line
    counts as dominant.
    """
    without_code = rest.split(maxsplit=1)
    tail = without_code[1] if len(without_code) > 1 else ""
    if tokens := _MONEY_TOKEN.findall(tail):
        return parse_money(tokens[-1])

    # Three-line layout: code line, description, then the figures.
    for offset in range(1, 4):
        j = index + offset
        if j >= len(lines):
            break
        candidate = lines[j]
        if LINE_ITEM_RE.match(candidate):
            break
        if NUMERIC_ROW_RE.match(candidate.strip()) and (
            tokens := _MONEY_TOKEN.findall(candidate)
        ):
            return parse_money(tokens[-1])
    return None


def _is_description(line: str, strict: bool = True) -> bool:
    """A wrapped service description, rather than a table row or a total.

    `strict` also rejects the form's field labels. It is only needed when the
    table header could not be found, since otherwise the search is already
    confined to lines below it.
    """
    stripped = line.strip()
    if len(stripped) < 8 or LINE_ITEM_RE.match(line) or _NOT_DESCRIPTION_RE.search(stripped):
        return False
    if strict and _METADATA_LABEL_RE.match(stripped):
        return False
    return sum(c.isalpha() for c in stripped) >= 5


def _collect(
    lines: list[str],
    start: int,
    step: int,
    limit: int = 2,
    floor: int = 0,
    strict: bool = True,
) -> str | None:
    """Gather up to `limit` consecutive description lines walking in one direction.

    `floor` keeps the walk inside the service table; without it, a row at the top
    of the table collects the form's metadata instead and yields nonsense
    categories like 'Prénom'.
    """
    found: list[str] = []
    i = start
    while floor <= i < len(lines) and len(found) < limit and _is_description(lines[i], strict):
        found.append(lines[i].strip())
        i += step
    if not found:
        return None
    if step < 0:
        found.reverse()
    return " ".join(found)


def _line_items(text: str) -> list[LineItem]:
    """Parse the service table, attaching each row's description.

    The two dialects put the description on opposite sides of the code row --
    4.5G below it, 4.0 H above it -- so the orientation is detected from the
    document rather than assumed. Guessing wrong silently attaches every
    description to the neighbouring service.
    """
    lines = text.split("\n")
    # Descriptions live only inside the table, below its column header.
    header_idx = next((i for i, line in enumerate(lines) if TABLE_HEADER_RE.match(line)), None)
    floor = header_idx + 1 if header_idx is not None else 0

    rows: list[tuple[int, LineItem]] = []
    for i, line in enumerate(lines):
        m = LINE_ITEM_RE.match(line)
        if not m:
            continue
        day = parse_date(m.group(1), m.group(2), m.group(3))
        if not day:
            continue
        rest = m.group(5)
        code = rest.split()[0] if rest.split() else None
        rows.append(
            (
                i,
                LineItem(
                    date=day,
                    tariff=m.group(4),
                    amount=_row_amount(rest, lines, i),
                    code=code,
                ),
            )
        )

    if not rows:
        return []

    # Counting descriptions on each side cannot separate the dialects: rows and
    # descriptions alternate, so both sides score almost identically (on a real
    # 4.0 H sample it was 7 after vs 6 before, which points the wrong way).
    # The structural signal is what sits immediately above the *first* row: the
    # column header in the 4.5G layout, but already a description in 4.0 H.
    first = rows[0][0]
    strict = header_idx is None
    if header_idx is not None:
        # 4.5G puts the first row straight under the header; 4.0 H puts that
        # row's description in between. Judge by what is in between, not by
        # the distance: OCR leaves stray fragments there ('0325', blank lines),
        # and counting those as a description attached every description to
        # the wrong row.
        between = lines[header_idx + 1 : first]
        step = -1 if any(_is_description(line, strict=False) for line in between) else 1
    elif first > 0:
        step = -1 if _is_description(lines[first - 1]) else 1
    else:
        after = sum(1 for i, _ in rows if i + 1 < len(lines) and _is_description(lines[i + 1]))
        before = sum(1 for i, _ in rows if i - 1 >= 0 and _is_description(lines[i - 1]))
        step = -1 if before > after else 1

    out = []
    for i, item in rows:
        description = _collect(lines, i + step, step, floor=floor, strict=strict)
        out.append(
            LineItem(
                date=item.date,
                tariff=item.tariff,
                amount=item.amount,
                code=item.code,
                description=description,
            )
        )
    return out


def extract(group: JustificatifGroup) -> RawJustificatif:
    text = group.text
    raw = RawJustificatif(
        page_indexes=list(group.page_indexes),
        release=group.release,
    )

    if m := NOM_RE.search(text):
        raw.nom = NOM_TAIL_RE.sub("", m.group(1)).strip() or None
    if m := PRENOM_RE.search(text):
        # 'BRUNO,JEAN' -> first given name; a wide gap means another column.
        raw.prenom = re.split(r"\s{2,}|,", m.group(1).strip())[0].strip() or None

    for m in TRAITEMENT_RE.finditer(text):
        for grp in (m.group(1, 2, 3), m.group(4, 5, 6)):
            if d := parse_date(*grp):
                raw.header_dates.append(d)

    raw.line_items = _line_items(text)

    raw.montant_facture = _first_money(MONTANT_FACTURE_RE, text) or _first_money(
        A_PAYER_RE, text
    )
    raw.montant_total = _first_money(MONTANT_TOTAL_RE, text)
    raw.text = text
    raw.provider_name, raw.provider_gln = _provider(text)
    if m := CURRENCY_RE.search(text):
        raw.currency = m.group(1)
    if m := REMINDER_FEE_RE.search(text):
        raw.reminder_fee = parse_money(m.group(1))
    return raw
