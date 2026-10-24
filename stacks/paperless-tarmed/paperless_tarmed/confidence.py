"""Deciding what may be written.

This is medical and financial data, so the rule is: a value that fails a check is
never written. The field is left alone and the document is tagged, which makes it
visible during review. Guessing is worse than a gap.

Only the values in `values.CORE` are gated this way. The descriptive ones
(category, provider, GLN) are published when found and simply absent otherwise.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import values as V
from .catalogue import decide
from .config import Config
from .models import Extraction, FieldOutcome, RawJustificatif
from .normalize import close_patient, fold, match_patient, shorten_provider, titlecase_name

WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _patient_from_page(raw: RawJustificatif, cfg: Config) -> str | None:
    """Last resort: find a configured name anywhere on the justificatif.

    Some layouts emit every field label first and every value after, so
    'Prénom' has no value on its own line and the labelled read fails. Scanning
    for a configured name recovers those without inventing anything -- but only
    when exactly one matches, since the invoice addressee is often a different
    family member than the patient.
    """
    if not raw.text:
        return None
    tokens = {fold(word) for word in WORD_RE.findall(raw.text)}
    hits = {name for name in cfg.patients if fold(name) in tokens}
    hits |= {
        target
        for alias, target in cfg.aliases.items()
        if target in cfg.patients and fold(alias) in tokens
    }
    return hits.pop() if len(hits) == 1 else None


def _patient(raw: RawJustificatif, cfg: Config, warnings: list[str]) -> FieldOutcome:
    """Normalise the patient name, against an allow-list when one is configured.

    With no list configured the document's own given name is used, so the tool
    is useful before any personal configuration exists. With a list, an unknown
    name is refused rather than guessed.
    """
    if not cfg.patients:
        given = (raw.prenom or raw.nom or "").split(",")[0].strip()
        if not given:
            return FieldOutcome(
                reason="no patient name found (and no PAPERLESS_TARMED_PATIENTS "
                "configured to fall back on)"
            )
        return FieldOutcome(value=titlecase_name(given), ok=True)

    matched = match_patient(raw.prenom, raw.nom, cfg.patients, cfg.aliases)
    if matched:
        return FieldOutcome(value=matched, ok=True)

    near = close_patient(raw.prenom, raw.nom, cfg.patients, cfg.aliases, cfg.patient_fuzzy)
    if near:
        seen = " ".join(p for p in (raw.prenom, raw.nom) if p)
        warnings.append(f"patient read as {seen!r}, matched to {near!r} by similarity")
        return FieldOutcome(value=near, ok=True)

    if recovered := _patient_from_page(raw, cfg):
        warnings.append(
            f"patient field unreadable; used the only configured name on the "
            f"page ({recovered})"
        )
        return FieldOutcome(value=recovered, ok=True)

    seen = " ".join(p for p in (raw.prenom, raw.nom) if p)
    return FieldOutcome(
        reason=f"patient {seen or '(not found)'!r} matches none of {cfg.patients} "
        "(PAPERLESS_TARMED_PATIENTS)"
    )


def _treatment_date(
    raw: RawJustificatif, cfg: Config, today: date, warnings: list[str]
) -> FieldOutcome:
    """The oldest treatment date, after discarding dates that cannot be real.

    Taking a bare min() over every date on the page is fragile in exactly one
    direction: a single OCR digit error in a year ('2025' -> '2015') produces a
    date that is *older* than the truth and therefore always wins. So outliers
    far behind the newest date are dropped, and their removal is reported rather
    than done quietly.
    """
    dates = raw.all_dates
    if not dates:
        return FieldOutcome(reason="no treatment date found")

    horizon = today + timedelta(days=cfg.future_tolerance_days)
    plausible = [d for d in dates if d <= horizon]
    if not plausible:
        return FieldOutcome(reason=f"every treatment date is in the future ({max(dates)})")
    if len(plausible) < len(dates):
        dropped = sorted({d for d in dates if d > horizon})
        warnings.append(f"ignored future date(s) {[str(d) for d in dropped]}")

    newest = max(plausible)
    kept = [d for d in plausible if (newest - d).days <= cfg.max_span_days]
    if len(kept) < len(plausible):
        dropped = sorted(set(plausible) - set(kept))
        warnings.append(
            f"ignored date(s) {[str(d) for d in dropped]} more than "
            f"{cfg.max_span_days} days before {newest} (likely a misread year)"
        )

    oldest = min(kept)
    if oldest < today - timedelta(days=cfg.max_age_days):
        return FieldOutcome(reason=f"treatment date {oldest} is implausibly old")
    return FieldOutcome(value=oldest, ok=True)


def _total(raw: RawJustificatif, cfg: Config, warnings: list[str]) -> FieldOutcome:
    value = raw.montant_facture
    if value is None:
        value = raw.montant_total
        if value is not None:
            # Not a silent swap: 'Montant total' can differ by rounding, a
            # deposit or a fee, hence the warning.
            warnings.append(
                "payable amount ('Montant de la facture' / 'A payer') not found; "
                "used 'Montant total' instead"
            )
    if value is None:
        return FieldOutcome(reason="no total amount found")
    if value <= 0:
        return FieldOutcome(reason=f"amount {value} is not positive")
    if value > cfg.max_amount:
        return FieldOutcome(reason=f"amount {value} exceeds the {cfg.max_amount} ceiling")
    if raw.reminder_fee:
        # The payable total includes a late-payment fee the insurer will not
        # reimburse, so it no longer equals the invoiced treatment cost.
        warnings.append(
            f"payable amount includes a {raw.reminder_fee} reminder fee "
            f"(invoiced total: {raw.montant_total})"
        )
    return FieldOutcome(value=value, ok=True)


def _category(raw: RawJustificatif, cfg: Config) -> str | None:
    """From the official code catalogues only -- see catalogue.py."""
    return decide(raw.line_items, cfg.category_overrides)[0]


def _present(value: object, missing: str) -> FieldOutcome:
    """A descriptive value: fine when found, quietly absent when not."""
    return FieldOutcome(value=value, ok=True) if value else FieldOutcome(reason=missing)


def build_title(template: str, extracted: dict[str, FieldOutcome]) -> str | None:
    """Render the title, dropping segments we could not fill.

    Splitting on the separator afterwards keeps the template readable while still
    collapsing 'Anna -  - 2025-04-11 - Dupont' into something sane. An empty
    template disables titling altogether.
    """
    if not template.strip():
        return None

    def usable(key: str) -> str:
        outcome = extracted.get(key)
        if not outcome or not outcome.ok or outcome.value in (None, ""):
            return ""
        value = outcome.value
        return value.isoformat() if isinstance(value, date) else str(value)

    # A title without a patient or a date identifies nothing.
    if not usable(V.PATIENT) or not usable(V.TREATMENT_DATE):
        return None

    substitutions = {key: usable(key) for key in V.ALL}
    substitutions["date"] = substitutions[V.TREATMENT_DATE]  # convenience alias
    try:
        rendered = template.format(**substitutions)
    except (KeyError, IndexError) as exc:
        raise ValueError(
            f"Unknown placeholder in PAPERLESS_TARMED_TITLE: {exc}. "
            f"Available: {', '.join(sorted(substitutions))}"
        ) from None

    parts = [p.strip() for p in rendered.split(" - ")]
    return " - ".join(p for p in parts if p) or None


def evaluate(
    raw: RawJustificatif,
    cfg: Config,
    *,
    today: date | None = None,
    doc_id: int | None = None,
    source: str | None = None,
) -> Extraction:
    today = today or date.today()
    result = Extraction(doc_id=doc_id, source=source, raw=raw)

    provider = shorten_provider(raw.provider_name, raw.provider_gln, cfg.gln_overrides)
    result.values = {
        V.PATIENT: _patient(raw, cfg, result.warnings),
        V.TREATMENT_DATE: _treatment_date(raw, cfg, today, result.warnings),
        V.TOTAL: _total(raw, cfg, result.warnings),
        V.TOTAL_UNROUNDED: _present(raw.montant_total, "no 'Montant total' found"),
        V.CATEGORY: _present(_category(raw, cfg), "no category"),
        V.PROVIDER: _present(
            titlecase_name(provider) if provider else None, "provider name not found"
        ),
        V.PROVIDER_GLN: _present(raw.provider_gln, "no provider GLN found"),
    }

    title = build_title(cfg.title_template, result.values)
    result.title = (
        FieldOutcome(value=title, ok=True)
        if title
        else FieldOutcome(reason="title needs at least a patient and a treatment date")
    )

    if not result.get(V.PROVIDER).ok:
        result.warnings.append("provider name not found")
    if not raw.line_items:
        # Without the service table there is no category, and the amount and
        # dates rest on the header alone. Worth knowing rather than inferring
        # it from a missing title segment.
        result.warnings.append("no service lines parsed from the table")
    elif not result.get(V.CATEGORY).ok:
        seen = sorted({li.tariff for li in raw.line_items})
        described = sum(1 for li in raw.line_items if li.description)
        result.warnings.append(
            f"no category: tariff type(s) {seen} unmapped, "
            f"{described}/{len(raw.line_items)} line(s) had a description"
        )
    return result


def needs_review(result: Extraction) -> bool:
    """Tag when anything was skipped or substituted, so it surfaces on review."""
    return bool(result.warnings) or not result.core_ok
