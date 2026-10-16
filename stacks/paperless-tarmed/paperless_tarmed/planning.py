"""What to write, given what Paperless already holds.

Extracted from the CLI so the webhook cannot drift from it. The batch tool was
careful -- fill blanks only, titles behind an opt-in, every write journalled --
while the webhook wrote everything unconditionally and always retitled. That
asymmetry made the *automated* path the dangerous one: it discarded manual
corrections silently, and because every run modified the document, a
"Document Updated" trigger turned it into a self-sustaining loop that also
renamed files on disk (PAPERLESS_FILENAME_FORMAT embeds the title).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .confidence import needs_review
from .models import Extraction
from .report import DIFFER, FILL, TITLE, Row


def specs_from_fields(fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalise resolved CustomField objects to the spec dicts used elsewhere.

    The CLI loads these from its cache (fields.json); the webhook resolves them
    live. Both need the same shape for the comparison below to behave alike.
    """
    return {
        key: {
            "id": f.id,
            "name": f.name,
            "data_type": f.data_type,
            "select_options": dict(f.select_options),
            "select_labels": dict(f.select_labels),
        }
        for key, f in fields.items()
    }


def current_values(doc: dict, fields: dict[str, Any]) -> dict[str, Any]:
    """What Paperless currently holds, in our own value vocabulary."""
    by_id = {
        entry["field"]: entry.get("value")
        for entry in doc.get("custom_fields") or []
        if isinstance(entry, dict) and "field" in entry
    }
    current: dict[str, Any] = {TITLE: doc.get("title")}
    for key, spec in fields.items():
        value = by_id.get(spec["id"])
        if spec["data_type"] == "select" and value is not None:
            # Back to the option's original label, not its folded form.
            value = (spec.get("select_labels") or {}).get(str(value), value)
        current[key] = value
    return current


def planned_writes(
    row: Row,
    result: Extraction,
    fields: dict[str, Any],
    *,
    overwrite: bool,
    do_title: bool,
) -> tuple[dict[int, Any], str | None]:
    """Decide the actual writes for one document.

    Custom fields fill blanks only unless `overwrite`, so re-running is safe and
    manual corrections survive. The title is always an overwrite by nature, so it
    needs its own opt-in.
    """
    writes: dict[int, Any] = {}
    for key in fields:
        verdict = row.status.get(key)
        if verdict == FILL or (overwrite and verdict == DIFFER):
            writes[fields[key]["id"]] = result.value(key)
    title = result.title.value if do_title and row.status.get(TITLE) in (FILL, DIFFER) else None
    return writes, title


def encode_writes(
    writes: dict[int, Any],
    fields: dict[str, Any],
    currency: str | None,
    *,
    encode: Callable[[Any, Any, str | None], Any],
) -> tuple[dict[int, Any], list[str]]:
    """Shape planned writes the way Paperless stores them.

    `fields` maps our key to its CustomField, in the order problems should be
    reported. A value that cannot be encoded (e.g. a select option Paperless
    does not have) becomes a problem string rather than aborting the document.
    """
    encoded: dict[int, Any] = {}
    problems: list[str] = []
    for key, field in fields.items():
        if field.id not in writes:
            continue
        try:
            encoded[field.id] = encode(field, writes[field.id], currency)
        except Exception as exc:  # reported to the caller, which tags for review
            problems.append(f"{key}: {exc}")
    return encoded, problems


def should_tag_for_review(
    result: Extraction,
    problems: list[str],
    doc: dict,
    justificatif_type_id: int | None,
) -> bool:
    """Whether a document earns the review tag.

    Only a justificatif can fail. A document with no justificatif page -- a
    letter, an invoice, the QR sheet split off a scan -- is simply something
    else and is left alone. The exception is a document Paperless itself has
    typed as a justificatif: finding no form page there means an unknown layout
    or missed OCR, which must surface rather than pass silently.
    """
    if not (problems or needs_review(result)):
        return False
    if result.found:
        return True
    return justificatif_type_id is not None and doc.get("document_type") == justificatif_type_id


def review_reasons(result: Extraction, problems: list[str]) -> list[str]:
    """Why a document earned the review tag, most specific first."""
    return problems or list(result.warnings) or ["incomplete extraction"]


def outcome_marks(
    doc: dict,
    *,
    failed: bool,
    found: bool,
    reasons: list[str],
    written: list[str],
    title: str | None,
    review_tag: int | None,
    done_tag: int | None,
    when: datetime | None = None,
) -> tuple[int | None, str | None]:
    """The tag to add and the note to leave once a document is processed, so
    Paperless itself shows when we ran and what came of it.

    - failed: the review tag, and a note with the time and the reasons. Only
      when the tag is new, so re-running over the archive does not pile up
      notes on a document that keeps failing.
    - read (a justificatif page, nothing to review): the done tag, and a note
      listing what was written -- only when something was, so a re-run that
      changes nothing leaves no trace.
    - anything else (not a justificatif): nothing.

    A justificatif without the done tag has therefore never been processed.
    """
    when = when or datetime.now().astimezone()
    stamp = f"paperless-tarmed, {when:%Y-%m-%d %H:%M}"
    tags = doc.get("tags") or []
    if failed:
        if review_tag is None or review_tag in tags:
            return None, None
        lines = [f"{stamp}: extraction failed"] + [f"- {r}" for r in reasons[:5]]
        return review_tag, "\n".join(lines)
    if not found:
        return None, None
    tag = done_tag if done_tag is not None and done_tag not in tags else None
    done = written + ([f"title: {title}"] if title else [])
    note = "\n".join([f"{stamp}: read and written"] + [f"- {d}" for d in done]) if done else None
    return tag, note


def append_journal(
    path: str | Path,
    *,
    doc_id: int,
    fields: dict[int, Any],
    title_from: Any = None,
    title_to: str | None = None,
    source: str = "cli",
) -> None:
    """Append one write to the change journal -- the only record of what we did."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "source": source,
                    "doc_id": doc_id,
                    "fields": {str(k): str(v) for k, v in fields.items()},
                    "title_from": title_from,
                    "title_to": title_to,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
