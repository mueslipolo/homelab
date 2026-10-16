"""Comparing what Paperless holds against what we extracted.

This is the quality instrument. A `differ` against a value entered by hand is the
most informative signal available -- it is a labelled disagreement, and worth more
than any number of green rows.

Which columns appear is driven by the value keys the deployment maps, so this
works whatever subset of fields a user configures.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Extraction

# Per-field verdicts.
OK = "ok"  # already correct
FILL = "fill"  # currently empty, we have a value
DIFFER = "differ"  # non-empty and disagrees -- look at these
FAIL = "fail"  # extraction produced nothing usable

TITLE = "title"


def _comparable(value: Any) -> str:
    """Normalise for comparison: '45.0', '45.00' and Decimal('45.00') agree."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    # Paperless monetary fields carry a currency prefix ('CHF388.80'); the
    # extracted value is a bare Decimal. Compare the numbers, not the notation.
    if money := re.fullmatch(r"([A-Za-z]{3})\s*(-?[\d'’ .,]+)", text):
        text = money.group(2)
    try:
        return f"{Decimal(text):.2f}"
    except (InvalidOperation, ArithmeticError):
        return text


def classify(current: Any, extracted: Any, ok: bool) -> str:
    if not ok:
        return FAIL
    cur, new = _comparable(current), _comparable(extracted)
    if not cur:
        return FILL
    return OK if cur == new else DIFFER


@dataclass
class Row:
    doc_id: int
    keys: tuple[str, ...] = ()
    current: dict[str, Any] = field(default_factory=dict)
    extracted: dict[str, Any] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def worst(self) -> str:
        for level in (FAIL, DIFFER, FILL, OK):
            if level in self.status.values():
                return level
        return OK


def build_row(
    doc_id: int,
    current: dict[str, Any],
    result: Extraction,
    keys: Sequence[str],
) -> Row:
    """Compare `keys` (plus the title) between Paperless and the extraction."""
    row = Row(doc_id=doc_id, keys=tuple(keys), warnings=list(result.warnings))
    for key in (*keys, TITLE):
        outcome = result.title if key == TITLE else result.get(key)
        row.current[key] = current.get(key)
        row.extracted[key] = outcome.value
        row.status[key] = classify(current.get(key), outcome.value, outcome.ok)
        if outcome.reason:
            row.reasons[key] = outcome.reason
    return row


def summarise(rows: list[Row], keys: Sequence[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {k: {} for k in (*keys, TITLE)}
    for row in rows:
        for key in counts:
            verdict = row.status.get(key, FAIL)
            counts[key][verdict] = counts[key].get(verdict, 0) + 1
    return counts


RULE = "=" * 74


def document_url(base_url: str, doc_id: int) -> str:
    """Link to the document in the Paperless UI, for eyeballing a disagreement."""
    return f"{base_url.rstrip('/')}/documents/{doc_id}/details" if base_url else ""


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _label(key: str) -> str:
    return key.replace("_", " ")


def _shown(value: Any) -> str:
    return "(empty)" if value in (None, "") else str(value)


def needs_attention(row: Row, keys: Sequence[str]) -> bool:
    """A title rename is expected; a failure or a disagreement is not."""
    return bool(row.warnings) or any(row.status.get(k) in (FAIL, DIFFER) for k in keys)


def _uniform_failure(row: Row, keys: Sequence[str]) -> str | None:
    """One reason shared by every value -- say it once, not once per column."""
    reasons = {row.reasons.get(k) for k in keys}
    if len(reasons) == 1 and all(row.status.get(k) == FAIL for k in keys):
        return reasons.pop()
    return None


def _heading(out: io.StringIO, text: str) -> None:
    out.write(f"\n{RULE}\n {text}\n{RULE}\n\n")


def _detail_block(out: io.StringIO, row: Row, keys: Sequence[str], base_url: str = "") -> None:
    title_now = _shown(row.current.get(TITLE))
    out.write(f"  #{row.doc_id}  {_clip(title_now, 62)}\n")
    if url := document_url(base_url, row.doc_id):
        out.write(f"        {url}\n")

    if shared := _uniform_failure(row, keys):
        out.write(f"        COULD NOT READ  {shared}\n\n")
        return

    for key in keys:
        verdict = row.status.get(key, FAIL)
        value, current = row.extracted.get(key), row.current.get(key)
        if verdict == FAIL:
            out.write(f"        {_label(key):<16} could not read - {row.reasons.get(key, '')}\n")
        elif verdict == DIFFER:
            out.write(f"        {_label(key):<16} {_shown(current)}  ->  {_shown(value)}   DISAGREES\n")
        elif verdict == FILL:
            out.write(f"        {_label(key):<16} (empty)  ->  {_shown(value)}\n")
        else:
            out.write(f"        {_label(key):<16} {_shown(value)}\n")
    for warning in row.warnings:
        out.write(f"        note: {warning}\n")
    if row.status.get(TITLE) in (FILL, DIFFER) and row.extracted.get(TITLE):
        out.write(f"        new title        {row.extracted[TITLE]}\n")
    out.write("\n")


def render_table(rows: list[Row], keys: Sequence[str], base_url: str = "") -> str:
    """Grouped by what you need to do about it, rather than one wide table."""
    keys = tuple(keys)
    out = io.StringIO()
    counts = summarise(rows, keys)

    _heading(out, f"SUMMARY - {len(rows)} document(s)")
    for key in (*keys, TITLE):
        parts = []
        for verdict, wording in (
            (OK, "already correct"),
            (FILL, "will fill"),
            (DIFFER, "disagrees"),
            (FAIL, "could not read"),
        ):
            if n := counts[key].get(verdict):
                parts.append(f"{n} {wording}")
        out.write(f"  {_label(key):<17} {'  |  '.join(parts) or 'nothing'}\n")

    attention = [r for r in rows if needs_attention(r, keys)]
    routine = [r for r in rows if not needs_attention(r, keys)]

    if attention:
        _heading(out, f"NEEDS ATTENTION ({len(attention)})")
        for row in attention:
            _detail_block(out, row, keys, base_url)

    if routine:
        _heading(out, f"CLEAN ({len(routine)}) - values read, nothing disagrees")
        for row in routine:
            values = "  |  ".join(_shown(row.extracted.get(k)) for k in keys)
            out.write(f"  #{row.doc_id}  {values}\n")
            if url := document_url(base_url, row.doc_id):
                out.write(f"          {url}\n")
            if row.status.get(TITLE) in (FILL, DIFFER) and row.extracted.get(TITLE):
                out.write(f"          title -> {row.extracted[TITLE]}\n")
        out.write("\n")

    return out.getvalue()


def render_csv(rows: list[Row], keys: Sequence[str], base_url: str = "") -> str:
    columns = (*keys, TITLE)
    out = io.StringIO()
    writer = csv.writer(out)
    header = ["doc_id", "url"]
    for key in columns:
        header += [f"{key}_status", f"{key}_current", f"{key}_extracted", f"{key}_reason"]
    header.append("warnings")
    writer.writerow(header)
    for row in rows:
        record: list[Any] = [row.doc_id, document_url(base_url, row.doc_id)]
        for key in columns:
            record += [
                row.status.get(key, ""),
                row.current.get(key, ""),
                row.extracted.get(key, ""),
                row.reasons.get(key, ""),
            ]
        record.append("; ".join(row.warnings))
        writer.writerow(record)
    return out.getvalue()
