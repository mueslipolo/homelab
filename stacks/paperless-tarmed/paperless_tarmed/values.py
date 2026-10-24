"""The values this extractor can publish.

These keys are the stable, language-neutral vocabulary of the project. A user
maps whichever ones they care about onto their own Paperless custom fields, so
nothing here assumes a particular field name, language or set of fields.
"""

from __future__ import annotations

PATIENT = "patient"
TREATMENT_DATE = "treatment_date"
TOTAL = "total"
TOTAL_UNROUNDED = "total_unrounded"
CATEGORY = "category"
PROVIDER = "provider"
PROVIDER_GLN = "provider_gln"

#: Every key, in the order reports display them.
ALL: tuple[str, ...] = (
    PATIENT,
    TREATMENT_DATE,
    TOTAL,
    TOTAL_UNROUNDED,
    CATEGORY,
    PROVIDER,
    PROVIDER_GLN,
)

#: Values guarded by plausibility checks. The rest are descriptive: they are
#: written when present and simply absent when not.
CORE: tuple[str, ...] = (PATIENT, TREATMENT_DATE, TOTAL)

DESCRIPTIONS: dict[str, str] = {
    PATIENT: "Patient given name, normalised against the configured list",
    TREATMENT_DATE: "Oldest treatment date on the justificatif",
    TOTAL: "Payable total ('Montant de la facture' / 'A payer')",
    TOTAL_UNROUNDED: "'Montant total' as printed",
    CATEGORY: "Short category derived from the line items' tariff type",
    PROVIDER: "Short provider label (fournisseur de prestations)",
    PROVIDER_GLN: "Provider's 13-digit GLN/EAN",
}
