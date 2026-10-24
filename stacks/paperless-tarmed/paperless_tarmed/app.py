"""Webhook receiver for newly-consumed documents.

Deliberately *not* a PAPERLESS_POST_CONSUME_SCRIPT: in paperless-ngx 3.x,
`Consumer.run_post_consume_script()` calls `self._fail(...)` on any exception,
which marks the whole consume FAILED even though the document was already stored.
A webhook cannot break the consume pipeline.

The handler answers 200 straight away and does the work on a background thread,
so Paperless never waits on us.
"""

from __future__ import annotations

import hmac
import logging
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from . import values as V
from .confidence import needs_review
from .config import Config
from .pipeline import extract_pdf
from .report import TITLE

log = logging.getLogger("paperless_tarmed.app")

app = FastAPI(title="paperless-tarmed", version="0.1.0")
_lock = threading.Lock()

def _doc_id_from(payload: Any, request: Request) -> int | None:
    """The id from a JSON body or the query string."""
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("doc_id", "document_id", "id", "pk"):
            if key in payload:
                candidates.append(payload[key])
    for key in ("doc_id", "document_id", "id"):
        if key in request.query_params:
            candidates.append(request.query_params[key])
    for value in candidates:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def process(doc_id: int, cfg: Config) -> None:
    """Download, extract, write. Never raises -- the caller is a background task.

    Plans its writes through the same code as `apply`, so the automated path is
    no less careful than the manual one: custom fields fill blanks only (unless
    PAPERLESS_TARMED_WEBHOOK_OVERWRITE) and the title is left alone unless
    PAPERLESS_TARMED_WEBHOOK_TITLE. That also makes a re-run a no-op, so a
    "Document Updated" trigger cannot feed itself a loop.
    """
    from .paperless import Paperless
    from .planning import (
        append_journal,
        current_values,
        encode_writes,
        outcome_marks,
        planned_writes,
        review_reasons,
        should_tag_for_review,
        specs_from_fields,
    )
    from .report import build_row

    try:
        with _lock, Paperless(cfg) as api:
            resolved = {key: api.find_field(name) for key, name in cfg.fields.items()}
            specs = specs_from_fields(resolved)
            keys = [key for key in V.ALL if key in specs]

            with tempfile.TemporaryDirectory(prefix="paperless-tarmed-") as tmp:
                pdf = api.download(doc_id, Path(tmp) / f"{doc_id}.pdf")
                result = extract_pdf(pdf, cfg, doc_id=doc_id)

            # What Paperless holds now, so a value already there is left alone.
            doc = api.document(doc_id)
            row = build_row(doc_id, current_values(doc, specs), result, keys)
            writes, title = planned_writes(
                row,
                result,
                specs,
                overwrite=cfg.webhook_overwrite,
                do_title=cfg.webhook_title,
            )

            currency = (result.raw.currency if result.raw else None) or cfg.currency
            problems = [
                f"{key}: {result.get(key).reason}" for key in keys if not result.get(key).ok
            ]
            encoded, encode_problems = encode_writes(
                writes,
                {key: resolved[key] for key in keys},
                currency,
                encode=api.encode_value,
            )
            problems += encode_problems

            if encoded:
                api.set_custom_fields(doc_id, encoded)
            if title:
                api.patch_document(doc_id, title=title)
            if encoded or title:
                append_journal(
                    cfg.cache_dir / "journal.jsonl",
                    doc_id=doc_id,
                    fields=encoded,
                    title_from=row.current.get(TITLE),
                    title_to=title,
                    source="webhook",
                )

            log.info(
                "doc %s: wrote %d field(s), title=%s, warnings=%s, problems=%s",
                doc_id,
                len(encoded),
                "rewritten" if title else "unchanged",
                result.warnings,
                problems,
            )

            # Resolving the type costs an API call; only needed when no
            # justificatif page was found.
            type_id = None
            if (problems or needs_review(result)) and cfg.document_type and not result.found:
                type_id = api.document_type_id(cfg.document_type)
            failed = should_tag_for_review(result, problems, doc, type_id)
            if not failed and not result.found:
                log.info("doc %s: no justificatif page; not one, left untagged", doc_id)
            review = api.tag_id(cfg.review_tag, create=True) if failed and cfg.review_tag else None
            done = (api.tag_id(cfg.done_tag, create=True)
                    if not failed and result.found and cfg.done_tag else None)
            tag, note = outcome_marks(
                doc,
                failed=failed,
                found=result.found,
                reasons=review_reasons(result, problems),
                written=[f"{key}={result.value(key)}" for key in keys if resolved[key].id in encoded],
                title=title,
                review_tag=review,
                done_tag=done,
            )
            if tag is not None:
                api.add_tag(doc_id, tag)
                log.info("doc %s: tagged %r", doc_id, cfg.review_tag if failed else cfg.done_tag)
            if note:
                _add_note(api, doc_id, note)
            if result.found:
                from .notify import bill_processed

                bill_processed(cfg, doc_id, result, currency, review=failed)
    except Exception as exc:
        # Swallowing is deliberate: a failure here must not become a retry storm
        # or an unhandled thread exception. The log is the audit trail; the note
        # (best effort) makes it visible in Paperless.
        log.exception("doc %s: extraction failed", doc_id)
        try:
            with Paperless(cfg) as api:
                stamp = f"{datetime.now().astimezone():%Y-%m-%d %H:%M}"
                _add_note(api, doc_id, f"paperless-tarmed, {stamp}: extraction failed\n- {type(exc).__name__}: {exc}"[:1000])
        except Exception:
            log.warning("doc %s: could not leave a note either", doc_id)


def _add_note(api: Any, doc_id: int, text: str) -> None:
    """A note is a courtesy: failing to leave one must not undo the writes."""
    from .paperless import PaperlessError

    try:
        api.add_note(doc_id, text)
    except PaperlessError as exc:
        log.warning("doc %s: note not added: %s", doc_id, exc)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/hook")
async def hook(
    request: Request,
    background: BackgroundTasks,
    x_paperless_tarmed_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    cfg = Config.from_env()
    # Fail closed: an unset secret must not silently turn auth off. compose.yaml
    # also refuses to deploy without one, but that guard lives outside the code.
    if not cfg.webhook_secret:
        raise HTTPException(status_code=503, detail="PAPERLESS_TARMED_WEBHOOK_SECRET is not set")
    if not hmac.compare_digest(x_paperless_tarmed_secret or "", cfg.webhook_secret):
        raise HTTPException(status_code=401, detail="bad or missing secret")

    # JSON only: a form body would need python-multipart, which is not installed.
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    doc_id = _doc_id_from(payload, request)
    if doc_id is None:
        raise HTTPException(status_code=400, detail="no document id in request")

    background.add_task(process, doc_id, cfg)
    return {"accepted": doc_id}
