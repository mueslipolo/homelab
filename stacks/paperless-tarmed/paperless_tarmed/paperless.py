"""Paperless-ngx REST client.

Only what this project needs. Custom field IDs are resolved by name at run time
rather than configured, because a hardcoded ID silently writes to the wrong field
if the instance is ever rebuilt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from .config import Config
from .normalize import fold

log = logging.getLogger(__name__)

# Writes are retried: re-sending the same title or field values is harmless,
# and Paperless can take over a minute to answer one while it renames files
# (PAPERLESS_FILENAME_FORMAT includes the title) during a large `apply`.
WRITE_ATTEMPTS = 3
WRITE_BACKOFF_SECONDS = 10.0
_sleep = time.sleep  # replaced in tests


@dataclass
class CustomField:
    id: int
    name: str
    data_type: str
    select_options: dict[str, str]  # folded label -> option id, for writing
    select_labels: dict[str, str]  # option id -> original label, for reading

    @property
    def is_select(self) -> bool:
        return self.data_type == "select"


class PaperlessError(RuntimeError):
    pass


class Paperless:
    def __init__(self, cfg: Config):
        cfg.require_api()
        self.cfg = cfg
        self._client = httpx.Client(
            base_url=cfg.paperless_url,
            headers={
                "Authorization": f"Token {cfg.paperless_token}",
                "Accept": "application/json",
            },
            verify=cfg.verify_ssl,
            timeout=cfg.timeout,
            follow_redirects=True,
        )
        self._fields: dict[str, CustomField] | None = None

    def __enter__(self) -> Paperless:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- plumbing

    def _get(self, url: str, **params: Any) -> dict:
        resp = self._client.get(url, params=params or None)
        if resp.status_code >= 400:
            raise PaperlessError(f"GET {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def _paginate(self, url: str, **params: Any) -> Iterator[dict]:
        """Follow Paperless's `next` links rather than assuming a single page."""
        payload = self._get(url, page_size=100, **params)
        while True:
            yield from payload.get("results", [])
            nxt = payload.get("next")
            if not nxt:
                return
            resp = self._client.get(nxt)
            if resp.status_code >= 400:
                raise PaperlessError(f"GET {nxt} -> {resp.status_code}")
            payload = resp.json()

    # ----------------------------------------------------------------- reading

    def custom_fields(self) -> dict[str, CustomField]:
        """All custom fields, keyed by folded name."""
        if self._fields is None:
            fields: dict[str, CustomField] = {}
            for row in self._paginate("/api/custom_fields/"):
                extra = row.get("extra_data") or {}
                options: dict[str, str] = {}
                labels: dict[str, str] = {}
                for opt in extra.get("select_options") or []:
                    # Newer Paperless uses {"id": "...", "label": "..."};
                    # older versions used a bare list of labels.
                    if isinstance(opt, dict):
                        label, option_id = str(opt.get("label", "")), opt.get("id")
                    else:
                        label = option_id = str(opt)
                    options[fold(label)] = option_id
                    # Keep the original spelling: decoding to the folded form
                    # would make every stored value look like a mismatch.
                    labels[str(option_id)] = label
                fields[fold(row["name"])] = CustomField(
                    id=row["id"],
                    name=row["name"],
                    data_type=row.get("data_type", "string"),
                    select_options=options,
                    select_labels=labels,
                )
            self._fields = fields
        return self._fields

    def find_field(self, name: str) -> CustomField:
        fields = self.custom_fields()
        if hit := fields.get(fold(name)):
            return hit
        # Lenient fallback: 'Montant facture' should still find
        # 'Montant total (facture)'.
        wanted = fold(name)
        partial = [f for k, f in fields.items() if wanted in k or k in wanted]
        if len(partial) == 1:
            log.info("custom field %r resolved to %r", name, partial[0].name)
            return partial[0]
        # Near-misses are the common case ('Date de traitement' vs 'Date
        # traitement'), so name the likely candidate instead of only listing all.
        import difflib

        names = sorted(f.name for f in fields.values())
        close = difflib.get_close_matches(fold(name), [fold(n) for n in names], n=2, cutoff=0.6)
        suggestions = [n for n in names if fold(n) in close]
        hint = f" Did you mean {' or '.join(repr(s) for s in suggestions)}?" if suggestions else ""
        raise PaperlessError(
            f"Custom field {name!r} not found (or ambiguous).{hint} "
            f"Available: {', '.join(names)}"
        )

    def document(self, doc_id: int) -> dict:
        """One document's metadata, including its current custom field values."""
        return self._get(f"/api/documents/{doc_id}/")

    def document_types(self) -> list[tuple[int, str, int]]:
        """(id, name, document count) for every document type."""
        return [
            (row["id"], row["name"], row.get("document_count", 0))
            for row in self._paginate("/api/document_types/")
        ]

    def document_type_id(self, value: str | int) -> int | None:
        """Resolve a document type given either its name or its numeric id.

        The UI shows ids, so accepting one saves copying a name that has to
        match exactly.
        """
        text = str(value).strip()
        types = self.document_types()
        if text.isdigit():
            wanted = int(text)
            return wanted if any(dt_id == wanted for dt_id, _, _ in types) else None
        for dt_id, name, _ in types:
            if fold(name) == fold(text):
                return dt_id
        return None

    def tag_id(self, name: str, *, create: bool = False) -> int | None:
        for row in self._paginate("/api/tags/"):
            if fold(row["name"]) == fold(name):
                return row["id"]
        if not create:
            return None
        resp = self._client.post("/api/tags/", json={"name": name})
        if resp.status_code >= 400:
            raise PaperlessError(f"creating tag {name!r} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()["id"]

    def documents(
        self,
        *,
        document_type_id: int | None = None,
        tag_id: int | None = None,
        query: str | None = None,
        ids: list[int] | None = None,
        created_after: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict]:
        params: dict[str, Any] = {"ordering": "-created"}
        if document_type_id is not None:
            params["document_type__id"] = document_type_id
        if tag_id is not None:
            params["tags__id__all"] = tag_id
        if query:
            params["query"] = query
        if ids:
            params["id__in"] = ",".join(str(i) for i in ids)
        if created_after:
            params["created__date__gte"] = created_after

        for n, doc in enumerate(self._paginate("/api/documents/", **params), start=1):
            yield doc
            if limit and n >= limit:
                return

    def download(self, doc_id: int, dest: Path, *, original: bool = False) -> Path:
        """Fetch the archive PDF, which carries the OCR text layer.

        The original is only useful when there is no archive version; for a scan
        it has no text at all.
        """
        params = {"original": "true"} if original else None
        with self._client.stream(
            "GET", f"/api/documents/{doc_id}/download/", params=params
        ) as resp:
            if resp.status_code >= 400:
                raise PaperlessError(f"downloading {doc_id} -> {resp.status_code}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        return dest

    # ----------------------------------------------------------------- writing

    @staticmethod
    def encode_value(field: CustomField, value: Any, currency: str | None = None) -> Any:
        """Shape a Python value the way Paperless stores that field type."""
        if field.is_select:
            option = field.select_options.get(fold(str(value)))
            if option is None:
                raise PaperlessError(
                    f"{value!r} is not an option of select field {field.name!r} "
                    f"(options: {sorted(field.select_options)})"
                )
            return option
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal):
            # Paperless monetary fields are strings carrying the currency, e.g.
            # 'CHF388.80'. Writing a bare number would not match existing rows.
            if field.data_type == "monetary":
                return f"{(currency or 'CHF').upper()}{value:.2f}"
            return str(value)
        return value

    def _write(self, method: str, url: str, payload: dict, what: str) -> None:
        """Send one idempotent write, retrying on a timeout or a server error."""
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            last = attempt == WRITE_ATTEMPTS
            try:
                resp = self._client.request(method, url, json=payload)
            except httpx.TimeoutException as exc:
                if last:
                    raise PaperlessError(
                        f"{what}: no answer within {self.cfg.timeout:.0f}s, {attempt} attempts"
                    ) from exc
                log.warning("%s: timed out (attempt %d), retrying", what, attempt)
            else:
                if resp.status_code < 400:
                    return
                if resp.status_code < 500 or last:
                    raise PaperlessError(f"{what} -> {resp.status_code}: {resp.text[:300]}")
                log.warning("%s: HTTP %d (attempt %d), retrying", what, resp.status_code, attempt)
            _sleep(WRITE_BACKOFF_SECONDS * attempt)

    def set_custom_fields(self, doc_id: int, values: dict[int, Any]) -> None:
        if not values:
            return
        payload = {
            "documents": [doc_id],
            "method": "modify_custom_fields",
            "parameters": {
                "add_custom_fields": {str(k): v for k, v in values.items()},
                "remove_custom_fields": [],
            },
        }
        self._write("POST", "/api/documents/bulk_edit/", payload, f"fields of {doc_id}")

    def add_tag(self, doc_id: int, tag: int) -> None:
        payload = {
            "documents": [doc_id],
            "method": "modify_tags",
            "parameters": {"add_tags": [tag], "remove_tags": []},
        }
        self._write("POST", "/api/documents/bulk_edit/", payload, f"tagging {doc_id}")

    def add_note(self, doc_id: int, text: str) -> None:
        """One attempt only: unlike the writes above, a POST retried after a
        timeout could leave the same note twice."""
        try:
            resp = self._client.post(f"/api/documents/{doc_id}/notes/", json={"note": text})
        except httpx.HTTPError as exc:
            raise PaperlessError(f"note on {doc_id}: {exc}") from exc
        if resp.status_code >= 400:
            raise PaperlessError(f"note on {doc_id} -> {resp.status_code}: {resp.text[:200]}")

    def patch_document(self, doc_id: int, **fields: Any) -> None:
        self._write("PATCH", f"/api/documents/{doc_id}/", fields, f"PATCH document {doc_id}")

