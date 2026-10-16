"""Data structures shared across the extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class LineItem:
    """One service row from the justificatif's table."""

    date: date
    tariff: str
    amount: Decimal | None
    code: str | None = None
    description: str | None = None


@dataclass
class RawJustificatif:
    """Everything read off a single justificatif, before validation.

    Values here are 'what the document says'. Nothing is guaranteed sane yet --
    that is confidence.py's job.
    """

    page_indexes: list[int] = field(default_factory=list)
    release: str | None = None
    nom: str | None = None
    prenom: str | None = None
    header_dates: list[date] = field(default_factory=list)
    line_items: list[LineItem] = field(default_factory=list)
    montant_facture: Decimal | None = None  # payable, after Swiss rounding
    montant_total: Decimal | None = None  # unrounded sum
    provider_name: str | None = None
    provider_gln: str | None = None
    currency: str | None = None
    reminder_fee: Decimal | None = None
    text: str = ""

    @property
    def all_dates(self) -> list[date]:
        return [*self.header_dates, *(li.date for li in self.line_items)]


@dataclass
class FieldOutcome:
    """A single extracted field plus whether it may be written."""

    value: Any = None
    ok: bool = False
    reason: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.ok


@dataclass
class Extraction:
    """The result for one document.

    Extracted values live in `values`, keyed by the vocabulary in `values.py`,
    rather than as fixed attributes -- which is what lets a user map any subset
    of them onto their own custom fields.
    """

    doc_id: int | None = None
    source: str | None = None
    values: dict[str, FieldOutcome] = field(default_factory=dict)
    title: FieldOutcome = field(default_factory=FieldOutcome)
    raw: RawJustificatif | None = None
    warnings: list[str] = field(default_factory=list)
    # False when no 'Justificatif de remboursement' page was found at all: the
    # document is something else, which is not an extraction failure.
    found: bool = True

    def get(self, key: str) -> FieldOutcome:
        return self.values.get(key, FieldOutcome())

    def value(self, key: str) -> Any:
        return self.get(key).value

    @property
    def core_ok(self) -> bool:
        """True when every plausibility-gated value came through."""
        from . import values as _values

        return all(self.get(k).ok for k in _values.CORE)

    def to_dict(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            return str(value) if isinstance(value, (date, Decimal)) else value

        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "values": {
                key: {
                    "value": encode(outcome.value),
                    "ok": outcome.ok,
                    "reason": outcome.reason,
                }
                for key, outcome in self.values.items()
            },
            "title": {
                "value": self.title.value,
                "ok": self.title.ok,
                "reason": self.title.reason,
            },
            "warnings": self.warnings,
        }
