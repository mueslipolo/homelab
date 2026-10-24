"""Tariff type -> short category, for the document title.

`data/tariff_types.json` is the official Forum Datenaustausch 'Tariftyp-Liste',
written by `paperless-tarmed refresh-tariffs`. Only that command reads it, to
show what changed; nothing here depends on it. Never hand-edit it.

The grouping below is editorial -- it maps official codes onto short labels
readable in a title -- so it lives in code where it survives a refresh. It can be
overridden or extended per deployment via PAPERLESS_TARMED_CATEGORIES. Anything
unmapped yields no category, and the title simply omits that segment.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

BUNDLED_DATA_PATH = Path(__file__).resolve().parent / "data" / "tariff_types.json"


def writable_data_path() -> Path:
    """Where `refresh-tariffs` writes.

    Inside the container the package directory is root-owned while the process
    runs unprivileged, so the image points PAPERLESS_TARMED_TARIFF_PATH at the data
    volume. A refresh then survives redeploys instead of being baked into the
    image.
    """
    override = os.environ.get("PAPERLESS_TARMED_TARIFF_PATH")
    return Path(override) if override else BUNDLED_DATA_PATH


def active_data_path() -> Path:
    """Where to read from: the refreshed copy when present, else the bundled one."""
    override = writable_data_path()
    return override if override.exists() else BUNDLED_DATA_PATH


CODES_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "Médecin": ("001", "002", "003", "005", "007", "TMA", "050", "051", "272",
                "273", "290", "350", "405", "406", "407"),
    "Labo": ("316", "317", "318"),
    "Pharma": ("401", "402", "403", "404", "408", "410"),
    "Matériel": ("451", "452", "453", "454", "980"),
    "Physio": ("311", "312", "553", "556"),
    "Ergo": ("325", "338", "554"),
    "Logo": ("328", "336", "555"),
    "Chiro": ("324", "424"),
    "Psy": ("330", "340", "352", "581", "582"),
    "Dentiste": ("222", "223", "320", "321", "322", "323", "335"),
    "Soins": ("334", "532", "533", "534", "535", "960", "963", "964", "965",
              "966", "967", "970", "971"),
    "Sage-femme": ("550",),
    "Hôpital": ("010", "011", "012", "020", "021", "030", "031", "302", "910",
                "930", "940"),
    "Thérapie": ("354", "590", "595"),  # complementary medicine
    "Podologie": ("341",),
    "Nutrition": ("510", "514", "551"),
    "Diabète": ("511", "515"),
    "Transport": ("580", "583", "584"),
    "Dialyse": ("500", "501", "502", "503", "504", "505"),
    "Invalidité": ("904", "907", "908", "909", "914", "915", "920", "923", "925"),
    "Optique": ("331",),
    "Audition": ("329",),
    "Orthopédie": ("326", "327", "337"),
    "Étranger": ("280",),
}

CATEGORY_BY_CODE: dict[str, str] = {
    code: category for category, codes in CODES_BY_CATEGORY.items() for code in codes
}


@lru_cache(maxsize=1)
def official_labels() -> dict[str, dict]:
    """The generated table, keyed by tariff code. Empty if never refreshed."""
    path = active_data_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {e["code"]: e for e in payload.get("tariff_types", [])}


def category_for_code(
    code: str | None, overrides: dict[str, str] | None = None
) -> str | None:
    """Category for one tariff type, with user overrides taking precedence.

    Overrides let a deployment relabel or extend the grouping -- German labels,
    a tariff we do not map, or a different split -- without editing this file.
    """
    if not code:
        return None
    key = code.strip().upper()
    if overrides and (pinned := overrides.get(key)):
        return pinned
    return CATEGORY_BY_CODE.get(key)
