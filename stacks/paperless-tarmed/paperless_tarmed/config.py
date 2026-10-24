"""Configuration, entirely from the environment.

Nothing about a particular Paperless instance is baked in: no field names, no
field ids, no patient names, no document type. All of it is supplied at run time.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from . import values

#: Placeholders available in the title template, plus '{date}' as an alias for
#: '{treatment_date}'.
DEFAULT_TITLE_TEMPLATE = "{patient} - {category} - {treatment_date} - {provider}"


_RELATIVE_RE = re.compile(r"(\d+)\s*([dwmy])", re.IGNORECASE)


def parse_since(value: str, today: date | None = None) -> str:
    """Turn '2y' / '18m' / '90d' / '6w' -- or an ISO date -- into an ISO date.

    Month and year steps are calendar-correct rather than 30/365-day
    approximations, so '2y' from 2026-08-31 is 2024-08-31.
    """
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value

    today = today or date.today()
    match = _RELATIVE_RE.fullmatch(value)
    if not match:
        raise SystemExit(
            f"Cannot read {value!r} as a date. Use an ISO date (2024-01-31) or a "
            "relative span: 90d, 6w, 18m, 2y."
        )
    amount, unit = int(match.group(1)), match.group(2).lower()

    if unit == "d":
        return (today - timedelta(days=amount)).isoformat()
    if unit == "w":
        return (today - timedelta(weeks=amount)).isoformat()

    months = amount * 12 if unit == "y" else amount
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    # Clamp for short months: 31 March minus one month is 28/29 February.
    last_day = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(today.day, last_day)).isoformat()


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> list[str]:
    """Load KEY=VALUE lines from a .env file into the environment.

    The README tells you to `cp .env.example .env`, so that file has to actually
    be read -- otherwise the defaults silently win and the failure looks like a
    Paperless problem. A real environment variable takes precedence unless
    `override` is set, which is the usual convention.

    Returns the names loaded, so the caller can say where its settings came from
    (never the values -- one of them is an API token).
    """
    file = Path(path)
    if not file.is_file():
        return []
    loaded: list[str] = []
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _split_pairs(raw: str, sep: str = "=") -> dict[str, str]:
    """Parse 'a=b,c=d' -- or a JSON object, for values containing a comma."""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return {str(k): str(v) for k, v in json.loads(raw).items()}
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or sep not in chunk:
            continue
        key, _, value = chunk.partition(sep)
        key, value = key.strip(), value.strip()
        if key and value:
            out[key] = value
    return out


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()] if raw else []


@dataclass
class Config:
    paperless_url: str = ""
    paperless_token: str = ""

    #: extracted value key -> Paperless custom field name. Empty means "write
    #: nothing"; `paperless-tarmed fields` explains how to populate it.
    fields: dict[str, str] = field(default_factory=dict)

    #: Similarity threshold for near-matching a misspelt patient name against
    #: the list. 0 disables it; 1 demands an exact match. 0.85 is a chosen
    #: default, not a measured one.
    patient_fuzzy: float = 0.85
    #: Optional allow-list of patient names. When empty, whatever given name the
    #: document carries is used as-is, which keeps the tool usable out of the box.
    patients: list[str] = field(default_factory=list)
    #: Extra spellings/nicknames -> preset value, e.g. {"Annie": "Anna"}.
    aliases: dict[str, str] = field(default_factory=dict)
    #: 13-digit GLN -> short provider label, overriding the automatic rule.
    gln_overrides: dict[str, str] = field(default_factory=dict)
    #: tariff type code -> category label, merged over the built-in grouping.
    #: Used only when the service description yields nothing.
    category_overrides: dict[str, str] = field(default_factory=dict)

    #: Fallback when the document does not state one.
    currency: str = "CHF"
    #: Empty disables title generation entirely.
    title_template: str = DEFAULT_TITLE_TEMPLATE
    #: Optional default filter for `pull`; no default document type is assumed.
    document_type: str = ""
    #: Optional default age limit for `pull`, e.g. '2y'. Empty means no limit.
    since: str = ""
    #: Empty disables tagging.
    review_tag: str = "extraction-failed"
    done_tag: str = "tarmed"

    # Plausibility bounds. A value outside these is never written.
    max_amount: Decimal = Decimal("50000")
    max_age_days: int = 3650
    future_tolerance_days: int = 2
    # How far before the newest date a treatment date may still be. Guards
    # against an OCR digit error in a year ('2025' -> '2015') dragging the
    # oldest-date calculation to a wrong value.
    max_span_days: int = 730

    webhook_secret: str = ""
    #: The webhook plans its writes exactly like `apply`: blanks only, unless
    #: this is set. Left off, a re-run is a no-op, so an "updated" trigger
    #: cannot feed itself.
    webhook_overwrite: bool = False
    #: Titles are an overwrite by nature, and PAPERLESS_FILENAME_FORMAT may put
    #: the title in the on-disk path -- so automated retitling renames files.
    #: Off by default; `apply --title` remains the deliberate way to do it.
    webhook_title: bool = False
    #: Optional ntfy push after the webhook reads a justificatif. Off unless
    #: both URL and token are set (paperless_tarmed/notify.py).
    ntfy_url: str = ""
    ntfy_token: str = ""
    ntfy_topic: str = "paperless"
    #: The patient's name shows on a lock screen: left out unless asked for.
    ntfy_patient: bool = False
    #: Paperless as a browser reaches it, for links in notifications.
    #: PAPERLESS_URL is usually the internal service address, useless on a phone.
    public_url: str = ""
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))
    verify_ssl: bool = True
    timeout: float = 60.0

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ.get
        return cls(
            paperless_url=env("PAPERLESS_URL", "").rstrip("/"),
            paperless_token=env("PAPERLESS_TOKEN", ""),
            fields=_split_pairs(env("PAPERLESS_TARMED_FIELDS", "")),
            patients=_env_list("PAPERLESS_TARMED_PATIENTS"),
            patient_fuzzy=float(env("PAPERLESS_TARMED_PATIENT_FUZZY", "0.85")),
            aliases=_split_pairs(env("PAPERLESS_TARMED_ALIASES", "")),
            gln_overrides=_split_pairs(env("PAPERLESS_TARMED_GLN_OVERRIDES", "")),
            category_overrides=_split_pairs(env("PAPERLESS_TARMED_CATEGORIES", "")),
            currency=env("PAPERLESS_TARMED_CURRENCY", "CHF"),
            title_template=env("PAPERLESS_TARMED_TITLE", DEFAULT_TITLE_TEMPLATE),
            document_type=env("PAPERLESS_TARMED_DOCUMENT_TYPE", ""),
            since=env("PAPERLESS_TARMED_SINCE", ""),
            review_tag=env("PAPERLESS_TARMED_REVIEW_TAG", "extraction-failed"),
            done_tag=env("PAPERLESS_TARMED_DONE_TAG", "tarmed"),
            max_amount=Decimal(env("PAPERLESS_TARMED_MAX_AMOUNT", "50000")),
            max_age_days=int(env("PAPERLESS_TARMED_MAX_AGE_DAYS", "3650")),
            future_tolerance_days=int(env("PAPERLESS_TARMED_FUTURE_TOLERANCE_DAYS", "2")),
            max_span_days=int(env("PAPERLESS_TARMED_MAX_SPAN_DAYS", "730")),
            webhook_secret=env("PAPERLESS_TARMED_WEBHOOK_SECRET", ""),
            webhook_overwrite=env("PAPERLESS_TARMED_WEBHOOK_OVERWRITE", "0").lower()
            not in ("0", "false", "no", ""),
            webhook_title=env("PAPERLESS_TARMED_WEBHOOK_TITLE", "0").lower()
            not in ("0", "false", "no", ""),
            ntfy_url=env("PAPERLESS_TARMED_NTFY_URL", "").strip(),
            ntfy_token=env("PAPERLESS_TARMED_NTFY_TOKEN", "").strip(),
            ntfy_topic=env("PAPERLESS_TARMED_NTFY_TOPIC", "paperless").strip() or "paperless",
            ntfy_patient=env("PAPERLESS_TARMED_NTFY_PATIENT", "0").lower()
            not in ("0", "false", "no", ""),
            public_url=env("PAPERLESS_TARMED_PUBLIC_URL", "").rstrip("/"),
            cache_dir=Path(env("PAPERLESS_TARMED_CACHE_DIR", ".cache")),
            verify_ssl=env("PAPERLESS_TARMED_VERIFY_SSL", "1").lower()
            not in ("0", "false", "no"),
            timeout=float(env("PAPERLESS_TARMED_TIMEOUT", "60")),
        )

    def unknown_field_keys(self) -> list[str]:
        """Mapped keys this extractor does not produce, e.g. a typo."""
        return sorted(k for k in self.fields if k not in values.ALL)

    def require_api(self) -> None:
        missing = [
            name
            for name, value in (
                ("PAPERLESS_URL", self.paperless_url),
                ("PAPERLESS_TOKEN", self.paperless_token),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                f"Missing environment variable(s): {', '.join(missing)}.\n"
                "Set them before running commands that talk to Paperless."
            )
