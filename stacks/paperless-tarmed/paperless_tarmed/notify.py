"""Optional push notification once the webhook has read a justificatif (ntfy).

Off unless PAPERLESS_TARMED_NTFY_URL and PAPERLESS_TARMED_NTFY_TOKEN are set.
A notification is a courtesy: whatever goes wrong here is logged and dropped,
never raised into the processing that has already written to Paperless.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from . import values as V
from .config import Config
from .report import document_url

log = logging.getLogger("paperless_tarmed.notify")

TIMEOUT = 5.0


def message(cfg: Config, result: Any, currency: str) -> str:
    """Provider · amount · date, plus the patient only when asked for: the
    text shows on a lock screen."""
    parts: list[str] = []
    if cfg.ntfy_patient and result.value(V.PATIENT):
        parts.append(str(result.value(V.PATIENT)))
    if result.value(V.PROVIDER):
        parts.append(str(result.value(V.PROVIDER)))
    total = result.value(V.TOTAL)
    if total is not None:
        parts.append(f"{currency} {total}")
    when = result.value(V.TREATMENT_DATE)
    if isinstance(when, date):
        parts.append(f"{when:%d.%m.%Y}")
    elif when:
        parts.append(str(when))
    return " · ".join(parts) or "No details read"


def bill_processed(cfg: Config, doc_id: int, result: Any, currency: str, *, review: bool) -> None:
    """Push one message for a justificatif just handled; never raises."""
    if not (cfg.ntfy_url and cfg.ntfy_token):
        return
    headers = {
        "Authorization": f"Bearer {cfg.ntfy_token}",
        "Title": "Bill needs review" if review else "Bill processed",
        "Tags": "warning" if review else "receipt",
    }
    link = document_url(cfg.public_url, doc_id)
    if link:
        headers["Click"] = link
    try:
        response = httpx.post(
            f"{cfg.ntfy_url.rstrip('/')}/{cfg.ntfy_topic}",
            content=message(cfg, result, currency).encode("utf-8"),
            headers=headers,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 -- see module docstring
        log.warning("doc %s: notification not sent: %s", doc_id, exc)
