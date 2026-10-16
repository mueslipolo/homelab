"""The write-planning layer shared by `apply` and the webhook.

Both callers used to carry their own copy of the encode loop and the journal
write; the CLI's copy dropped the `source` key without anyone noticing. These
tests pin the shared behaviour directly, and check the CLI actually uses it.

All data here is invented.
"""

from __future__ import annotations

import argparse
import json

from paperless_tarmed.config import Config
from paperless_tarmed.paperless import CustomField
from paperless_tarmed.planning import append_journal, encode_writes

PAGE = """Justificatif de remboursement Release 4.5G/fr
Patient Nom MUSTER Prénom Anna
Traitement 23.07.2026 - 23.07.2026
Fournisseur de prestations Dupont Marie
EAN 7600000000001
Tarif 001
Montant de la facture: 252.00
"""


def _field(fid: int, name: str = "f") -> CustomField:
    return CustomField(id=fid, name=name, data_type="text", select_options={}, select_labels={})


def test_encode_writes_only_encodes_planned_fields() -> None:
    fields = {"patient": _field(2), "total": _field(3)}
    encoded, problems = encode_writes({2: "Anna"}, fields, "CHF", encode=lambda f, v, c: f"{v}!")
    assert encoded == {2: "Anna!"}
    assert problems == []


def test_encode_writes_reports_failures_instead_of_raising() -> None:
    def encode(field, value, currency):
        raise ValueError("not an option")

    encoded, problems = encode_writes({2: "Zed"}, {"patient": _field(2)}, None, encode=encode)
    assert encoded == {}
    assert problems == ["patient: not an option"]


def test_journal_entry_schema(tmp_path) -> None:
    path = tmp_path / "sub" / "journal.jsonl"
    append_journal(path, doc_id=7, fields={2: "Anna"}, title_from="a", title_to="b", source="x")
    (entry,) = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert set(entry) == {"ts", "source", "doc_id", "fields", "title_from", "title_to"}
    assert entry["source"] == "x"
    assert entry["fields"] == {"2": "Anna"}


class _FakeApi:
    def __init__(self):
        self.set_calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def find_field(self, name):
        return _field(2, name)

    @staticmethod
    def encode_value(field, value, currency=None):
        return str(value)

    def set_custom_fields(self, doc_id, values):
        self.set_calls.append(values)

    def patch_document(self, doc_id, **fields):
        raise AssertionError("title not requested")


def test_cli_apply_journals_with_cli_source(monkeypatch, tmp_path) -> None:
    """The drift this module exists to prevent: CLI entries must carry the same
    schema as webhook ones, tagged source=cli."""
    import paperless_tarmed.cli as cli
    import paperless_tarmed.paperless as paperless_module
    from paperless_tarmed.pipeline import extract_from_pages

    api = _FakeApi()
    monkeypatch.setattr(paperless_module, "Paperless", lambda cfg: api)
    fields = {"patient": {"id": 2, "name": "Concernés", "data_type": "text",
                          "select_options": {}, "select_labels": {}}}
    doc = {"id": 1, "title": "scan001", "custom_fields": []}
    monkeypatch.setattr(cli, "_documents_for", lambda api, args, cfg, cache: ([doc], fields))
    monkeypatch.setattr(
        cli, "extract_pdf", lambda path, cfg, doc_id=None: extract_from_pages([PAGE], cfg, doc_id=doc_id)
    )

    cfg = Config(paperless_url="http://x", paperless_token="t",
                 fields={"patient": "Concernés"}, cache_dir=tmp_path)
    journal = tmp_path / "journal.jsonl"
    args = argparse.Namespace(journal=str(journal), tag_failures=False, apply=True,
                              overwrite=False, title=False, verbose=False)
    assert cli.cmd_apply(args, cfg) == 0

    assert api.set_calls == [{2: "Anna"}]
    (entry,) = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert entry["source"] == "cli"
    assert entry["fields"] == {"2": "Anna"}


# --- resilience of large runs -------------------------------------------------


def _paperless_with(handler):
    import httpx

    import paperless_tarmed.paperless as paperless_module

    api = paperless_module.Paperless(Config(paperless_url="http://x", paperless_token="t"))
    api._client = httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler))
    return api


def test_a_timed_out_write_is_retried(monkeypatch) -> None:
    """Paperless can take over a minute to rename a file; a retry must cover it."""
    import httpx

    import paperless_tarmed.paperless as paperless_module

    monkeypatch.setattr(paperless_module, "_sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={})

    _paperless_with(handler).patch_document(7, title="t")
    assert len(calls) == 2


def test_retries_are_bounded(monkeypatch) -> None:
    import httpx
    import pytest

    import paperless_tarmed.paperless as paperless_module

    monkeypatch.setattr(paperless_module, "_sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(paperless_module.PaperlessError, match="no answer"):
        _paperless_with(handler).patch_document(7, title="t")
    assert len(calls) == paperless_module.WRITE_ATTEMPTS


def test_a_client_error_is_not_retried(monkeypatch) -> None:
    import httpx
    import pytest

    import paperless_tarmed.paperless as paperless_module

    monkeypatch.setattr(paperless_module, "_sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="bad")

    with pytest.raises(paperless_module.PaperlessError, match="400"):
        _paperless_with(handler).patch_document(7, title="t")
    assert len(calls) == 1


def test_one_failing_document_does_not_end_the_run(monkeypatch, tmp_path, capsys) -> None:
    """The archive-wide run died on one ReadTimeout and left ~400 documents
    unprocessed. Now the failure is reported and the run carries on."""
    import paperless_tarmed.cli as cli
    import paperless_tarmed.paperless as paperless_module
    from paperless_tarmed.pipeline import extract_from_pages

    class Flaky(_FakeApi):
        def set_custom_fields(self, doc_id, values):
            if doc_id == 1:
                raise paperless_module.PaperlessError("fields of 1: no answer")
            super().set_custom_fields(doc_id, values)

    api = Flaky()
    monkeypatch.setattr(paperless_module, "Paperless", lambda cfg: api)
    fields = {"patient": {"id": 2, "name": "Concernés", "data_type": "text",
                          "select_options": {}, "select_labels": {}}}
    docs = [{"id": i, "title": "scan", "custom_fields": []} for i in (1, 2)]
    monkeypatch.setattr(cli, "_documents_for", lambda api, args, cfg, cache: (docs, fields))
    monkeypatch.setattr(
        cli, "extract_pdf", lambda path, cfg, doc_id=None: extract_from_pages([PAGE], cfg, doc_id=doc_id)
    )
    cfg = Config(paperless_url="http://x", paperless_token="t",
                 fields={"patient": "Concernés"}, cache_dir=tmp_path)
    journal = tmp_path / "journal.jsonl"
    args = argparse.Namespace(journal=str(journal), tag_failures=False, apply=True,
                              overwrite=False, title=False, verbose=False)

    assert cli.cmd_apply(args, cfg) == 1, "a failed write must show in the exit code"
    assert api.set_calls == [{2: "Anna"}], "document 2 is still written"
    out = capsys.readouterr().out
    assert "1 write(s) FAILED" in out and "#1:" in out
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [e["doc_id"] for e in entries] == [2], "only what landed is journalled"


# --- tag and note once a document is processed ----------------------------------

from datetime import datetime  # noqa: E402

from paperless_tarmed.planning import outcome_marks  # noqa: E402

WHEN = datetime(2026, 10, 12, 14, 3)


def _marks(doc=None, **kw):
    args = dict(failed=False, found=True, reasons=[], written=[], title=None,
                review_tag=7, done_tag=8, when=WHEN)
    args.update(kw)
    return outcome_marks(doc or {"tags": []}, **args)


def test_a_failure_gets_the_review_tag_and_a_dated_note_with_its_reasons() -> None:
    tag, note = _marks(failed=True, reasons=["total: not found", "provider name not found"])
    assert tag == 7
    assert note == ("paperless-tarmed, 2026-10-12 14:03: extraction failed\n"
                    "- total: not found\n- provider name not found")


def test_a_document_that_keeps_failing_gets_one_note_not_one_per_run() -> None:
    assert _marks({"tags": [7]}, failed=True, reasons=["x"]) == (None, None)


def test_a_success_gets_the_done_tag_and_a_note_of_what_was_written() -> None:
    tag, note = _marks(written=["patient=Anna", "total=113.00"], title="Anna - Physio")
    assert tag == 8
    assert note == ("paperless-tarmed, 2026-10-12 14:03: read and written\n"
                    "- patient=Anna\n- total=113.00\n- title: Anna - Physio")


def test_a_success_with_nothing_to_write_is_tagged_without_a_note() -> None:
    """Re-running over the archive marks old documents as processed, quietly."""
    assert _marks() == (8, None)


def test_a_rerun_that_changes_nothing_leaves_no_trace() -> None:
    assert _marks({"tags": [8]}) == (None, None)


def test_a_document_that_is_no_justificatif_is_left_alone() -> None:
    assert _marks(found=False) == (None, None)


def test_without_tags_configured_only_the_success_note_remains() -> None:
    assert _marks(failed=True, reasons=["x"], review_tag=None) == (None, None)
    assert _marks(written=["patient=Anna"], done_tag=None)[0] is None
